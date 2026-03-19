"""Local daemon launcher: start/stop/manage APME services on localhost.

Provides standalone users the same gRPC architecture as the Podman pod
without requiring containers.  The daemon runs Primary + validators as
localhost gRPC servers in a single background process.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from importlib.metadata import version as pkg_version
from pathlib import Path

_DATA_DIR = Path(os.environ.get("APME_DATA_DIR", "~/.apme-data")).expanduser()
_STATE_FILE = _DATA_DIR / "daemon.json"

_DEFAULT_PORTS = {
    "primary": 50051,
    "cache": 50052,
    "native": 50055,
    "opa": 50054,
}

_OPTIONAL_SERVICES = {
    "ansible": 50053,
    "gitleaks": 50056,
}

_HEALTH_TIMEOUT = 10.0
_HEALTH_POLL_INTERVAL = 0.3


@dataclass
class DaemonState:
    """Persisted daemon state (written to daemon.json).

    Attributes:
        pid: Process ID of the daemon.
        primary: Primary service gRPC address.
        version: APME engine version at start time.
        started_at: ISO-format timestamp of daemon start.
        services: Map of service name to gRPC address.
    """

    pid: int
    primary: str
    version: str
    started_at: str
    services: dict[str, str] = field(default_factory=dict)

    def save(self) -> None:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(asdict(self), indent=2) + "\n")

    @classmethod
    def load(cls) -> DaemonState | None:
        if not _STATE_FILE.exists():
            return None
        try:
            data = json.loads(_STATE_FILE.read_text())
            return cls(**data)
        except (json.JSONDecodeError, TypeError, KeyError):
            return None

    @staticmethod
    def remove() -> None:
        with __import__("contextlib").suppress(FileNotFoundError):
            _STATE_FILE.unlink()


def _current_version() -> str:
    try:
        return pkg_version("apme-engine")
    except Exception:
        return "0.0.0-dev"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _health_check(address: str, timeout: float = 3.0) -> bool:
    """Synchronous gRPC health check against a running service.

    Args:
        address: gRPC address to probe.
        timeout: Seconds to wait before giving up.

    Returns:
        True if the service responds with status "ok".
    """
    import grpc

    from apme.v1 import primary_pb2_grpc
    from apme.v1.common_pb2 import HealthRequest

    try:
        channel = grpc.insecure_channel(address)
        stub = primary_pb2_grpc.PrimaryStub(channel)  # type: ignore[no-untyped-call]
        resp = stub.Health(HealthRequest(), timeout=timeout)
        channel.close()
        return bool(resp.status == "ok")
    except Exception:
        return False


async def _run_daemon(services: dict[str, str]) -> None:
    """Run all daemon services in a single event loop (blocks forever).

    Args:
        services: Map of service name -> listen address.
    """
    from apme_engine.daemon.primary_server import serve as primary_serve

    servers = []

    # Set validator env vars so Primary knows where to fan out
    env_map = {
        "native": "NATIVE_GRPC_ADDRESS",
        "opa": "OPA_GRPC_ADDRESS",
        "ansible": "ANSIBLE_GRPC_ADDRESS",
        "gitleaks": "GITLEAKS_GRPC_ADDRESS",
    }
    for name, env_var in env_map.items():
        if name in services:
            os.environ[env_var] = services[name]

    if "cache" in services:
        os.environ["APME_CACHE_GRPC_ADDRESS"] = services["cache"]

    # Start async validators
    if "native" in services:
        from apme_engine.daemon.native_validator_server import serve as native_serve

        servers.append(await native_serve(services["native"]))
        sys.stderr.write(f"  Native validator on {services['native']}\n")

    if "opa" in services:
        from apme_engine.daemon.opa_validator_server import serve as opa_serve

        servers.append(await opa_serve(services["opa"]))
        sys.stderr.write(f"  OPA validator on {services['opa']}\n")

    if "ansible" in services:
        from apme_engine.daemon.ansible_validator_server import serve as ansible_serve

        servers.append(await ansible_serve(services["ansible"]))
        sys.stderr.write(f"  Ansible validator on {services['ansible']}\n")

    if "gitleaks" in services:
        from apme_engine.daemon.gitleaks_validator_server import serve as gitleaks_serve

        servers.append(await gitleaks_serve(services["gitleaks"]))
        sys.stderr.write(f"  Gitleaks validator on {services['gitleaks']}\n")

    # Start sync cache in a thread
    if "cache" in services:
        from apme_engine.daemon.cache_maintainer_server import serve as cache_serve

        cache_server = cache_serve(services["cache"])
        cache_server.start()
        sys.stderr.write(f"  Cache maintainer on {services['cache']}\n")

    # Start Primary last (depends on validators being up)
    primary = await primary_serve(services["primary"])
    servers.append(primary)
    sys.stderr.write(f"  Primary on {services['primary']}\n")
    sys.stderr.flush()

    # Wait until terminated
    await primary.wait_for_termination()


def start_daemon(
    *,
    include_optional: bool = False,
    host: str = "127.0.0.1",
) -> DaemonState:
    """Fork a background daemon process running Primary + core validators.

    Args:
        include_optional: Also start Ansible and Gitleaks validators.
        host: Bind address (default 127.0.0.1 for localhost-only).

    Returns:
        DaemonState with PID and addresses.

    Raises:
        RuntimeError: If daemon fails to become healthy.
    """
    services: dict[str, str] = {}
    for name, port in _DEFAULT_PORTS.items():
        services[name] = f"{host}:{port}"
    if include_optional:
        for name, port in _OPTIONAL_SERVICES.items():
            services[name] = f"{host}:{port}"

    pid = os.fork()
    if pid == 0:
        # Child: detach and run services
        os.setsid()
        # Redirect stdout/stderr to log file
        log_path = _DATA_DIR / "daemon.log"
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        log_fd = open(log_path, "a")  # noqa: SIM115
        os.dup2(log_fd.fileno(), sys.stdout.fileno())
        os.dup2(log_fd.fileno(), sys.stderr.fileno())

        sys.stderr.write(f"\n--- daemon start {datetime.now(timezone.utc).isoformat()} ---\n")
        sys.stderr.flush()

        try:
            asyncio.run(_run_daemon(services))
        except KeyboardInterrupt:
            pass
        except Exception as e:
            sys.stderr.write(f"Daemon crashed: {e}\n")
            import traceback

            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
        finally:
            os._exit(0)

    # Parent: write state and wait for health
    state = DaemonState(
        pid=pid,
        primary=services["primary"],
        version=_current_version(),
        started_at=datetime.now(timezone.utc).isoformat(),
        services=services,
    )
    state.save()

    # Poll until Primary is healthy
    deadline = time.monotonic() + _HEALTH_TIMEOUT
    while time.monotonic() < deadline:
        if _health_check(state.primary, timeout=1.0):
            return state
        if not _pid_alive(pid):
            DaemonState.remove()
            msg = "Daemon process exited before becoming healthy"
            raise RuntimeError(msg)
        time.sleep(_HEALTH_POLL_INTERVAL)

    # Timed out — kill the child and clean up
    stop_daemon()
    msg = f"Daemon did not become healthy within {_HEALTH_TIMEOUT}s"
    raise RuntimeError(msg)


def stop_daemon() -> bool:
    """Stop a running daemon.

    Returns:
        True if a daemon was stopped, False if none was running.
    """
    state = DaemonState.load()
    if state is None:
        return False

    if _pid_alive(state.pid):
        try:
            os.kill(state.pid, signal.SIGTERM)
            # Wait briefly for clean shutdown
            for _ in range(20):
                time.sleep(0.1)
                if not _pid_alive(state.pid):
                    break
            else:
                os.kill(state.pid, signal.SIGKILL)
        except OSError:
            pass

    DaemonState.remove()
    return True


def daemon_status() -> DaemonState | None:
    """Check daemon status.

    Returns:
        DaemonState if running, None otherwise.
    """
    state = DaemonState.load()
    if state is None:
        return None
    if not _pid_alive(state.pid):
        DaemonState.remove()
        return None
    return state


def ensure_daemon() -> str:
    """Ensure a daemon is running and return the Primary address.

    Discovery order:
    1. APME_PRIMARY_ADDRESS env var (explicit, wins always)
    2. daemon.json exists and PID is alive
    3. Auto-start daemon

    Restarts on version mismatch.  Delegates to ``start_daemon()``
    which raises ``RuntimeError`` if the daemon fails to start.

    Returns:
        Primary gRPC address (e.g. "127.0.0.1:50051").
    """
    # 1. Explicit env var
    addr = os.environ.get("APME_PRIMARY_ADDRESS")
    if addr:
        return addr

    # 2. Existing daemon
    state = daemon_status()
    if state is not None:
        current = _current_version()
        if state.version != current:
            sys.stderr.write(
                f"Daemon version {state.version} != installed {current}, restarting...\n"
            )
            sys.stderr.flush()
            stop_daemon()
        else:
            return state.primary

    # 3. Auto-start
    sys.stderr.write("Starting APME daemon...\n")
    sys.stderr.flush()
    state = start_daemon()
    sys.stderr.write(f"Daemon ready on {state.primary} (pid {state.pid})\n")
    sys.stderr.flush()
    return state.primary
