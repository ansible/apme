"""Local daemon launcher: start/stop/manage APME services on localhost.

Provides standalone users the same gRPC architecture as the Podman pod
without requiring containers.  The daemon runs Engine + validators as
localhost gRPC servers in a single background process.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
import signal
import socket
import sys
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from importlib.metadata import version as pkg_version
from pathlib import Path

_DATA_DIR = Path(os.environ.get("APME_DATA_DIR", "~/.apme-data")).expanduser()
_STATE_FILE = _DATA_DIR / "daemon.json"
_MARKER_FILE = _DATA_DIR / "daemon.marker"

_DEFAULT_PORTS = {
    "engine": 50051,
    "native": 50055,
    "opa": 50054,
    "ansible": 50053,
    "galaxy_proxy": 8765,
}

_OPTIONAL_SERVICES = {
    "gitleaks": 50056,
    "collection_health": 50058,
    "dep_audit": 50059,
}

_HEALTH_TIMEOUT = 10.0
_HEALTH_POLL_INTERVAL = 0.3


@contextlib.contextmanager
def _daemon_lifecycle_lock() -> Iterator[None]:
    """Exclusive lock for daemon start/stop/status to prevent concurrent races.

    Yields:
        None: Exclusive lock hold for the caller.
    """
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock_file = _DATA_DIR / "daemon.lock"
    fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@dataclass
class DaemonState:
    """Persisted daemon state (written to daemon.json).

    Attributes:
        pid: Process ID of the daemon.
        engine: Engine service gRPC address.
        version: APME engine version at start time.
        started_at: ISO-format timestamp of daemon start.
        services: Map of service name to gRPC address.
    """

    pid: int
    engine: str
    version: str
    started_at: str
    services: dict[str, str] = field(default_factory=dict)

    def save(self) -> None:
        """Persist daemon state to disk."""
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(asdict(self), indent=2) + "\n")

    @classmethod
    def load(cls) -> DaemonState | None:
        """Load daemon state from disk, or None if absent/corrupt.

        Returns:
            Loaded DaemonState or None.
        """
        if not _STATE_FILE.exists():
            return None
        try:
            data = json.loads(_STATE_FILE.read_text())
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        engine_addr = data.get("engine")
        if engine_addr is None:
            return None
        try:
            return cls(
                pid=int(data["pid"]),
                engine=str(engine_addr),
                version=str(data.get("version", "0.0.0-dev")),
                started_at=str(data["started_at"]),
                services=dict(data.get("services") or {}),
            )
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def remove() -> None:
        """Delete the persisted daemon state file."""
        with contextlib.suppress(FileNotFoundError):
            _STATE_FILE.unlink()
        _remove_daemon_marker()


def _require_proc_identity() -> None:
    """Raise when ``/proc`` starttime identity checks are unavailable.

    Daemon start and stop both require a verifiable process identity. Writing a
    PID-only marker would leave ``stop_daemon()`` unable to signal the process.

    Raises:
        RuntimeError: When ``/proc/<pid>/stat`` starttime cannot be read.
    """
    if _proc_starttime(os.getpid()) is None:
        msg = (
            "Daemon requires /proc for process identity verification "
            "(cannot read starttime). Refusing to start without a stop-safe marker."
        )
        raise RuntimeError(msg)


def _write_daemon_marker(pid: int) -> None:
    """Record the daemon PID and process start time for ownership checks.

    Args:
        pid: Process ID of the running daemon child.

    Raises:
        RuntimeError: When ``/proc`` identity is unavailable.
    """
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    starttime = _proc_starttime(pid)
    if starttime is None:
        msg = f"Cannot write a stop-safe daemon marker: /proc starttime unavailable for pid {pid}."
        raise RuntimeError(msg)
    _MARKER_FILE.write_text(f"{pid}\n{starttime}\n")


def _remove_daemon_marker() -> None:
    with contextlib.suppress(FileNotFoundError):
        _MARKER_FILE.unlink()


def _proc_starttime(pid: int) -> int | None:
    """Return the kernel starttime tick count for *pid*, or None if unavailable.

    Args:
        pid: Process ID to inspect via ``/proc/<pid>/stat``.

    Returns:
        Field 22 (starttime) from ``/proc/<pid>/stat``, or None on error.
    """
    try:
        data = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    # ``comm`` is wrapped in parentheses and may contain spaces or ``)``.
    rparen = data.rfind(")")
    if rparen < 0:
        return None
    fields = data[rparen + 2 :].split()
    try:
        # After pid + comm, remaining fields are numbered from 3; starttime is 22.
        return int(fields[19])
    except (IndexError, ValueError):
        return None


def _verify_daemon_ownership(pid: int) -> bool:
    """Return True when the marker file identifies *pid* as the local daemon.

    Compares both PID and ``/proc`` starttime so a recycled PID cannot pass
    ownership checks after an abnormal shutdown.

    Args:
        pid: Process ID from daemon.json.

    Returns:
        True when the marker matches the live process identity.
    """
    if not _MARKER_FILE.exists():
        return False
    try:
        lines = _MARKER_FILE.read_text().splitlines()
        marker_pid = int(lines[0].strip())
    except (OSError, ValueError, IndexError):
        return False
    if marker_pid != pid or not _pid_alive(pid):
        return False
    if len(lines) < 2:
        # Incomplete markers (PID only) are not trusted for stop signaling.
        return False
    try:
        marker_start = int(lines[1].strip())
    except ValueError:
        return False
    live_start = _proc_starttime(pid)
    return live_start is not None and live_start == marker_start


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

    from apme.v1 import engine_pb2_grpc
    from apme.v1.common_pb2 import HealthRequest

    channel = None
    try:
        channel = grpc.insecure_channel(address)
        stub = engine_pb2_grpc.EngineStub(channel)  # type: ignore[no-untyped-call]
        resp = stub.Health(HealthRequest(), timeout=timeout)
        return bool(resp.status == "ok")
    except Exception:  # noqa: BLE001 - health probe must degrade to False
        return False
    finally:
        if channel is not None:
            channel.close()


def _check_port_available(host: str, port: int) -> bool:
    """Return True if *port* on *host* is free (bind succeeds).

    Uses ``bind()`` instead of ``connect()`` so the check works for
    non-loopback addresses like ``0.0.0.0`` and avoids socket leaks.

    Args:
        host: Host to probe.
        port: TCP port number.

    Returns:
        True when the port is available (bind succeeds).
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
        else:
            return True


def _address_port_bound(address: str) -> bool:
    """Return True when the host:port in *address* is already bound.

    Args:
        address: gRPC-style ``host:port`` string from daemon state.

    Returns:
        True when the port is in use (or the address cannot be parsed).
    """
    host, _, port_s = address.rpartition(":")
    if not host or not port_s.isdigit():
        return True
    # Bind checks against 127.0.0.1 / 0.0.0.0 — normalize wildcard hosts.
    probe_host = "127.0.0.1" if host in {"0.0.0.0", "::", "[::]"} else host
    return not _check_port_available(probe_host, int(port_s))


def _assert_ports_free(host: str, ports: dict[str, int]) -> None:
    """Raise RuntimeError if any port in *ports* is already bound.

    Args:
        host: Host to probe.
        ports: Map of service name to port number.

    Raises:
        RuntimeError: When a port is already in use.
    """
    for name, port in ports.items():
        if not _check_port_available(host, port):
            msg = (
                f"Port {port} ({name}) is already in use — is a Podman pod or "
                f"another daemon running? Set APME_ENGINE_ADDRESS to connect "
                f"to an existing service instead of starting a new daemon."
            )
            raise RuntimeError(msg)


async def _run_daemon(services: dict[str, str]) -> None:
    """Run all daemon services in a single event loop (blocks forever).

    Args:
        services: Map of service name -> listen address.
    """
    from apme_engine.log_bridge import install_handler

    install_handler()

    from apme_engine.daemon.engine_server import serve as engine_serve

    servers = []

    # Set validator env vars so Engine knows where to fan out
    env_map = {
        "native": "NATIVE_GRPC_ADDRESS",
        "opa": "OPA_GRPC_ADDRESS",
        "ansible": "ANSIBLE_GRPC_ADDRESS",
        "gitleaks": "GITLEAKS_GRPC_ADDRESS",
        "collection_health": "COLLECTION_HEALTH_GRPC_ADDRESS",
        "dep_audit": "DEP_AUDIT_GRPC_ADDRESS",
    }
    for name, env_var in env_map.items():
        if name in services:
            os.environ[env_var] = services[name]

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

    if "collection_health" in services:
        from apme_engine.daemon.collection_health_server import serve as collection_health_serve

        servers.append(await collection_health_serve(services["collection_health"]))
        sys.stderr.write(f"  Collection Health validator on {services['collection_health']}\n")

    if "dep_audit" in services:
        from apme_engine.daemon.dep_audit_server import serve as dep_audit_serve

        servers.append(await dep_audit_serve(services["dep_audit"]))
        sys.stderr.write(f"  Dep Audit validator on {services['dep_audit']}\n")

    # Galaxy Proxy (uvicorn, not gRPC) — must start before Engine so
    # APME_GALAXY_PROXY_URL is set when the engine creates session venvs.
    if "galaxy_proxy" in services:
        proxy_addr = services["galaxy_proxy"]
        proxy_host, _, proxy_port_s = proxy_addr.rpartition(":")
        proxy_url = f"http://{proxy_addr}"
        os.environ["APME_GALAXY_PROXY_URL"] = proxy_url

        import uvicorn  # noqa: PLC0415

        from galaxy_proxy.proxy.server import create_app  # noqa: PLC0415

        proxy_app = create_app()
        config = uvicorn.Config(
            proxy_app,
            host=proxy_host or "127.0.0.1",
            port=int(proxy_port_s),
            log_level="warning",
        )
        proxy_server = uvicorn.Server(config)
        asyncio.create_task(proxy_server.serve())
        sys.stderr.write(f"  Galaxy Proxy on {proxy_url}\n")

    # Start Engine last (depends on validators being up)
    engine_server = await engine_serve(services["engine"])
    servers.append(engine_server)
    sys.stderr.write(f"  Engine on {services['engine']}\n")
    sys.stderr.flush()

    # Wait until terminated
    await engine_server.wait_for_termination()


def start_daemon(
    *,
    include_optional: bool = False,
    host: str = "127.0.0.1",
) -> DaemonState:
    """Fork a background daemon process running Engine + all validators.

    Args:
        include_optional: Also start optional validators (Gitleaks, Collection Health, Dep Audit).
        host: Bind address (default 127.0.0.1 for localhost-only).

    Returns:
        DaemonState with PID and addresses.
    """
    with _daemon_lifecycle_lock():
        return _start_daemon_unlocked(include_optional=include_optional, host=host)


def _start_daemon_unlocked(
    *,
    include_optional: bool = False,
    host: str = "127.0.0.1",
) -> DaemonState:
    """Start daemon without acquiring the lifecycle lock (caller must hold it).

    Args:
        include_optional: Also start optional validators (Gitleaks, Collection Health, Dep Audit).
        host: Bind address (default 127.0.0.1 for localhost-only).

    Returns:
        DaemonState with PID and addresses.

    Raises:
        RuntimeError: If daemon fails to become healthy, or ``/proc`` identity
            checks are unavailable.
        OSError: If daemon state cannot be persisted after the child is forked;
            the child is terminated and the ownership marker is removed first.
    """
    _require_proc_identity()

    existing = _daemon_status_unlocked()
    if existing is not None and _pid_alive(existing.pid) and _health_check(existing.engine, timeout=1.0):
        return existing
    if existing is not None:
        _stop_daemon_unlocked()

    services: dict[str, str] = {}
    all_ports = dict(_DEFAULT_PORTS)
    if include_optional:
        all_ports.update(_OPTIONAL_SERVICES)

    _assert_ports_free(host, all_ports)

    for name, port in all_ports.items():
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

        sys.stderr.write(f"\n--- daemon start {datetime.now(UTC).isoformat()} ---\n")
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

    # Parent: publish ownership marker before daemon.json so stop_daemon can
    # always verify the child (race if state is visible without a marker).
    try:
        _write_daemon_marker(pid)
    except (RuntimeError, OSError):
        with contextlib.suppress(ProcessLookupError, OSError):
            os.kill(pid, signal.SIGTERM)
        with contextlib.suppress(ChildProcessError, OSError):
            os.waitpid(pid, 0)
        raise

    state = DaemonState(
        pid=pid,
        engine=services["engine"],
        version=_current_version(),
        started_at=datetime.now(UTC).isoformat(),
        services=services,
    )
    try:
        state.save()
    except OSError:
        with contextlib.suppress(ProcessLookupError, OSError):
            os.kill(pid, signal.SIGTERM)
        with contextlib.suppress(ChildProcessError, OSError):
            os.waitpid(pid, 0)
        _remove_daemon_marker()
        raise

    # Poll until Engine, required validators, and Galaxy Proxy are healthy
    from apme_engine.daemon.health_check import run_health_checks

    deadline = time.monotonic() + _HEALTH_TIMEOUT
    while time.monotonic() < deadline:
        results = run_health_checks(state.engine, timeout=1.0)
        required_ok = all(results[name].get("ok") for name in ("engine", "native", "opa", "ansible", "galaxy_proxy"))
        if required_ok:
            return state
        if not _pid_alive(pid):
            DaemonState.remove()
            msg = "Daemon process exited before becoming healthy"
            raise RuntimeError(msg)
        time.sleep(_HEALTH_POLL_INTERVAL)

    # Timed out — kill the child and clean up
    _stop_daemon_unlocked()
    msg = f"Daemon did not become healthy within {_HEALTH_TIMEOUT}s"
    raise RuntimeError(msg)


def stop_daemon() -> bool:
    """Stop a running daemon.

    Returns:
        True if a daemon was stopped, False if none was running.
    """
    with _daemon_lifecycle_lock():
        return _stop_daemon_unlocked()


def _stop_daemon_unlocked() -> bool:
    """Stop daemon without acquiring the lifecycle lock (caller must hold it).

    Returns:
        True if a daemon was stopped, False if none was running.
    """
    state = DaemonState.load()
    if state is None:
        return False

    if not _verify_daemon_ownership(state.pid):
        DaemonState.remove()
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


def _is_within_startup_window(state: DaemonState) -> bool:
    """Return True when *state* was published recently and may still be starting.

    Args:
        state: Persisted daemon state with ``started_at``.

    Returns:
        True when ``started_at`` is within ``_HEALTH_TIMEOUT`` of now.
    """
    try:
        started = datetime.fromisoformat(state.started_at)
    except ValueError:
        return False
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return (datetime.now(UTC) - started).total_seconds() < _HEALTH_TIMEOUT


def daemon_status() -> DaemonState | None:
    """Check daemon status.

    Returns:
        DaemonState if running, None otherwise.
    """
    with _daemon_lifecycle_lock():
        return _daemon_status_unlocked()


def _daemon_status_unlocked() -> DaemonState | None:
    """Check daemon status without acquiring the lifecycle lock.

    Returns:
        DaemonState if running, None otherwise.

    Raises:
        RuntimeError: When a live process owns daemon ports but cannot be
            verified or health-checked (blocks unsafe auto-start), or when a
            verified daemon is still within its startup health window.
    """
    state = DaemonState.load()
    if state is None:
        return None
    if not _pid_alive(state.pid):
        DaemonState.remove()
        return None
    if not _health_check(state.engine, timeout=1.0):
        # Crashed process, unbound marker, or PID reuse without a stop-safe marker.
        if _verify_daemon_ownership(state.pid):
            # Concurrent ensure_daemon() during start_daemon()'s health poll must
            # not treat the still-starting process as stale and kill it.
            if _is_within_startup_window(state):
                msg = (
                    f"Daemon (pid {state.pid}) is still starting. "
                    f"Wait for required health checks to complete, then retry."
                )
                raise RuntimeError(msg)
            _stop_daemon_unlocked()
            return None
        # Live PID, failed Engine health, no ownership proof. If the recorded
        # Engine port is still bound, auto-start would collide — ask the
        # operator to free it instead of deleting state and retrying.
        if _address_port_bound(state.engine):
            msg = (
                f"A live process (pid {state.pid}) still holds daemon ports but "
                f"does not answer Engine health and cannot be verified for "
                f"ownership. Stop it manually (`kill {state.pid}`), remove "
                f"`~/.apme-data/daemon.json` if it remains, then retry. "
                f"Auto-start is blocked to avoid port conflicts."
            )
            raise RuntimeError(msg)
        DaemonState.remove()
        return None
    return state


def ensure_daemon() -> str:
    """Ensure a daemon is running and return the Engine address.

    Discovery order:
    1. APME_ENGINE_ADDRESS env var (explicit, wins always)
    2. daemon.json exists and PID is alive
    3. Auto-start daemon

    Restarts on version mismatch.  Delegates to ``start_daemon()``
    which raises ``RuntimeError`` if the daemon fails to start.

    Returns:
        Engine gRPC address (e.g. "127.0.0.1:50051").
    """
    # 1. Explicit env var
    addr = os.environ.get("APME_ENGINE_ADDRESS")
    if addr:
        return addr

    # 2. Existing daemon / 3. Auto-start — serialized to prevent concurrent forks
    with _daemon_lifecycle_lock():
        state = _daemon_status_unlocked()
        if state is not None:
            current = _current_version()
            if state.version != current:
                sys.stderr.write(f"Daemon version {state.version} != installed {current}, restarting...\n")
                sys.stderr.flush()
                _stop_daemon_unlocked()
            else:
                return state.engine

        sys.stderr.write("Starting APME daemon...\n")
        sys.stderr.flush()
        state = _start_daemon_unlocked()
        sys.stderr.write(f"Daemon ready on {state.engine} (pid {state.pid})\n")
        sys.stderr.flush()
        return state.engine
