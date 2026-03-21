"""Integration test infrastructure: daemon lifecycle via pytest hooks.

Starts the local APME daemon (Primary + Native + OPA + Ansible + Cache)
when integration-marked tests are collected, and tears it down in
``pytest_sessionfinish``.  Follows the ansible-dev-tools pattern of
hook-based lifecycle management rather than session-scoped fixtures.

Run with::

    pytest -m integration tests/integration/ -v
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    pass

LOGGER = logging.getLogger(__name__)

_ENV_KEYS = (
    "APME_DATA_DIR",
    "APME_PRIMARY_ADDRESS",
    "OPA_USE_PODMAN",
)


@dataclass
class Infrastructure:
    """Holds daemon state and original env for restoration on teardown.

    Attributes:
        primary_address: gRPC address of the Primary service.
        data_dir: Temporary directory used for daemon state isolation.
        original_env: Snapshot of env vars before daemon start.
    """

    primary_address: str = ""
    data_dir: str = ""
    original_env: dict[str, str | None] = field(default_factory=dict)


INFRASTRUCTURE: Infrastructure | None = None


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Start daemon if integration-marked tests will actually run.

    This hook fires *before* pytest applies ``-m`` deselection, so we must
    check ``markexpr`` ourselves to avoid starting the daemon when the
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
        _start_daemon()


def pytest_sessionfinish(session: pytest.Session) -> None:
    """Stop the daemon and restore environment.

    Args:
        session: Pytest session (unused beyond signature).
    """
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return
    _stop_daemon()


def _start_daemon() -> None:
    """Fork a local daemon with all validators and configure env for CLI."""
    global INFRASTRUCTURE  # noqa: PLW0603

    from apme_engine.daemon.launcher import start_daemon

    original_env = {k: os.environ.get(k) for k in _ENV_KEYS}
    data_dir = tempfile.mkdtemp(prefix="apme-integration-")

    os.environ["APME_DATA_DIR"] = data_dir
    os.environ["OPA_USE_PODMAN"] = "0"

    LOGGER.warning("Starting APME daemon (data_dir=%s)", data_dir)

    try:
        state = start_daemon(include_optional=True)
    except RuntimeError as exc:
        _restore_env(original_env)
        pytest.exit(f"Failed to start daemon: {exc}", returncode=2)
        return  # unreachable, keeps mypy happy

    os.environ["APME_PRIMARY_ADDRESS"] = state.primary
    LOGGER.warning("APME daemon ready at %s (pid %d)", state.primary, state.pid)

    INFRASTRUCTURE = Infrastructure(
        primary_address=state.primary,
        data_dir=data_dir,
        original_env=original_env,
    )


def _stop_daemon() -> None:
    """Stop the daemon and restore saved environment variables."""
    global INFRASTRUCTURE  # noqa: PLW0603

    if INFRASTRUCTURE is None:
        return

    from apme_engine.daemon.launcher import stop_daemon

    LOGGER.warning("Stopping APME daemon")
    try:
        stop_daemon()
    except Exception:
        LOGGER.exception("Error stopping daemon")

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
