"""Integration test infrastructure: daemon + gateway + galaxy proxy lifecycle.

Starts a galaxy proxy (for collection installs), the reporting gateway
(gRPC + REST), and the local APME daemon (Primary + Native + OPA + Ansible)
when integration-marked tests are collected, and tears all down in
``pytest_sessionfinish``.

Run with::

    pytest -m integration tests/integration/ -v
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

LOGGER = logging.getLogger(__name__)

_ENV_KEYS = (
    "APME_DATA_DIR",
    "APME_PRIMARY_ADDRESS",
    "APME_GALAXY_PROXY_URL",
    "APME_REPORTING_ENDPOINT",
    "APME_DB_PATH",
    "OPA_USE_PODMAN",
)


@dataclass
class Infrastructure:
    """Holds daemon, proxy, and gateway state for restoration on teardown.

    Attributes:
        primary_address: gRPC address of the Primary service.
        data_dir: Temporary directory used for daemon state isolation.
        proxy_process: Galaxy proxy subprocess (terminated on teardown).
        gateway_process: Gateway subprocess (terminated on teardown).
        gateway_db_path: Path to the gateway SQLite database file.
        gateway_http_url: Base URL for the gateway REST API.
        original_env: Snapshot of env vars before daemon start.
    """

    primary_address: str = ""
    data_dir: str = ""
    proxy_process: subprocess.Popen[bytes] | None = None
    gateway_process: subprocess.Popen[bytes] | None = None
    gateway_db_path: str = ""
    gateway_http_url: str = ""
    original_env: dict[str, str | None] = field(default_factory=dict)


INFRASTRUCTURE: Infrastructure | None = None


def _free_port() -> int:
    """Find a free TCP port on localhost.

    Returns:
        Available port number.
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for_port(port: int, timeout: float = 15.0) -> bool:
    """Block until a TCP port is accepting connections.

    Args:
        port: Port number to probe.
        timeout: Maximum seconds to wait.

    Returns:
        True if port became reachable, False on timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Start proxy + gateway + daemon if integration-marked tests will actually run.

    This hook fires *before* pytest applies ``-m`` deselection, so we must
    check ``markexpr`` ourselves to avoid starting infrastructure when the
    session runs with ``-m 'not integration'`` (the default addopts).

    Args:
        config: Pytest config object.
        items: Collected test items (before marker-based deselection).
    """
    if config.option.collectonly:
        return
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return
    markexpr = getattr(config.option, "markexpr", "") or ""
    if "not integration" in markexpr:
        return
    has_integration = any(item.get_closest_marker("integration") for item in items)
    if has_integration and INFRASTRUCTURE is None:
        _start_infrastructure()


def pytest_sessionfinish(session: pytest.Session) -> None:
    """Stop daemon, gateway, and proxy, restore environment.

    Args:
        session: Pytest session (unused beyond signature).
    """
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return
    _stop_infrastructure()


def _start_infrastructure() -> None:
    """Start galaxy proxy, gateway, then fork the daemon with all validators."""
    global INFRASTRUCTURE  # noqa: PLW0603

    from apme_engine.daemon.launcher import start_daemon

    original_env = {k: os.environ.get(k) for k in _ENV_KEYS}
    data_dir = tempfile.mkdtemp(prefix="apme-integration-")

    proxy_port = _free_port()
    proxy_stderr = Path(data_dir) / "proxy_stderr.log"
    proxy_stderr_fh = open(proxy_stderr, "w")  # noqa: SIM115
    proxy_proc = subprocess.Popen(
        [sys.executable, "-m", "galaxy_proxy.cli", "--host", "127.0.0.1", "--port", str(proxy_port)],
        stdout=subprocess.DEVNULL,
        stderr=proxy_stderr_fh,
    )
    if not _wait_for_port(proxy_port):
        proxy_proc.terminate()
        proxy_proc.wait(timeout=5)
        proxy_stderr_fh.close()
        pytest.exit(
            f"Galaxy proxy did not start on port {proxy_port}: {proxy_stderr.read_text()[:2000]}",
            returncode=2,
        )
        return

    proxy_url = f"http://127.0.0.1:{proxy_port}"
    LOGGER.warning("Galaxy proxy ready at %s (pid %d)", proxy_url, proxy_proc.pid)

    os.environ["APME_DATA_DIR"] = data_dir
    os.environ["APME_GALAXY_PROXY_URL"] = proxy_url
    os.environ["OPA_USE_PODMAN"] = "0"

    # --- Gateway (gRPC reporting + REST API) ---
    gateway_grpc_port = _free_port()
    gateway_http_port = _free_port()
    gateway_db_path = str(Path(data_dir) / "gateway.db")
    gateway_env = {
        **os.environ,
        "APME_DB_PATH": gateway_db_path,
        "APME_GATEWAY_GRPC_LISTEN": f"127.0.0.1:{gateway_grpc_port}",
        "APME_GATEWAY_HTTP_HOST": "127.0.0.1",
        "APME_GATEWAY_HTTP_PORT": str(gateway_http_port),
    }
    gateway_stderr = Path(data_dir) / "gateway_stderr.log"
    gateway_stderr_fh = open(gateway_stderr, "w")  # noqa: SIM115
    gateway_proc = subprocess.Popen(
        [sys.executable, "-m", "apme_gateway.main"],
        stdout=subprocess.DEVNULL,
        stderr=gateway_stderr_fh,
        env=gateway_env,
    )
    if not _wait_for_port(gateway_grpc_port):
        gateway_proc.terminate()
        gateway_proc.wait(timeout=5)
        gateway_stderr_fh.close()
        proxy_proc.terminate()
        proxy_proc.wait(timeout=5)
        _restore_env(original_env)
        pytest.exit(
            f"Gateway did not start on port {gateway_grpc_port}: {gateway_stderr.read_text()[:2000]}",
            returncode=2,
        )
        return

    LOGGER.warning(
        "Gateway ready gRPC=%d HTTP=%d DB=%s (pid %d)",
        gateway_grpc_port,
        gateway_http_port,
        gateway_db_path,
        gateway_proc.pid,
    )

    os.environ["APME_REPORTING_ENDPOINT"] = f"127.0.0.1:{gateway_grpc_port}"
    os.environ["APME_DB_PATH"] = gateway_db_path

    LOGGER.warning("Starting APME daemon (data_dir=%s)", data_dir)

    try:
        state = start_daemon(include_optional=True)
    except RuntimeError as exc:
        gateway_proc.terminate()
        gateway_proc.wait(timeout=5)
        proxy_proc.terminate()
        proxy_proc.wait(timeout=5)
        _restore_env(original_env)
        pytest.exit(f"Failed to start daemon: {exc}", returncode=2)
        return

    os.environ["APME_PRIMARY_ADDRESS"] = state.primary
    LOGGER.warning("APME daemon ready at %s (pid %d)", state.primary, state.pid)

    INFRASTRUCTURE = Infrastructure(
        primary_address=state.primary,
        data_dir=data_dir,
        proxy_process=proxy_proc,
        gateway_process=gateway_proc,
        gateway_db_path=gateway_db_path,
        gateway_http_url=f"http://127.0.0.1:{gateway_http_port}",
        original_env=original_env,
    )


def _stop_infrastructure() -> None:
    """Stop daemon, gateway, and proxy, restore saved environment variables."""
    global INFRASTRUCTURE  # noqa: PLW0603

    if INFRASTRUCTURE is None:
        return

    from apme_engine.daemon.launcher import stop_daemon

    LOGGER.warning("Stopping APME daemon")
    try:
        stop_daemon()
    except Exception:
        LOGGER.exception("Error stopping daemon")

    if INFRASTRUCTURE.gateway_process is not None:
        LOGGER.warning("Stopping gateway")
        INFRASTRUCTURE.gateway_process.terminate()
        try:
            INFRASTRUCTURE.gateway_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            INFRASTRUCTURE.gateway_process.kill()
            INFRASTRUCTURE.gateway_process.wait()

    if INFRASTRUCTURE.proxy_process is not None:
        LOGGER.warning("Stopping galaxy proxy")
        INFRASTRUCTURE.proxy_process.terminate()
        try:
            INFRASTRUCTURE.proxy_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            INFRASTRUCTURE.proxy_process.kill()
            INFRASTRUCTURE.proxy_process.wait()

    _restore_env(INFRASTRUCTURE.original_env)
    INFRASTRUCTURE = None


def _restore_env(saved: dict[str, str | None]) -> None:
    """Restore environment variables from a saved snapshot.

    Args:
        saved: Map of env var name to original value (None means unset).
    """
    for key, val in saved.items():
        if val is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = val


@pytest.fixture(scope="session")  # type: ignore[untyped-decorator]
def infrastructure() -> Infrastructure:
    """Provide the daemon infrastructure to tests.

    Returns:
        Infrastructure dataclass with daemon addresses and state.
    """
    assert INFRASTRUCTURE is not None, "Daemon not started. Run with: pytest -m integration"
    return INFRASTRUCTURE


FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "terrible-playbook"


@pytest.fixture(scope="session")  # type: ignore[untyped-decorator]
def fixture_dir() -> Path:
    """Path to the terrible-playbook fixture directory.

    Returns:
        Resolved Path to the fixture.
    """
    return FIXTURE_DIR
