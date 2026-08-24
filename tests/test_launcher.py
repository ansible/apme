"""Tests for apme_engine.daemon.launcher port-conflict guard and state loading."""

from __future__ import annotations

import json
import signal
import socket
from pathlib import Path
from unittest.mock import patch

import pytest
from pytest import MonkeyPatch

from apme_engine.daemon.launcher import (
    DaemonState,
    _address_port_bound,
    _assert_ports_free,
    _check_port_available,
    _proc_starttime,
    daemon_status,
)


def _ephemeral_port() -> int:
    """Allocate and release an ephemeral port, returning its number.

    Returns:
        An unused TCP port number.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def test_check_port_available_on_free_port() -> None:
    """A port that nobody is listening on should report as available."""
    port = _ephemeral_port()
    assert _check_port_available("127.0.0.1", port) is True


def test_check_port_available_on_bound_port() -> None:
    """A port that is already bound should report as unavailable."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    try:
        assert _check_port_available("127.0.0.1", port) is False
    finally:
        sock.close()


def test_assert_ports_free_raises_on_conflict() -> None:
    """_assert_ports_free raises RuntimeError when a port is in use."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    try:
        with pytest.raises(RuntimeError, match="already in use"):
            _assert_ports_free("127.0.0.1", {"engine": port})
    finally:
        sock.close()


def test_daemon_state_load_rejects_missing_engine_field(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """State without engine is treated as corrupt.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    data_dir = tmp_path / "apme-data"
    data_dir.mkdir()
    state_file = data_dir / "daemon.json"
    state_file.write_text(
        json.dumps(
            {
                "pid": 4242,
                "version": "0.1.0",
                "started_at": "2026-08-03T00:00:00Z",
            }
        )
        + "\n"
    )
    monkeypatch.setattr("apme_engine.daemon.launcher._DATA_DIR", data_dir)
    monkeypatch.setattr("apme_engine.daemon.launcher._STATE_FILE", state_file)

    assert DaemonState.load() is None


def test_address_port_bound_normalizes_wildcard_hosts() -> None:
    """Wildcard bind addresses probe via 127.0.0.1 for port availability."""
    port = _ephemeral_port()
    assert _address_port_bound(f"0.0.0.0:{port}") is False
    assert _address_port_bound(f"[::]:{port}") is False


def test_address_port_bound_treats_malformed_addresses_as_bound() -> None:
    """Malformed addresses are treated as bound so auto-start does not proceed."""
    assert _address_port_bound("127.0.0.1") is True
    assert _address_port_bound("127.0.0.1:not-a-port") is True


def test_proc_starttime_parses_comm_with_spaces_and_paren() -> None:
    """Starttime is read from field 22 even when comm contains spaces or ')'."""
    stat_line = "4242 (my daemon) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 99999"
    with patch("apme_engine.daemon.launcher.Path.read_text", return_value=stat_line):
        assert _proc_starttime(4242) == 99999


def test_daemon_status_removes_stale_state_when_port_free(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Unverified live PID with a free Engine port removes daemon.json only.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    data_dir = tmp_path / "apme-data"
    data_dir.mkdir()
    state_file = data_dir / "daemon.json"
    state_file.write_text(
        json.dumps(
            {
                "pid": 4242,
                "engine": "127.0.0.1:50051",
                "version": "0.1.0",
                "started_at": "2026-08-03T00:00:00Z",
            }
        )
        + "\n"
    )
    monkeypatch.setattr("apme_engine.daemon.launcher._DATA_DIR", data_dir)
    monkeypatch.setattr("apme_engine.daemon.launcher._STATE_FILE", state_file)
    monkeypatch.setattr("apme_engine.daemon.launcher._MARKER_FILE", data_dir / "daemon.marker")

    with (
        patch("apme_engine.daemon.launcher._pid_alive", return_value=True),
        patch("apme_engine.daemon.launcher._health_check", return_value=False),
        patch("apme_engine.daemon.launcher._address_port_bound", return_value=False),
        patch("apme_engine.daemon.launcher._stop_daemon_unlocked") as stop_mock,
    ):
        assert daemon_status() is None

    stop_mock.assert_not_called()
    assert not state_file.exists()


def test_daemon_status_blocks_when_unverified_pid_holds_ports(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Unverified live PID with a bound Engine port raises instead of auto-start.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    data_dir = tmp_path / "apme-data"
    data_dir.mkdir()
    state_file = data_dir / "daemon.json"
    state_file.write_text(
        json.dumps(
            {
                "pid": 4242,
                "engine": "127.0.0.1:50051",
                "version": "0.1.0",
                "started_at": "2026-08-03T00:00:00Z",
            }
        )
        + "\n"
    )
    monkeypatch.setattr("apme_engine.daemon.launcher._DATA_DIR", data_dir)
    monkeypatch.setattr("apme_engine.daemon.launcher._STATE_FILE", state_file)
    monkeypatch.setattr("apme_engine.daemon.launcher._MARKER_FILE", data_dir / "daemon.marker")

    with (
        patch("apme_engine.daemon.launcher._pid_alive", return_value=True),
        patch("apme_engine.daemon.launcher._health_check", return_value=False),
        patch("apme_engine.daemon.launcher._address_port_bound", return_value=True),
        patch("apme_engine.daemon.launcher._stop_daemon_unlocked") as stop_mock,
        pytest.raises(RuntimeError, match="still holds daemon ports"),
    ):
        daemon_status()

    stop_mock.assert_not_called()
    assert state_file.exists()


def test_write_daemon_marker_requires_proc_starttime(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """PID-only markers are not written when /proc starttime is unavailable.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    data_dir = tmp_path / "apme-data"
    marker_file = data_dir / "daemon.marker"
    monkeypatch.setattr("apme_engine.daemon.launcher._DATA_DIR", data_dir)
    monkeypatch.setattr("apme_engine.daemon.launcher._MARKER_FILE", marker_file)

    with (
        patch("apme_engine.daemon.launcher._proc_starttime", return_value=None),
        pytest.raises(RuntimeError, match="stop-safe daemon marker"),
    ):
        from apme_engine.daemon.launcher import _write_daemon_marker

        _write_daemon_marker(4242)

    assert not marker_file.exists()


def test_require_proc_identity_fails_without_proc(monkeypatch: MonkeyPatch) -> None:
    """start_daemon refuses to launch when /proc identity is unavailable.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    with (
        patch("apme_engine.daemon.launcher._proc_starttime", return_value=None),
        pytest.raises(RuntimeError, match="requires /proc"),
    ):
        from apme_engine.daemon.launcher import _require_proc_identity

        _require_proc_identity()


def test_daemon_status_stops_verified_stale_daemon(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """A marked daemon PID with a failed health check is stopped safely.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from datetime import UTC, datetime, timedelta

    data_dir = tmp_path / "apme-data"
    data_dir.mkdir()
    state_file = data_dir / "daemon.json"
    marker_file = data_dir / "daemon.marker"
    stale_started_at = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    state_file.write_text(
        json.dumps(
            {
                "pid": 4242,
                "engine": "127.0.0.1:50051",
                "version": "0.1.0",
                "started_at": stale_started_at,
            }
        )
        + "\n"
    )
    marker_file.write_text("4242\n99999\n")
    monkeypatch.setattr("apme_engine.daemon.launcher._DATA_DIR", data_dir)
    monkeypatch.setattr("apme_engine.daemon.launcher._STATE_FILE", state_file)
    monkeypatch.setattr("apme_engine.daemon.launcher._MARKER_FILE", marker_file)

    with (
        patch("apme_engine.daemon.launcher._pid_alive", return_value=True),
        patch("apme_engine.daemon.launcher._proc_starttime", return_value=99999),
        patch("apme_engine.daemon.launcher._health_check", return_value=False),
        patch("apme_engine.daemon.launcher._stop_daemon_unlocked", return_value=True) as stop_mock,
    ):
        assert daemon_status() is None

    stop_mock.assert_called_once()


def test_verify_daemon_ownership_rejects_reused_pid(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """A matching PID with a different /proc starttime is not owned by us.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    marker_file = tmp_path / "daemon.marker"
    marker_file.write_text("4242\n111\n")
    monkeypatch.setattr("apme_engine.daemon.launcher._MARKER_FILE", marker_file)

    with (
        patch("apme_engine.daemon.launcher._pid_alive", return_value=True),
        patch("apme_engine.daemon.launcher._proc_starttime", return_value=222),
    ):
        from apme_engine.daemon.launcher import _verify_daemon_ownership

        assert _verify_daemon_ownership(4242) is False


def test_verify_daemon_ownership_rejects_pid_only_marker(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """PID-only markers are not trusted for stop signaling.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    marker_file = tmp_path / "daemon.marker"
    marker_file.write_text("4242\n")
    monkeypatch.setattr("apme_engine.daemon.launcher._MARKER_FILE", marker_file)

    with patch("apme_engine.daemon.launcher._pid_alive", return_value=True):
        from apme_engine.daemon.launcher import _verify_daemon_ownership

        assert _verify_daemon_ownership(4242) is False


def test_start_daemon_writes_marker_before_state(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Parent publishes daemon.marker before daemon.json after fork.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    data_dir = tmp_path / "apme-data"
    data_dir.mkdir()
    state_file = data_dir / "daemon.json"
    marker_file = data_dir / "daemon.marker"
    monkeypatch.setattr("apme_engine.daemon.launcher._DATA_DIR", data_dir)
    monkeypatch.setattr("apme_engine.daemon.launcher._STATE_FILE", state_file)
    monkeypatch.setattr("apme_engine.daemon.launcher._MARKER_FILE", marker_file)
    monkeypatch.setattr("apme_engine.daemon.launcher._HEALTH_POLL_INTERVAL", 0.001)

    order: list[str] = []

    def fake_write(pid: int) -> None:
        order.append("marker")
        marker_file.write_text(f"{pid}\n1\n")

    def fake_save(self: object) -> None:
        order.append("state")
        state_file.write_text("{}\n")

    with (
        patch("apme_engine.daemon.launcher._require_proc_identity"),
        patch("apme_engine.daemon.launcher._assert_ports_free"),
        patch("apme_engine.daemon.launcher.os.fork", return_value=4242),
        patch("apme_engine.daemon.launcher._write_daemon_marker", side_effect=fake_write),
        patch("apme_engine.daemon.launcher.DaemonState.save", fake_save),
        patch(
            "apme_engine.daemon.health_check.run_health_checks",
            return_value={
                "engine": {"ok": True},
                "native": {"ok": True},
                "opa": {"ok": True},
                "ansible": {"ok": True},
                "galaxy_proxy": {"ok": True},
            },
        ),
    ):
        from apme_engine.daemon.launcher import start_daemon

        start_daemon()

    assert order == ["marker", "state"]


def test_start_daemon_cleans_up_when_state_save_fails(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Failed daemon.json persistence kills the child and removes the marker.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    data_dir = tmp_path / "apme-data"
    data_dir.mkdir()
    state_file = data_dir / "daemon.json"
    marker_file = data_dir / "daemon.marker"
    monkeypatch.setattr("apme_engine.daemon.launcher._DATA_DIR", data_dir)
    monkeypatch.setattr("apme_engine.daemon.launcher._STATE_FILE", state_file)
    monkeypatch.setattr("apme_engine.daemon.launcher._MARKER_FILE", marker_file)

    def fake_write(pid: int) -> None:
        marker_file.write_text(f"{pid}\n1\n")

    def boom(self: object) -> None:
        raise OSError("disk full")

    with (
        patch("apme_engine.daemon.launcher._require_proc_identity"),
        patch("apme_engine.daemon.launcher._assert_ports_free"),
        patch("apme_engine.daemon.launcher.os.fork", return_value=4242),
        patch("apme_engine.daemon.launcher._write_daemon_marker", side_effect=fake_write),
        patch("apme_engine.daemon.launcher.DaemonState.save", boom),
        patch("apme_engine.daemon.launcher.os.kill") as mock_kill,
        patch("apme_engine.daemon.launcher.os.waitpid") as mock_wait,
        pytest.raises(OSError, match="disk full"),
    ):
        from apme_engine.daemon.launcher import start_daemon

        start_daemon()

    mock_kill.assert_called_once_with(4242, signal.SIGTERM)
    mock_wait.assert_called_once_with(4242, 0)
    assert not marker_file.exists()


def test_start_daemon_cleans_up_when_marker_write_raises_oserror(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """OSError from marker persistence kills and reaps the forked child.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    data_dir = tmp_path / "apme-data"
    data_dir.mkdir()
    state_file = data_dir / "daemon.json"
    marker_file = data_dir / "daemon.marker"
    monkeypatch.setattr("apme_engine.daemon.launcher._DATA_DIR", data_dir)
    monkeypatch.setattr("apme_engine.daemon.launcher._STATE_FILE", state_file)
    monkeypatch.setattr("apme_engine.daemon.launcher._MARKER_FILE", marker_file)

    with (
        patch("apme_engine.daemon.launcher._require_proc_identity"),
        patch("apme_engine.daemon.launcher._assert_ports_free"),
        patch("apme_engine.daemon.launcher.os.fork", return_value=4242),
        patch(
            "apme_engine.daemon.launcher._write_daemon_marker",
            side_effect=OSError("disk full"),
        ),
        patch("apme_engine.daemon.launcher.os.kill") as mock_kill,
        patch("apme_engine.daemon.launcher.os.waitpid") as mock_wait,
        pytest.raises(OSError, match="disk full"),
    ):
        from apme_engine.daemon.launcher import start_daemon

        start_daemon()

    mock_kill.assert_called_once_with(4242, signal.SIGTERM)
    mock_wait.assert_called_once_with(4242, 0)
    assert not state_file.exists()


def test_daemon_status_preserves_starting_daemon(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Owned daemon within the startup window is not stopped on failed health.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from datetime import UTC, datetime

    data_dir = tmp_path / "apme-data"
    data_dir.mkdir()
    state_file = data_dir / "daemon.json"
    marker_file = data_dir / "daemon.marker"
    state_file.write_text(
        json.dumps(
            {
                "pid": 4242,
                "engine": "127.0.0.1:50051",
                "version": "0.1.0",
                "started_at": datetime.now(UTC).isoformat(),
            }
        )
        + "\n"
    )
    marker_file.write_text("4242\n1\n")
    monkeypatch.setattr("apme_engine.daemon.launcher._DATA_DIR", data_dir)
    monkeypatch.setattr("apme_engine.daemon.launcher._STATE_FILE", state_file)
    monkeypatch.setattr("apme_engine.daemon.launcher._MARKER_FILE", marker_file)

    with (
        patch("apme_engine.daemon.launcher._pid_alive", return_value=True),
        patch("apme_engine.daemon.launcher._health_check", return_value=False),
        patch("apme_engine.daemon.launcher._verify_daemon_ownership", return_value=True),
        patch("apme_engine.daemon.launcher._stop_daemon_unlocked") as stop_mock,
        pytest.raises(RuntimeError, match="still starting"),
    ):
        daemon_status()

    stop_mock.assert_not_called()
    assert state_file.exists()


def test_start_daemon_returns_existing_healthy_without_fork(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """A healthy recorded daemon is reused instead of forking again.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from datetime import UTC, datetime

    data_dir = tmp_path / "apme-data"
    data_dir.mkdir()
    state_file = data_dir / "daemon.json"
    marker_file = data_dir / "daemon.marker"
    state_file.write_text(
        json.dumps(
            {
                "pid": 4242,
                "engine": "127.0.0.1:50051",
                "version": "0.1.0",
                "started_at": datetime.now(UTC).isoformat(),
            }
        )
        + "\n"
    )
    marker_file.write_text("4242\n1\n")
    monkeypatch.setattr("apme_engine.daemon.launcher._DATA_DIR", data_dir)
    monkeypatch.setattr("apme_engine.daemon.launcher._STATE_FILE", state_file)
    monkeypatch.setattr("apme_engine.daemon.launcher._MARKER_FILE", marker_file)

    with (
        patch("apme_engine.daemon.launcher._pid_alive", return_value=True),
        patch("apme_engine.daemon.launcher._health_check", return_value=True),
        patch("apme_engine.daemon.launcher.os.fork") as fork_mock,
    ):
        from apme_engine.daemon.launcher import start_daemon

        state = start_daemon()

    fork_mock.assert_not_called()
    assert state.pid == 4242
    assert state.engine == "127.0.0.1:50051"


def test_concurrent_start_daemon_single_fork(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Concurrent start_daemon calls serialize on the lifecycle lock.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    import threading

    data_dir = tmp_path / "apme-data"
    data_dir.mkdir()
    state_file = data_dir / "daemon.json"
    marker_file = data_dir / "daemon.marker"
    monkeypatch.setattr("apme_engine.daemon.launcher._DATA_DIR", data_dir)
    monkeypatch.setattr("apme_engine.daemon.launcher._STATE_FILE", state_file)
    monkeypatch.setattr("apme_engine.daemon.launcher._MARKER_FILE", marker_file)
    monkeypatch.setattr("apme_engine.daemon.launcher._HEALTH_POLL_INTERVAL", 0.001)

    fork_count = 0
    fork_lock = threading.Lock()

    def counting_fork() -> int:
        nonlocal fork_count
        with fork_lock:
            fork_count += 1
        return 4242 + fork_count

    def fake_write(pid: int) -> None:
        marker_file.write_text(f"{pid}\n1\n")
        state_file.write_text(
            json.dumps(
                {
                    "pid": pid,
                    "engine": "127.0.0.1:50051",
                    "version": "0.1.0",
                    "started_at": "2026-08-05T00:00:00Z",
                    "services": {"engine": "127.0.0.1:50051"},
                }
            )
            + "\n"
        )

    health = {
        "engine": {"ok": True},
        "native": {"ok": True},
        "opa": {"ok": True},
        "ansible": {"ok": True},
        "galaxy_proxy": {"ok": True},
    }

    errors: list[Exception] = []

    def run_start() -> None:
        try:
            from apme_engine.daemon.launcher import start_daemon

            start_daemon()
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - surfaced after join
            errors.append(exc)

    with (
        patch("apme_engine.daemon.launcher._require_proc_identity"),
        patch("apme_engine.daemon.launcher._assert_ports_free"),
        patch("apme_engine.daemon.launcher._pid_alive", return_value=True),
        patch("apme_engine.daemon.launcher._health_check", return_value=True),
        patch("apme_engine.daemon.launcher.os.fork", side_effect=counting_fork),
        patch("apme_engine.daemon.launcher._write_daemon_marker", side_effect=fake_write),
        patch("apme_engine.daemon.health_check.run_health_checks", return_value=health),
    ):
        threads = [threading.Thread(target=run_start) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    assert not errors
    assert fork_count == 1
