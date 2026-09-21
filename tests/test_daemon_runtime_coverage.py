"""Unit tests for daemon runtime modules with prior coverage gaps."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import grpc
import pytest

from apme.v1 import common_pb2, validate_pb2
from apme_engine.daemon.session import SessionState, SessionStore

# ---------------------------------------------------------------------------
# launcher: DaemonState.load error paths
# ---------------------------------------------------------------------------


def test_launcher_load_rejects_corrupt_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Corrupt daemon.json returns None instead of raising.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    state_file = data_dir / "daemon.json"
    state_file.write_text("not-json{{{\n")
    monkeypatch.setattr(launcher, "_DATA_DIR", data_dir)
    monkeypatch.setattr(launcher, "_STATE_FILE", state_file)
    assert launcher.DaemonState.load() is None


def test_launcher_load_rejects_non_dict_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-dict JSON payload returns None.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    state_file = data_dir / "daemon.json"
    state_file.write_text("[1, 2, 3]\n")
    monkeypatch.setattr(launcher, "_DATA_DIR", data_dir)
    monkeypatch.setattr(launcher, "_STATE_FILE", state_file)
    assert launcher.DaemonState.load() is None


def test_launcher_load_rejects_bad_types(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Uncoercible pid and missing started_at return None.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    state_file = data_dir / "daemon.json"
    monkeypatch.setattr(launcher, "_DATA_DIR", data_dir)
    monkeypatch.setattr(launcher, "_STATE_FILE", state_file)

    state_file.write_text(
        json.dumps({"pid": "not-an-int", "engine": "127.0.0.1:50051", "started_at": "x", "version": "v"})
    )
    assert launcher.DaemonState.load() is None

    state_file.write_text(json.dumps({"pid": 123, "engine": "127.0.0.1:50051", "version": "v"}))
    assert launcher.DaemonState.load() is None


def test_launcher_load_missing_file_returns_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Absent daemon.json returns None.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(launcher, "_DATA_DIR", data_dir)
    monkeypatch.setattr(launcher, "_STATE_FILE", data_dir / "daemon.json")
    assert launcher.DaemonState.load() is None


def test_launcher_write_marker_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Valid /proc starttime writes pid and starttime marker.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    data_dir = tmp_path / "data"
    marker = data_dir / "daemon.marker"
    monkeypatch.setattr(launcher, "_DATA_DIR", data_dir)
    monkeypatch.setattr(launcher, "_MARKER_FILE", marker)
    with patch.object(launcher, "_proc_starttime", return_value=777):
        launcher._write_daemon_marker(4242)
    assert marker.read_text().splitlines() == ["4242", "777"]


def test_launcher_proc_starttime_oserror_returns_none() -> None:
    """OSError reading /proc stat returns None."""
    from apme_engine.daemon import launcher

    with patch("apme_engine.daemon.launcher.Path.read_text", side_effect=OSError("no proc")):
        assert launcher._proc_starttime(999999) is None


def test_launcher_proc_starttime_no_rparen_returns_none() -> None:
    """Stat line without ')' returns None."""
    from apme_engine.daemon import launcher

    with patch("apme_engine.daemon.launcher.Path.read_text", return_value="no parens at all"):
        assert launcher._proc_starttime(1) is None


def test_launcher_proc_starttime_short_fields_returns_none() -> None:
    """Stat line with too few fields returns None."""
    from apme_engine.daemon import launcher

    with patch("apme_engine.daemon.launcher.Path.read_text", return_value="1 (x) S 1 2"):
        assert launcher._proc_starttime(1) is None


def test_launcher_proc_starttime_non_int_returns_none() -> None:
    """Non-integer starttime field returns None."""
    from apme_engine.daemon import launcher

    line = "1 (x) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 abc"
    with patch("apme_engine.daemon.launcher.Path.read_text", return_value=line):
        assert launcher._proc_starttime(1) is None


def test_launcher_verify_ownership_missing_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Absent marker file is not owned.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    monkeypatch.setattr(launcher, "_MARKER_FILE", tmp_path / "nope.marker")
    assert launcher._verify_daemon_ownership(123) is False


def test_launcher_verify_ownership_corrupt_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-integer marker pid is not owned.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    marker = tmp_path / "daemon.marker"
    marker.write_text("not-a-pid\n1\n")
    monkeypatch.setattr(launcher, "_MARKER_FILE", marker)
    assert launcher._verify_daemon_ownership(123) is False


def test_launcher_verify_ownership_pid_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Marker pid differing from state pid is not owned.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    marker = tmp_path / "daemon.marker"
    marker.write_text("111\n222\n")
    monkeypatch.setattr(launcher, "_MARKER_FILE", marker)
    with patch.object(launcher, "_pid_alive", return_value=True):
        assert launcher._verify_daemon_ownership(999) is False


def test_launcher_verify_ownership_dead_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Marker pid for a dead process is not owned.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    marker = tmp_path / "daemon.marker"
    marker.write_text("4242\n999\n")
    monkeypatch.setattr(launcher, "_MARKER_FILE", marker)
    with patch.object(launcher, "_pid_alive", return_value=False):
        assert launcher._verify_daemon_ownership(4242) is False


def test_launcher_verify_ownership_bad_starttime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-integer marker starttime is not owned.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    marker = tmp_path / "daemon.marker"
    marker.write_text("4242\nabc\n")
    monkeypatch.setattr(launcher, "_MARKER_FILE", marker)
    with patch.object(launcher, "_pid_alive", return_value=True):
        assert launcher._verify_daemon_ownership(4242) is False


def test_launcher_verify_ownership_live_starttime_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unavailable live starttime is not owned.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    marker = tmp_path / "daemon.marker"
    marker.write_text("4242\n111\n")
    monkeypatch.setattr(launcher, "_MARKER_FILE", marker)
    with (
        patch.object(launcher, "_pid_alive", return_value=True),
        patch.object(launcher, "_proc_starttime", return_value=None),
    ):
        assert launcher._verify_daemon_ownership(4242) is False


def test_launcher_verify_ownership_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Matching pid and starttime is owned.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    marker = tmp_path / "daemon.marker"
    marker.write_text("4242\n555\n")
    monkeypatch.setattr(launcher, "_MARKER_FILE", marker)
    with (
        patch.object(launcher, "_pid_alive", return_value=True),
        patch.object(launcher, "_proc_starttime", return_value=555),
    ):
        assert launcher._verify_daemon_ownership(4242) is True


def test_launcher_current_version_ok_and_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Version lookup returns package version or dev fallback.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    monkeypatch.setattr(launcher, "pkg_version", lambda _name: "9.9.9")
    assert launcher._current_version() == "9.9.9"

    def _boom(_name: str) -> str:
        raise ValueError("missing")

    monkeypatch.setattr(launcher, "pkg_version", _boom)
    assert launcher._current_version() == "0.0.0-dev"


def test_launcher_pid_alive_true_and_false() -> None:
    """_pid_alive maps kill success/failure to bool."""
    from apme_engine.daemon import launcher

    with patch("apme_engine.daemon.launcher.os.kill", return_value=None):
        assert launcher._pid_alive(1) is True
    with patch("apme_engine.daemon.launcher.os.kill", side_effect=OSError("gone")):
        assert launcher._pid_alive(1) is False


def test_launcher_health_check_ok() -> None:
    """Healthy Engine stub returns True and closes channel."""
    from apme_engine.daemon import launcher

    channel = MagicMock()
    stub = MagicMock()
    stub.Health.return_value = MagicMock(status="ok")
    with (
        patch("grpc.insecure_channel", return_value=channel),
        patch("apme.v1.engine_pb2_grpc.EngineStub", return_value=stub),
    ):
        assert launcher._health_check("127.0.0.1:50051", timeout=0.1) is True
    channel.close.assert_called_once()


def test_launcher_health_check_bad_status() -> None:
    """Non-ok status returns False."""
    from apme_engine.daemon import launcher

    channel = MagicMock()
    stub = MagicMock()
    stub.Health.return_value = MagicMock(status="bad")
    with (
        patch("grpc.insecure_channel", return_value=channel),
        patch("apme.v1.engine_pb2_grpc.EngineStub", return_value=stub),
    ):
        assert launcher._health_check("127.0.0.1:50051", timeout=0.1) is False


def test_launcher_health_check_exception_returns_false() -> None:
    """RPC exception degrades to False."""
    from apme_engine.daemon import launcher

    channel = MagicMock()
    with (
        patch("grpc.insecure_channel", return_value=channel),
        patch("apme.v1.engine_pb2_grpc.EngineStub", side_effect=RuntimeError("down")),
    ):
        assert launcher._health_check("127.0.0.1:50051", timeout=0.1) is False
    channel.close.assert_called_once()


def _make_fake_servers() -> tuple[MagicMock, list[MagicMock]]:
    """Build a fake engine server plus validator servers.

    Returns:
        Tuple of engine server mock and list of validator server mocks.
    """
    engine_server = MagicMock()
    engine_server.wait_for_termination = AsyncMock(return_value=None)
    others = [MagicMock() for _ in range(6)]
    return engine_server, others


def test_launcher_run_daemon_all_services(monkeypatch: pytest.MonkeyPatch) -> None:
    """_run_daemon starts every validator, proxy, and engine.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    import apme_engine.daemon.launcher as launcher

    engine_server, _ = _make_fake_servers()
    monkeypatch.setattr("apme_engine.log_bridge.install_handler", lambda: None)

    async def _fake_native(_addr: str) -> MagicMock:
        return MagicMock()

    async def _fake_opa(_addr: str) -> MagicMock:
        return MagicMock()

    async def _fake_ansible(_addr: str) -> MagicMock:
        return MagicMock()

    async def _fake_gitleaks(_addr: str) -> MagicMock:
        return MagicMock()

    async def _fake_ch(_addr: str) -> MagicMock:
        return MagicMock()

    async def _fake_dep(_addr: str) -> MagicMock:
        return MagicMock()

    async def _fake_engine(_addr: str) -> MagicMock:
        return engine_server

    monkeypatch.setattr("apme_engine.daemon.native_validator_server.serve", _fake_native)
    monkeypatch.setattr("apme_engine.daemon.opa_validator_server.serve", _fake_opa)
    monkeypatch.setattr("apme_engine.daemon.ansible_validator_server.serve", _fake_ansible)
    monkeypatch.setattr("apme_engine.daemon.gitleaks_validator_server.serve", _fake_gitleaks)
    monkeypatch.setattr("apme_engine.daemon.collection_health_server.serve", _fake_ch)
    monkeypatch.setattr("apme_engine.daemon.dep_audit_server.serve", _fake_dep)
    monkeypatch.setattr("apme_engine.daemon.engine_server.serve", _fake_engine)

    fake_app = object()

    def _fake_create_app() -> object:
        return fake_app

    fake_proxy_mod = types.ModuleType("galaxy_proxy.proxy.server")
    fake_proxy_mod.create_app = _fake_create_app  # type: ignore[attr-defined]
    fake_uvicorn = types.ModuleType("uvicorn")

    class _FakeConfig:
        def __init__(self, *args: object, **kwargs: object) -> None:
            """Record constructor args.

            Args:
                *args: Positional args.
                **kwargs: Keyword args.
            """

    class _FakeServer:
        def __init__(self, _config: object) -> None:
            """Record config.

            Args:
                _config: Server config.
            """

        async def serve(self) -> None:
            """No-op serve."""
            return None

    fake_uvicorn.Config = _FakeConfig  # type: ignore[attr-defined]
    fake_uvicorn.Server = _FakeServer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.setitem(sys.modules, "galaxy_proxy.proxy.server", fake_proxy_mod)

    services = {
        "engine": "127.0.0.1:50051",
        "native": "127.0.0.1:50055",
        "opa": "127.0.0.1:50054",
        "ansible": "127.0.0.1:50053",
        "gitleaks": "127.0.0.1:50056",
        "collection_health": "127.0.0.1:50058",
        "dep_audit": "127.0.0.1:50059",
        "galaxy_proxy": "127.0.0.1:8765",
    }
    old_env = dict(os.environ)
    try:
        asyncio.run(launcher._run_daemon(services))
        proxy_url = os.environ.get("APME_GALAXY_PROXY_URL")
    finally:
        os.environ.clear()
        os.environ.update(old_env)
    assert proxy_url == "http://127.0.0.1:8765"


def test_launcher_start_unlocked_stops_stale_then_starts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stale recorded daemon is stopped before forking a replacement.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(launcher, "_DATA_DIR", data_dir)
    monkeypatch.setattr(launcher, "_STATE_FILE", data_dir / "daemon.json")
    monkeypatch.setattr(launcher, "_MARKER_FILE", data_dir / "daemon.marker")
    monkeypatch.setattr(launcher, "_HEALTH_POLL_INTERVAL", 0.001)

    stale = launcher.DaemonState(
        pid=1111, engine="127.0.0.1:50051", version="x", started_at=datetime.now(UTC).isoformat(), services={}
    )
    healthy = {
        "engine": {"ok": True},
        "native": {"ok": True},
        "opa": {"ok": True},
        "ansible": {"ok": True},
        "galaxy_proxy": {"ok": True},
    }
    with (
        patch.object(launcher, "_require_proc_identity"),
        patch.object(launcher, "_daemon_status_unlocked", return_value=stale),
        patch.object(launcher, "_pid_alive", return_value=False),
        patch.object(launcher, "_stop_daemon_unlocked", return_value=True) as stop_mock,
        patch.object(launcher, "_assert_ports_free"),
        patch("apme_engine.daemon.launcher.os.fork", return_value=4242),
        patch.object(launcher, "_write_daemon_marker"),
        patch.object(launcher.DaemonState, "save"),
        patch("apme_engine.daemon.health_check.run_health_checks", return_value=healthy),
    ):
        state = launcher._start_daemon_unlocked()
    stop_mock.assert_called_once()
    assert state.pid == 4242


def test_launcher_child_keyboard_interrupt_exits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Forked child swallows KeyboardInterrupt then exits zero.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(launcher, "_DATA_DIR", data_dir)
    monkeypatch.setattr(launcher, "_STATE_FILE", data_dir / "daemon.json")
    monkeypatch.setattr(launcher, "_MARKER_FILE", data_dir / "daemon.marker")

    def _raise_kb(_services: object) -> None:
        raise KeyboardInterrupt

    def _fake_exit(_code: int) -> None:
        raise SystemExit(_code)

    with (
        patch.object(launcher, "_require_proc_identity"),
        patch.object(launcher, "_daemon_status_unlocked", return_value=None),
        patch.object(launcher, "_assert_ports_free"),
        patch("apme_engine.daemon.launcher.os.fork", return_value=0),
        patch("apme_engine.daemon.launcher.os.setsid", return_value=0),
        patch("apme_engine.daemon.launcher.os.dup2", return_value=None),
        patch("apme_engine.daemon.launcher.asyncio.run", side_effect=_raise_kb),
        patch("apme_engine.daemon.launcher.os._exit", side_effect=_fake_exit),
        pytest.raises(SystemExit),
    ):
        launcher._start_daemon_unlocked()


def test_launcher_child_exception_exits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Forked child logs crashes then exits zero.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(launcher, "_DATA_DIR", data_dir)
    monkeypatch.setattr(launcher, "_STATE_FILE", data_dir / "daemon.json")
    monkeypatch.setattr(launcher, "_MARKER_FILE", data_dir / "daemon.marker")

    def _raise_runtime(_services: object) -> None:
        raise RuntimeError("boom")

    def _fake_exit(_code: int) -> None:
        raise SystemExit(_code)

    with (
        patch.object(launcher, "_require_proc_identity"),
        patch.object(launcher, "_daemon_status_unlocked", return_value=None),
        patch.object(launcher, "_assert_ports_free"),
        patch("apme_engine.daemon.launcher.os.fork", return_value=0),
        patch("apme_engine.daemon.launcher.os.setsid", return_value=0),
        patch("apme_engine.daemon.launcher.os.dup2", return_value=None),
        patch("apme_engine.daemon.launcher.asyncio.run", side_effect=_raise_runtime),
        patch("apme_engine.daemon.launcher.os._exit", side_effect=_fake_exit),
        pytest.raises(SystemExit),
    ):
        launcher._start_daemon_unlocked()


def test_launcher_start_poll_pid_dies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Poll loop raises when the child dies before health.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(launcher, "_DATA_DIR", data_dir)
    monkeypatch.setattr(launcher, "_STATE_FILE", data_dir / "daemon.json")
    monkeypatch.setattr(launcher, "_MARKER_FILE", data_dir / "daemon.marker")
    bad = {
        "engine": {"ok": False},
        "native": {"ok": True},
        "opa": {"ok": True},
        "ansible": {"ok": True},
        "galaxy_proxy": {"ok": True},
    }
    with (
        patch.object(launcher, "_require_proc_identity"),
        patch.object(launcher, "_daemon_status_unlocked", return_value=None),
        patch.object(launcher, "_assert_ports_free"),
        patch("apme_engine.daemon.launcher.os.fork", return_value=4242),
        patch.object(launcher, "_write_daemon_marker"),
        patch.object(launcher.DaemonState, "save"),
        patch("apme_engine.daemon.health_check.run_health_checks", return_value=bad),
        patch.object(launcher, "_pid_alive", return_value=False),
        patch("apme_engine.daemon.launcher.time.sleep", return_value=None),
        pytest.raises(RuntimeError, match="exited before becoming healthy"),
    ):
        launcher._start_daemon_unlocked()


def test_launcher_start_poll_timeout_kills_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Poll timeout stops the child and raises.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(launcher, "_DATA_DIR", data_dir)
    monkeypatch.setattr(launcher, "_STATE_FILE", data_dir / "daemon.json")
    monkeypatch.setattr(launcher, "_MARKER_FILE", data_dir / "daemon.marker")
    bad = {
        "engine": {"ok": False},
        "native": {"ok": True},
        "opa": {"ok": True},
        "ansible": {"ok": True},
        "galaxy_proxy": {"ok": True},
    }
    with (
        patch.object(launcher, "_require_proc_identity"),
        patch.object(launcher, "_daemon_status_unlocked", return_value=None),
        patch.object(launcher, "_assert_ports_free"),
        patch("apme_engine.daemon.launcher.os.fork", return_value=4242),
        patch.object(launcher, "_write_daemon_marker"),
        patch.object(launcher.DaemonState, "save"),
        patch("apme_engine.daemon.health_check.run_health_checks", return_value=bad),
        patch.object(launcher, "_pid_alive", return_value=True),
        patch("apme_engine.daemon.launcher.time.monotonic", side_effect=[0.0, 1000.0]),
        patch("apme_engine.daemon.launcher.time.sleep", return_value=None),
        patch.object(launcher, "_stop_daemon_unlocked", return_value=True) as stop_mock,
        pytest.raises(RuntimeError, match="did not become healthy"),
    ):
        launcher._start_daemon_unlocked()
    stop_mock.assert_called_once()


def test_launcher_stop_no_state_returns_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stopping with no state file returns False.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(launcher, "_DATA_DIR", data_dir)
    monkeypatch.setattr(launcher, "_STATE_FILE", data_dir / "daemon.json")
    monkeypatch.setattr(launcher, "_MARKER_FILE", data_dir / "daemon.marker")
    assert launcher._stop_daemon_unlocked() is False


def test_launcher_stop_unowned_removes_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unowned state is removed without signaling.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    state_file = data_dir / "daemon.json"
    state_file.write_text(
        json.dumps(
            {"pid": 4242, "engine": "127.0.0.1:50051", "version": "v", "started_at": datetime.now(UTC).isoformat()}
        )
    )
    monkeypatch.setattr(launcher, "_DATA_DIR", data_dir)
    monkeypatch.setattr(launcher, "_STATE_FILE", state_file)
    monkeypatch.setattr(launcher, "_MARKER_FILE", data_dir / "daemon.marker")
    with patch.object(launcher, "_verify_daemon_ownership", return_value=False):
        assert launcher._stop_daemon_unlocked() is False
    assert not state_file.exists()


def test_launcher_stop_sigkill_after_grace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stubborn child gets SIGKILL after the grace loop.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    state_file = data_dir / "daemon.json"
    state_file.write_text(
        json.dumps(
            {"pid": 4242, "engine": "127.0.0.1:50051", "version": "v", "started_at": datetime.now(UTC).isoformat()}
        )
    )
    monkeypatch.setattr(launcher, "_DATA_DIR", data_dir)
    monkeypatch.setattr(launcher, "_STATE_FILE", state_file)
    monkeypatch.setattr(launcher, "_MARKER_FILE", data_dir / "daemon.marker")
    kills: list[int] = []
    with (
        patch.object(launcher, "_verify_daemon_ownership", return_value=True),
        patch.object(launcher, "_pid_alive", return_value=True),
        patch("apme_engine.daemon.launcher.os.kill", side_effect=lambda pid, sig: kills.append(int(sig))),
        patch("apme_engine.daemon.launcher.time.sleep", return_value=None),
    ):
        assert launcher._stop_daemon_unlocked() is True
    assert signal.SIGTERM in kills
    assert signal.SIGKILL in kills


def test_launcher_stop_kill_oserror_suppressed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """OSError during signaling still removes state and returns True.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    state_file = data_dir / "daemon.json"
    state_file.write_text(
        json.dumps(
            {"pid": 4242, "engine": "127.0.0.1:50051", "version": "v", "started_at": datetime.now(UTC).isoformat()}
        )
    )
    monkeypatch.setattr(launcher, "_DATA_DIR", data_dir)
    monkeypatch.setattr(launcher, "_STATE_FILE", state_file)
    monkeypatch.setattr(launcher, "_MARKER_FILE", data_dir / "daemon.marker")
    with (
        patch.object(launcher, "_verify_daemon_ownership", return_value=True),
        patch.object(launcher, "_pid_alive", return_value=True),
        patch("apme_engine.daemon.launcher.os.kill", side_effect=OSError("gone")),
        patch("apme_engine.daemon.launcher.time.sleep", return_value=None),
    ):
        assert launcher._stop_daemon_unlocked() is True


def test_launcher_startup_window_branches() -> None:
    """Startup window handles invalid, naive, recent, and old timestamps."""
    from apme_engine.daemon.launcher import DaemonState, _is_within_startup_window

    bad = DaemonState(pid=1, engine="e", version="v", started_at="not-a-time")
    assert _is_within_startup_window(bad) is False

    naive = DaemonState(pid=1, engine="e", version="v", started_at=datetime.now(UTC).replace(tzinfo=None).isoformat())
    assert _is_within_startup_window(naive) is True

    recent = DaemonState(pid=1, engine="e", version="v", started_at=datetime.now(UTC).isoformat())
    assert _is_within_startup_window(recent) is True

    old = DaemonState(pid=1, engine="e", version="v", started_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat())
    assert _is_within_startup_window(old) is False


def test_launcher_status_no_state_and_dead_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Status returns None for missing state and reaps dead pids.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    state_file = data_dir / "daemon.json"
    monkeypatch.setattr(launcher, "_DATA_DIR", data_dir)
    monkeypatch.setattr(launcher, "_STATE_FILE", state_file)
    monkeypatch.setattr(launcher, "_MARKER_FILE", data_dir / "daemon.marker")
    assert launcher._daemon_status_unlocked() is None

    state_file.write_text(
        json.dumps(
            {"pid": 4242, "engine": "127.0.0.1:50051", "version": "v", "started_at": datetime.now(UTC).isoformat()}
        )
    )
    with patch.object(launcher, "_pid_alive", return_value=False):
        assert launcher._daemon_status_unlocked() is None
    assert not state_file.exists()


def test_launcher_status_healthy_returns_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Healthy engine check returns the recorded state.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    state_file = data_dir / "daemon.json"
    state_file.write_text(
        json.dumps(
            {"pid": 4242, "engine": "127.0.0.1:50051", "version": "v", "started_at": datetime.now(UTC).isoformat()}
        )
    )
    monkeypatch.setattr(launcher, "_DATA_DIR", data_dir)
    monkeypatch.setattr(launcher, "_STATE_FILE", state_file)
    monkeypatch.setattr(launcher, "_MARKER_FILE", data_dir / "daemon.marker")
    with (
        patch.object(launcher, "_pid_alive", return_value=True),
        patch.object(launcher, "_health_check", return_value=True),
    ):
        state = launcher._daemon_status_unlocked()
    assert state is not None
    assert state.pid == 4242


def test_launcher_ensure_env_var_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit APME_ENGINE_ADDRESS short-circuits discovery.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    monkeypatch.setenv("APME_ENGINE_ADDRESS", "10.0.0.1:50051")
    with patch.object(launcher, "_daemon_status_unlocked") as status_mock:
        assert launcher.ensure_daemon() == "10.0.0.1:50051"
    status_mock.assert_not_called()


def test_launcher_ensure_reuses_version_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Matching version returns the existing engine address.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    monkeypatch.delenv("APME_ENGINE_ADDRESS", raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(launcher, "_DATA_DIR", data_dir)
    monkeypatch.setattr(launcher, "_STATE_FILE", data_dir / "daemon.json")
    monkeypatch.setattr(launcher, "_MARKER_FILE", data_dir / "daemon.marker")
    state = launcher.DaemonState(pid=1, engine="127.0.0.1:50051", version="9.9.9", started_at="x")
    with (
        patch.object(launcher, "_daemon_status_unlocked", return_value=state),
        patch.object(launcher, "_current_version", return_value="9.9.9"),
        patch.object(launcher, "_start_daemon_unlocked") as start_mock,
    ):
        assert launcher.ensure_daemon() == "127.0.0.1:50051"
    start_mock.assert_not_called()


def test_launcher_ensure_restarts_on_version_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Version drift stops the old daemon and starts a fresh one.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    monkeypatch.delenv("APME_ENGINE_ADDRESS", raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(launcher, "_DATA_DIR", data_dir)
    monkeypatch.setattr(launcher, "_STATE_FILE", data_dir / "daemon.json")
    monkeypatch.setattr(launcher, "_MARKER_FILE", data_dir / "daemon.marker")
    old = launcher.DaemonState(pid=1, engine="127.0.0.1:50051", version="old", started_at="x")
    new = launcher.DaemonState(pid=2, engine="127.0.0.1:50051", version="new", started_at="x")
    with (
        patch.object(launcher, "_daemon_status_unlocked", return_value=old),
        patch.object(launcher, "_current_version", return_value="new"),
        patch.object(launcher, "_stop_daemon_unlocked", return_value=True) as stop_mock,
        patch.object(launcher, "_start_daemon_unlocked", return_value=new),
    ):
        assert launcher.ensure_daemon() == "127.0.0.1:50051"
    stop_mock.assert_called_once()


def test_launcher_ensure_autostarts_when_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing daemon triggers autostart.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    monkeypatch.delenv("APME_ENGINE_ADDRESS", raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(launcher, "_DATA_DIR", data_dir)
    monkeypatch.setattr(launcher, "_STATE_FILE", data_dir / "daemon.json")
    monkeypatch.setattr(launcher, "_MARKER_FILE", data_dir / "daemon.marker")
    new = launcher.DaemonState(pid=2, engine="127.0.0.1:50051", version="v", started_at="x")
    with (
        patch.object(launcher, "_daemon_status_unlocked", return_value=None),
        patch.object(launcher, "_start_daemon_unlocked", return_value=new),
    ):
        assert launcher.ensure_daemon() == "127.0.0.1:50051"


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


def test_health_body_empty_is_unhealthy() -> None:
    """Blank bodies are unhealthy."""
    from apme_engine.daemon.health_check import _http_health_body_ok

    assert _http_health_body_ok("") is False
    assert _http_health_body_ok("   \n  ") is False
    assert _http_health_body_ok("not json") is False


def test_health_stub_protocol_callable() -> None:
    """Protocol Health placeholder executes."""

    class _Impl:
        def Health(self, req: object, timeout: float = 5.0) -> object:
            """Echo stub.

            Args:
                req: Request object.
                timeout: Timeout seconds.

            Returns:
                Placeholder response.
            """
            return {"req": req, "timeout": timeout}

    impl = _Impl()
    assert impl.Health(object(), timeout=1.0) is not None


def test_derive_addresses_with_and_without_port() -> None:
    """Derivation keeps explicit engine addr and handles bare hosts."""
    from apme_engine.daemon.health_check import _derive_addresses

    with_port = _derive_addresses("10.0.0.1:50051")
    assert with_port["engine"] == "10.0.0.1:50051"
    assert with_port["native"] == "10.0.0.1:50055"
    assert with_port["galaxy_proxy"] == "http://10.0.0.1:8765"

    bare = _derive_addresses("myhost")
    assert bare["native"] == "myhost:50055"
    assert bare["galaxy_proxy"] == "http://myhost:8765"


def test_check_grpc_health_success_ok() -> None:
    """OK status maps to ok True."""
    from apme_engine.daemon import health_check

    channel = MagicMock()
    channel.close = MagicMock()
    stub = MagicMock()
    stub.Health.return_value = MagicMock(status="ok")
    with patch("apme_engine.daemon.health_check.grpc.insecure_channel", return_value=channel):
        result = health_check.check_grpc_health("127.0.0.1:1", lambda _ch: stub, timeout=0.1)
    assert result["ok"] is True
    assert result["error"] is None
    channel.close.assert_called_once()


def test_check_grpc_health_success_not_ok() -> None:
    """Non-ok status maps to ok False."""
    from apme_engine.daemon import health_check

    channel = MagicMock()
    stub = MagicMock()
    stub.Health.return_value = MagicMock(status="bad")
    with patch("apme_engine.daemon.health_check.grpc.insecure_channel", return_value=channel):
        result = health_check.check_grpc_health("127.0.0.1:1", lambda _ch: stub, timeout=0.1)
    assert result["ok"] is False


def test_check_grpc_health_rpc_error() -> None:
    """grpc.RpcError degrades to ok False with details."""

    class _RpcError(grpc.RpcError):
        def code(self) -> grpc.StatusCode:
            """Return unavailable code.

            Returns:
                Unavailable status code.
            """
            return grpc.StatusCode.UNAVAILABLE

        def details(self) -> str:
            """Return details string.

            Returns:
                Details string.
            """
            return "unavailable"

    from apme_engine.daemon import health_check

    channel = MagicMock()
    stub = MagicMock()
    stub.Health.side_effect = _RpcError()
    with patch("apme_engine.daemon.health_check.grpc.insecure_channel", return_value=channel):
        result = health_check.check_grpc_health("127.0.0.1:1", lambda _ch: stub, timeout=0.1)
    assert result["ok"] is False
    assert result["error"] == "unavailable"


def test_check_grpc_health_generic_error() -> None:
    """Generic exceptions degrade to ok False."""
    from apme_engine.daemon import health_check

    channel = MagicMock()
    stub = MagicMock()
    stub.Health.side_effect = ValueError("bad stub")
    with patch("apme_engine.daemon.health_check.grpc.insecure_channel", return_value=channel):
        result = health_check.check_grpc_health("127.0.0.1:1", lambda _ch: stub, timeout=0.1)
    assert result["ok"] is False
    assert "bad stub" in str(result["error"])


def test_check_http_health_http_error() -> None:
    """httpx.HTTPError maps to ok False."""
    import httpx

    from apme_engine.daemon import health_check

    with patch("apme_engine.daemon.health_check.httpx.Client", side_effect=httpx.ConnectError("refused")):
        result = health_check.check_http_health("http://127.0.0.1:8765", timeout=0.1)
    assert result["ok"] is False


def test_check_http_health_generic_error() -> None:
    """Generic exceptions map to ok False."""
    from apme_engine.daemon import health_check

    with patch("apme_engine.daemon.health_check.httpx.Client", side_effect=ValueError("bad client")):
        result = health_check.check_http_health("http://127.0.0.1:8765", timeout=0.1)
    assert result["ok"] is False


def test_check_http_health_success_ok() -> None:
    """HTTP 200 with ok body maps to ok True."""
    from apme_engine.daemon import health_check

    resp = MagicMock(status_code=200, text='{"status": "ok"}')
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.get.return_value = resp
    with patch("apme_engine.daemon.health_check.httpx.Client", return_value=client):
        result = health_check.check_http_health("http://127.0.0.1:8765", timeout=0.1)
    assert result["ok"] is True


def test_run_health_checks_uses_env_and_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit args win; env vars override derived defaults.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import health_check

    seen: dict[str, str] = {}

    def _fake_grpc(addr: str, _factory: object, _timeout: float = 1.0) -> dict[str, object]:
        seen.setdefault("grpc", addr)
        return {"ok": True, "status": "ok", "error": None, "latency_ms": 1.0}

    def _fake_http(url: str, _timeout: float = 1.0) -> dict[str, object]:
        seen["http"] = url
        return {"ok": True, "status": "ok", "error": None, "latency_ms": 1.0}

    monkeypatch.setenv("NATIVE_GRPC_ADDRESS", "10.0.0.1:50055")
    monkeypatch.delenv("OPA_GRPC_ADDRESS", raising=False)
    monkeypatch.delenv("ANSIBLE_GRPC_ADDRESS", raising=False)
    monkeypatch.delenv("APME_GALAXY_PROXY_URL", raising=False)
    with (
        patch.object(health_check, "check_grpc_health", side_effect=_fake_grpc),
        patch.object(health_check, "check_http_health", side_effect=_fake_http),
    ):
        results = health_check.run_health_checks("127.0.0.1:50051", opa_addr="9.9.9.9:50054", timeout=0.1)
    assert set(results) == {"engine", "native", "opa", "ansible", "galaxy_proxy"}
    assert seen["http"].startswith("http://")


# ---------------------------------------------------------------------------
# chunked_fs
# ---------------------------------------------------------------------------


def test_chunked_load_ignore_missing_returns_empty(tmp_path: Path) -> None:
    """Missing .apmeignore yields no patterns.

    Args:
        tmp_path: Pytest temporary directory.
    """
    from apme_engine.daemon.chunked_fs import _load_apmeignore

    assert _load_apmeignore(tmp_path) == []


def test_chunked_load_ignore_parses_patterns(tmp_path: Path) -> None:
    """Comments and blanks are skipped.

    Args:
        tmp_path: Pytest temporary directory.
    """
    from apme_engine.daemon.chunked_fs import _load_apmeignore

    (tmp_path / ".apmeignore").write_text("# comment\n\n*.log\nbuild/\n")
    assert _load_apmeignore(tmp_path) == ["*.log", "build/"]


def test_chunked_load_ignore_oserror_returns_empty(tmp_path: Path) -> None:
    """Unreadable .apmeignore degrades to empty.

    Args:
        tmp_path: Pytest temporary directory.
    """
    from apme_engine.daemon.chunked_fs import _load_apmeignore

    (tmp_path / ".apmeignore").write_text("*.log\n")
    with patch.object(Path, "read_text", side_effect=OSError("denied")):
        assert _load_apmeignore(tmp_path) == []


def test_chunked_matches_ignore_branches() -> None:
    """Directory, glob, nested, part, and miss patterns behave."""
    from apme_engine.daemon.chunked_fs import _matches_ignore

    assert _matches_ignore("build/out.yml", ["build/"]) is True
    assert _matches_ignore("a.log", ["*.log"]) is True
    assert _matches_ignore("roles/x/tasks.yml", ["roles/*/tasks.yml"]) is True
    assert _matches_ignore("roles/x/tasks.yml", ["tasks.yml"]) is True
    assert _matches_ignore("play.yml", ["*.log", "build/"]) is False


def test_chunked_should_include_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Size, skip dirs, skip files, and extension gates filter correctly.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import chunked_fs

    root = tmp_path / "proj"
    root.mkdir()
    good = root / "play.yml"
    good.write_text("- hosts: all\n")
    assert chunked_fs._should_include(good, root) is True

    assert chunked_fs._should_include(root, root) is False

    monkeypatch.setattr(chunked_fs, "MAX_FILE_SIZE", 1)
    big = root / "big.yml"
    big.write_bytes(b"x" * 10)
    assert chunked_fs._should_include(big, root) is False
    monkeypatch.setattr(chunked_fs, "MAX_FILE_SIZE", 2 * 1024 * 1024)

    git_file = root / ".git" / "x.yml"
    git_file.parent.mkdir(parents=True, exist_ok=True)
    git_file.write_text("x\n")
    assert chunked_fs._should_include(git_file, root) is False

    travis = root / ".travis.yml"
    travis.write_text("x\n")
    assert chunked_fs._should_include(travis, root) is False

    assert chunked_fs._should_include(root / "other.txt", root) is False

    roles_txt = root / "roles" / "r" / "notes.txt"
    roles_txt.parent.mkdir(parents=True, exist_ok=True)
    roles_txt.write_text("hi\n")
    assert chunked_fs._should_include(roles_txt, root) is True

    assert chunked_fs._should_include(Path("/elsewhere/x.yml"), root) is False

    outside = tmp_path / "elsewhere" / "x.yml"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("x\n")
    assert chunked_fs._should_include(outside, root) is False

    ignored = root / "skipme.yml"
    ignored.write_text("x\n")
    assert chunked_fs._should_include(ignored, root, ["skipme.yml"]) is False


def test_chunked_should_include_stat_oserror(tmp_path: Path) -> None:
    """Stat failures exclude the file.

    Args:
        tmp_path: Pytest temporary directory.
    """
    from apme_engine.daemon import chunked_fs

    root = tmp_path / "proj"
    root.mkdir()
    target = MagicMock()
    target.is_file.return_value = True
    target.stat.side_effect = OSError("denied")
    target.relative_to.return_value = Path("a.yml")
    target.name = "a.yml"
    target.suffix = ".yml"
    assert chunked_fs._should_include(target, root) is False


def test_chunked_should_include_named_and_noext(tmp_path: Path) -> None:
    """Bare playbook names and no-extension roles files are included.

    Args:
        tmp_path: Pytest temporary directory.
    """
    from apme_engine.daemon.chunked_fs import _should_include

    root = tmp_path / "proj"
    root.mkdir()
    named = root / "playbook"
    named.write_text("- hosts: all\n")
    assert _should_include(named, root) is True


def test_chunked_build_bundle_missing_raises(tmp_path: Path) -> None:
    """Nonexistent target raises FileNotFoundError.

    Args:
        tmp_path: Pytest temporary directory.
    """
    from apme_engine.daemon.chunked_fs import build_scan_bundle

    with pytest.raises(FileNotFoundError):
        build_scan_bundle(tmp_path / "nope")


def test_chunked_build_bundle_file_and_dir(tmp_path: Path) -> None:
    """Single-file and directory walks collect text and skip binary.

    Args:
        tmp_path: Pytest temporary directory.
    """
    from apme_engine.daemon.chunked_fs import build_scan_bundle

    single = tmp_path / "play.yml"
    single.write_text("- hosts: all\n")
    bundle = build_scan_bundle(single, scan_id="s1")
    assert bundle.scan_id == "s1"
    assert len(bundle.files) == 1

    root = tmp_path / "proj"
    (root / "roles" / "r").mkdir(parents=True)
    (root / "play.yml").write_text("- hosts: all\n")
    (root / "bin.dat").write_bytes(b"\x00\x01\x02binary")
    (root / "bad.yml").write_bytes(b"\x00\x01\x02binary")
    (root / ".git" / "x.yml").parent.mkdir(parents=True, exist_ok=True)
    (root / ".git" / "x.yml").write_text("skip\n")
    bundle2 = build_scan_bundle(root)
    paths = sorted(f.path for f in bundle2.files)
    assert "play.yml" in paths
    assert "bin.dat" not in paths
    assert "bad.yml" not in paths
    assert bundle2.scan_id != ""


def test_chunked_build_bundle_options(tmp_path: Path) -> None:
    """Options fields populate from kwargs.

    Args:
        tmp_path: Pytest temporary directory.
    """
    from apme.v1.common_pb2 import GalaxyServerDef
    from apme.v1.engine_pb2 import RuleConfig
    from apme_engine.daemon.chunked_fs import build_scan_bundle

    target = tmp_path / "play.yml"
    target.write_text("- hosts: all\n")
    bundle = build_scan_bundle(
        target,
        ansible_core_version="2.20",
        collection_specs=["ns.coll"],
        session_id="sess-1",
        galaxy_servers=[GalaxyServerDef(url="https://g.example")],
        rule_configs=[RuleConfig(rule_id="L001")],
        skip_collection_health=True,
        skip_dep_audit=True,
    )
    assert bundle.options.ansible_core_version == "2.20"
    assert list(bundle.options.collection_specs) == ["ns.coll"]
    assert bundle.options.session_id == "sess-1"
    assert bundle.options.skip_collection_health is True
    assert bundle.options.skip_dep_audit is True


def test_chunked_yield_empty_and_single(tmp_path: Path) -> None:
    """Empty bundle yields one terminal chunk; single file carries metadata.

    Args:
        tmp_path: Pytest temporary directory.
    """
    from apme_engine.daemon.chunked_fs import yield_scan_chunks

    root = tmp_path / "empty"
    root.mkdir()
    (root / "notes.txt").write_text("hello\n")
    chunks = list(yield_scan_chunks(root, scan_id="e1"))
    assert len(chunks) == 1
    assert chunks[0].last is True

    single = tmp_path / "one.yml"
    single.write_text("- hosts: all\n")
    chunks2 = list(yield_scan_chunks(single, scan_id="s2"))
    assert len(chunks2) == 1
    assert chunks2[0].scan_id == "s2"
    assert chunks2[0].last is True


def test_chunked_yield_splits_batches(tmp_path: Path) -> None:
    """Tiny chunk budget forces first/middle/last batch boundaries.

    Args:
        tmp_path: Pytest temporary directory.
    """
    from apme_engine.daemon.chunked_fs import yield_scan_chunks

    root = tmp_path / "proj"
    root.mkdir()
    for i in range(4):
        (root / f"p{i}.yml").write_text(f"- hosts: all #{i} " + "x" * 200 + "\n")
    chunks = list(yield_scan_chunks(root, scan_id="multi", chunk_max_bytes=100))
    assert len(chunks) >= 3
    assert chunks[0].scan_id == "multi"
    assert chunks[-1].last is True
    assert all(c.last is False for c in chunks[:-1])


# ---------------------------------------------------------------------------
# grpc_reporting sink
# ---------------------------------------------------------------------------


def test_grpc_sink_start_immediate_available() -> None:
    """First probe success skips retries but still starts health loop."""
    from apme_engine.daemon.sinks.grpc_reporting import GrpcReportingSink

    async def _run() -> GrpcReportingSink:
        sink = GrpcReportingSink("127.0.0.1:50051")
        channel = MagicMock()
        channel.close = AsyncMock(return_value=None)

        async def _fake_probe() -> None:
            sink._available = True

        with (
            patch("apme_engine.daemon.sinks.grpc_reporting.grpc.aio.insecure_channel", return_value=channel),
            patch("apme_engine.daemon.sinks.grpc_reporting.reporting_pb2_grpc.ReportingStub", return_value=MagicMock()),
            patch.object(GrpcReportingSink, "_probe", new=AsyncMock(side_effect=_fake_probe)),
            patch("apme_engine.daemon.sinks.grpc_reporting.asyncio.sleep", new=AsyncMock()),
        ):
            await sink.start()
            assert sink._health_task is not None
            await sink.stop()
            return sink

    # Bind helper before patching (closure above references it at call time).
    sink = asyncio.run(_run())
    assert sink._available is True


def test_grpc_sink_start_retries_then_warns() -> None:
    """Persistent probe failure warns and still launches the health loop."""
    from apme_engine.daemon.sinks.grpc_reporting import GrpcReportingSink

    async def _run() -> bool:
        sink = GrpcReportingSink("127.0.0.1:50051")
        channel = MagicMock()
        channel.close = AsyncMock(return_value=None)
        with (
            patch("apme_engine.daemon.sinks.grpc_reporting.grpc.aio.insecure_channel", return_value=channel),
            patch("apme_engine.daemon.sinks.grpc_reporting.reporting_pb2_grpc.ReportingStub", return_value=MagicMock()),
            patch.object(GrpcReportingSink, "_probe", new=AsyncMock(return_value=None)),
            patch("apme_engine.daemon.sinks.grpc_reporting.asyncio.sleep", new=AsyncMock(return_value=None)),
        ):
            await sink.start()
            task_ok = sink._health_task is not None
            await sink.stop()
            return task_ok and sink._available is False

    assert asyncio.run(_run()) is True


def test_grpc_sink_stop_without_resources() -> None:
    """Stop with no task/channel is a no-op."""

    async def _run() -> None:
        from apme_engine.daemon.sinks.grpc_reporting import GrpcReportingSink

        sink = GrpcReportingSink("127.0.0.1:50051")
        await sink.stop()

    asyncio.run(_run())


def test_grpc_sink_stop_cancels_task() -> None:
    """Stop cancels the health loop and closes the channel."""

    async def _run() -> None:
        from apme_engine.daemon.sinks.grpc_reporting import GrpcReportingSink

        sink = GrpcReportingSink("127.0.0.1:50051")
        channel = MagicMock()
        channel.close = AsyncMock(return_value=None)

        async def _never() -> None:
            await asyncio.sleep(60)

        sink._channel = channel
        sink._health_task = asyncio.ensure_future(_never())
        await asyncio.sleep(0)
        await sink.stop()
        channel.close.assert_awaited_once()

    asyncio.run(_run())


def test_grpc_sink_on_fix_no_stub_returns() -> None:
    """Missing stub drops the event silently."""

    async def _run() -> None:
        from apme.v1 import reporting_pb2
        from apme_engine.daemon.sinks.grpc_reporting import GrpcReportingSink

        sink = GrpcReportingSink("127.0.0.1:50051")
        await sink.on_fix_completed(reporting_pb2.FixCompletedEvent(scan_id="s"))

    asyncio.run(_run())


def test_grpc_sink_on_fix_success_recovers() -> None:
    """Successful delivery flips a down endpoint back to available."""

    async def _run() -> None:
        from apme.v1 import reporting_pb2
        from apme_engine.daemon.sinks.grpc_reporting import GrpcReportingSink

        sink = GrpcReportingSink("127.0.0.1:50051")
        sink._stub = MagicMock()
        sink._stub.ReportFixCompleted = AsyncMock(return_value=MagicMock())
        sink._available = False
        await sink.on_fix_completed(reporting_pb2.FixCompletedEvent(scan_id="s"))
        assert sink._available is True

    asyncio.run(_run())


def test_grpc_sink_on_fix_failure_marks_down() -> None:
    """Delivery failure marks the endpoint unavailable."""

    async def _run() -> None:
        from apme.v1 import reporting_pb2
        from apme_engine.daemon.sinks.grpc_reporting import GrpcReportingSink

        sink = GrpcReportingSink("127.0.0.1:50051")
        sink._stub = MagicMock()
        sink._stub.ReportFixCompleted = AsyncMock(side_effect=RuntimeError("down"))
        sink._available = True
        await sink.on_fix_completed(reporting_pb2.FixCompletedEvent(scan_id="s"))
        assert sink._available is False

    asyncio.run(_run())


def test_grpc_sink_register_rules_branches() -> None:
    """Register covers no-stub, recovery, and failure paths."""

    async def _run() -> None:
        from apme.v1 import reporting_pb2
        from apme_engine.daemon.sinks.grpc_reporting import GrpcReportingSink

        sink = GrpcReportingSink("127.0.0.1:50051")
        assert await sink.register_rules(reporting_pb2.RegisterRulesRequest()) is None

        stub = MagicMock()
        stub.RegisterRules = AsyncMock(return_value=MagicMock())
        sink._stub = stub
        sink._available = False
        resp = await sink.register_rules(reporting_pb2.RegisterRulesRequest())
        assert resp is not None
        assert sink._available is True

        stub.RegisterRules = AsyncMock(side_effect=RuntimeError("down"))
        sink._available = True
        assert await sink.register_rules(reporting_pb2.RegisterRulesRequest()) is None
        assert sink._available is False

    asyncio.run(_run())


def test_grpc_sink_probe_success_failure_cancelled() -> None:
    """Probe sets available, clears on error, and re-raises cancel."""

    async def _run() -> None:
        from apme_engine.daemon.sinks.grpc_reporting import GrpcReportingSink

        sink = GrpcReportingSink("127.0.0.1:50051")
        sink._channel = MagicMock()
        sink._available = False
        with patch(
            "grpc_health.v1.health_pb2_grpc.HealthStub",
            return_value=MagicMock(Check=AsyncMock(return_value=MagicMock())),
        ):
            await sink._probe()
        assert sink._available is True

        sink._available = True
        with patch(
            "grpc_health.v1.health_pb2_grpc.HealthStub",
            return_value=MagicMock(Check=AsyncMock(side_effect=RuntimeError("down"))),
        ):
            await sink._probe()
        assert sink._available is False

        with (
            patch(
                "grpc_health.v1.health_pb2_grpc.HealthStub",
                return_value=MagicMock(Check=AsyncMock(side_effect=asyncio.CancelledError())),
            ),
            pytest.raises(asyncio.CancelledError),
        ):
            await sink._probe()

    asyncio.run(_run())


def test_grpc_sink_health_loop_probes_once() -> None:
    """Health loop sleeps then probes; cancellation stops it."""

    async def _run() -> None:
        from apme_engine.daemon.sinks.grpc_reporting import GrpcReportingSink

        sink = GrpcReportingSink("127.0.0.1:50051")
        calls: list[str] = []

        async def _fake_probe() -> None:
            calls.append("probe")
            raise asyncio.CancelledError

        sink._probe = _fake_probe  # type: ignore[method-assign]
        with (
            patch("apme_engine.daemon.sinks.grpc_reporting.asyncio.sleep", new=AsyncMock(return_value=None)),
            pytest.raises(asyncio.CancelledError),
        ):
            await sink._health_loop()
        assert calls == ["probe"]

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# ansible validator package
# ---------------------------------------------------------------------------


def test_ansible_extract_task_nodes_branches() -> None:
    """Non-taskcall shapes are skipped; taskcalls are collected."""
    from apme_engine.engine.models import YAMLDict
    from apme_engine.validators.ansible import _extract_task_nodes

    assert _extract_task_nodes(None) == []
    assert _extract_task_nodes({"hierarchy": "nope"}) == []
    assert _extract_task_nodes({"hierarchy": ["nope"]}) == []
    assert _extract_task_nodes({"hierarchy": [{"nodes": "nope"}]}) == []
    task: YAMLDict = {"type": "taskcall", "module": "debug"}
    other: YAMLDict = {"type": "play", "name": "p"}
    payload: YAMLDict = {"hierarchy": [{"nodes": [task, other, "str"]}]}
    assert _extract_task_nodes(payload) == [task]


def test_ansible_build_lookup_branches() -> None:
    """Empty, corrupt, and misshapen graph payloads yield empty lookup."""
    import json as _json

    from apme_engine.validators.ansible import build_node_lookup

    assert build_node_lookup(b"") == {}
    assert build_node_lookup(b"not-json{{{") == {}
    assert build_node_lookup(_json.dumps({"nodes": "nope"}).encode()) == {}
    assert build_node_lookup(_json.dumps({"nodes": ["x"]}).encode()) == {}
    assert build_node_lookup(_json.dumps({"nodes": [{"id": "", "data": {}}]}).encode()) == {}
    assert (
        build_node_lookup(
            _json.dumps({"nodes": [{"id": "n1", "data": {"file_path": "", "line_start": 1, "line_end": 2}}]}).encode()
        )
        == {}
    )
    assert (
        build_node_lookup(
            _json.dumps(
                {"nodes": [{"id": "n1", "data": {"file_path": "a.yml", "line_start": "x", "line_end": 2}}]}
            ).encode()
        )
        == {}
    )
    assert (
        build_node_lookup(
            _json.dumps(
                {"nodes": [{"id": "n1", "data": {"file_path": "a.yml", "line_start": 0, "line_end": 0}}]}
            ).encode()
        )
        == {}
    )


def test_ansible_build_lookup_sorts_ranges() -> None:
    """Valid nodes group by file and sort by start line."""
    import json as _json

    from apme_engine.validators.ansible import build_node_lookup

    data = {
        "nodes": [
            {"id": "n2", "data": {"file_path": "a.yml", "line_start": 10, "line_end": 12}},
            {"id": "n1", "data": {"file_path": "a.yml", "line_start": 1, "line_end": 5}},
        ]
    }
    lookup = build_node_lookup(_json.dumps(data).encode())
    assert [nid for _, _, nid in lookup["a.yml"]] == ["n1", "n2"]


def test_ansible_resolve_narrowest_and_miss() -> None:
    """Narrowest containing range wins; gaps and files miss."""
    from apme_engine.validators.ansible import resolve_file_line_to_node

    assert resolve_file_line_to_node({}, "a.yml", 3) == ""
    assert resolve_file_line_to_node({"b.yml": [(1, 5, "n1")]}, "a.yml", 3) == ""
    lookup = {"a.yml": [(1, 10, "wide"), (3, 4, "narrow"), (20, 30, "later")]}
    assert resolve_file_line_to_node(lookup, "a.yml", 3) == "narrow"
    assert resolve_file_line_to_node(lookup, "a.yml", 15) == ""
    assert resolve_file_line_to_node(lookup, "a.yml", 25) == "later"


def test_ansible_run_delegates_to_timing(tmp_path: Path) -> None:
    """run() returns violations from run_with_timing.

    Args:
        tmp_path: Pytest temporary directory.
    """
    from apme_engine.validators.ansible import AnsibleValidator
    from apme_engine.validators.base import ScanContext

    validator = AnsibleValidator(venv_root=tmp_path)
    with patch.object(AnsibleValidator, "run_with_timing") as mock_timed:
        mock_timed.return_value = MagicMock(violations=[{"rule_id": "L057"}])
        out = validator.run(ScanContext(hierarchy_payload={}, root_dir=""))
    assert out == [{"rule_id": "L057"}]


def test_ansible_run_timing_no_tasks_early_return(tmp_path: Path) -> None:
    """Missing root dir and no task nodes returns empty result.

    Args:
        tmp_path: Pytest temporary directory.
    """
    from apme_engine.validators.ansible import AnsibleValidator
    from apme_engine.validators.base import ScanContext

    validator = AnsibleValidator(venv_root=tmp_path)
    result = validator.run_with_timing(ScanContext(hierarchy_payload={}, root_dir=str(tmp_path / "nope")))
    assert result.violations == []
    assert result.rule_timings == []


def test_ansible_run_timing_l057_with_lookup(tmp_path: Path) -> None:
    """L057 violations resolve file/line to graph node ids.

    Args:
        tmp_path: Pytest temporary directory.
    """
    import json as _json

    from apme_engine.validators.ansible import AnsibleValidator
    from apme_engine.validators.base import ScanContext

    root = tmp_path / "proj"
    root.mkdir()
    (root / "play.yml").write_text("- hosts: all\n")
    graph = {"nodes": [{"id": "node-1", "data": {"file_path": "play.yml", "line_start": 1, "line_end": 3}}]}
    l057_viol = [{"rule_id": "L057", "file": "play.yml", "line": 2, "message": "bad"}]
    with (
        patch("apme_engine.validators.ansible.L057_syntax.run", return_value=l057_viol),
        patch("apme_engine.validators.ansible.M001_M004_introspect.run", return_value=[]),
        patch("apme_engine.validators.ansible.L058_argspec_doc.run", return_value=[]),
        patch("apme_engine.validators.ansible.L059_argspec_mock.run", return_value=[]),
    ):
        result = AnsibleValidator(venv_root=tmp_path).run_with_timing(
            ScanContext(hierarchy_payload={}, root_dir=str(root)),
            content_graph_data=_json.dumps(graph).encode(),
        )
    assert result.violations[0]["path"] == "node-1"
    assert any(t.rule_id == "L057" for t in result.rule_timings)


def test_ansible_run_timing_full_rules(tmp_path: Path) -> None:
    """Task nodes trigger M001-M004, L058, and L059 with cache stats.

    Args:
        tmp_path: Pytest temporary directory.
    """
    from apme_engine.engine.models import YAMLDict
    from apme_engine.validators.ansible import AnsibleValidator
    from apme_engine.validators.base import ScanContext

    root = tmp_path / "proj"
    root.mkdir()
    payload: YAMLDict = {"hierarchy": [{"nodes": [{"type": "taskcall", "module": "debug"}]}]}
    with (
        patch("apme_engine.validators.ansible.L057_syntax.run", return_value=[]),
        patch(
            "apme_engine.validators.ansible.M001_M004_introspect.run",
            return_value=[{"rule_id": "M001", "message": "m"}],
        ),
        patch("apme_engine.validators.ansible.L058_argspec_doc.run", return_value=[{"rule_id": "L058"}]),
        patch("apme_engine.validators.ansible.L059_argspec_mock.run", return_value=[{"rule_id": "L059"}]),
        patch("apme_engine.validators.ansible.plugin_cache.stats", return_value={"cache_introspect_hits": 1}),
    ):
        result = AnsibleValidator(venv_root=tmp_path).run_with_timing(
            ScanContext(hierarchy_payload=payload, root_dir=str(root))
        )
    assert len(result.violations) == 3
    assert {t.rule_id for t in result.rule_timings} >= {"M001-M004", "L058", "L059"}
    assert result.metadata == {"cache_introspect_hits": 1}


# ---------------------------------------------------------------------------
# ansible_validator_server
# ---------------------------------------------------------------------------


def test_ansible_server_run_no_venv(tmp_path: Path) -> None:
    """Empty venv_path yields an R901 violation.

    Args:
        tmp_path: Pytest temporary directory.
    """
    from apme_engine.daemon import ansible_validator_server as mod

    target = tmp_path / "t"
    target.mkdir()
    with patch.object(mod, "write_chunked_fs", return_value=target):
        result = mod._run_ansible_validate([], "2.20", {}, "req-1", "")
    assert result.run_result.violations[0]["rule_id"] == "R901"


def test_ansible_server_run_success(tmp_path: Path) -> None:
    """Validator result passes through with the requested version.

    Args:
        tmp_path: Pytest temporary directory.
    """
    from apme_engine.daemon import ansible_validator_server as mod
    from apme_engine.validators.ansible import AnsibleRunResult

    target = tmp_path / "t"
    target.mkdir()
    fake_result = AnsibleRunResult(violations=[{"rule_id": "L057"}], rule_timings=[], metadata={})
    with (
        patch.object(mod, "write_chunked_fs", return_value=target),
        patch.object(mod, "AnsibleValidator") as cls,
    ):
        cls.return_value.run_with_timing.return_value = fake_result
        result = mod._run_ansible_validate([], "2.20", {}, "req-1", "/venv", content_graph_data=b"{}")
    assert result.ansible_core_version == "2.20"
    assert result.run_result.violations == [{"rule_id": "L057"}]


def test_ansible_server_run_exception_yields_public_error(tmp_path: Path) -> None:
    """Blocking errors degrade to the public validator message.

    Args:
        tmp_path: Pytest temporary directory.
    """
    from apme_engine.daemon import ansible_validator_server as mod
    from apme_engine.daemon.validator_errors import PUBLIC_VALIDATOR_ERROR

    with patch.object(mod, "write_chunked_fs", side_effect=RuntimeError("disk gone")):
        result = mod._run_ansible_validate([], "2.20", {}, "req-1", "/venv")
    assert result.run_result.violations[0]["message"] == PUBLIC_VALIDATOR_ERROR


def test_ansible_server_validate_empty_returns_empty() -> None:
    """No files and no payload short-circuits to empty."""

    async def _run() -> None:
        from apme_engine.daemon.ansible_validator_server import AnsibleValidatorServicer

        req = validate_pb2.ValidateRequest(request_id="empty-1")
        resp = await AnsibleValidatorServicer().Validate(req, MagicMock())
        assert resp.violations == []  # type: ignore[attr-defined]
        assert resp.request_id == "empty-1"  # type: ignore[attr-defined]

    asyncio.run(_run())


def test_ansible_server_validate_bad_hierarchy_payload() -> None:
    """Unparseable hierarchy payload still validates files."""

    async def _run() -> None:
        from apme_engine.daemon import ansible_validator_server as mod
        from apme_engine.daemon.ansible_validator_server import AnsibleValidatorServicer
        from apme_engine.validators.ansible import AnsibleRunResult

        req = validate_pb2.ValidateRequest(
            request_id="bad-h",
            files=[common_pb2.File(path="a.yml", content=b"x\n")],
            hierarchy_payload=b"not-json",
            ansible_core_version="2.20",
        )
        fake = mod._AnsibleResult(
            run_result=AnsibleRunResult(violations=[], rule_timings=[], metadata={}), ansible_core_version="2.20"
        )
        with patch.object(mod, "_run_ansible_validate", return_value=fake):
            resp = await AnsibleValidatorServicer().Validate(req, MagicMock())
        assert resp.request_id == "bad-h"  # type: ignore[attr-defined]

    asyncio.run(_run())


def test_ansible_server_validate_metadata_and_source() -> None:
    """Metadata entries stringify and violations gain ansible source."""

    async def _run() -> None:
        from apme_engine.daemon import ansible_validator_server as mod
        from apme_engine.daemon.ansible_validator_server import AnsibleValidatorServicer
        from apme_engine.validators.ansible import AnsibleRuleTiming, AnsibleRunResult

        req = validate_pb2.ValidateRequest(
            request_id="meta-1",
            files=[common_pb2.File(path="a.yml", content=b"x\n")],
            hierarchy_payload=b"{}",
        )
        fake = mod._AnsibleResult(
            run_result=AnsibleRunResult(
                violations=[
                    {"rule_id": "L057", "severity": "error", "message": "m", "file": "a.yml", "line": 1, "path": ""}
                ],
                rule_timings=[AnsibleRuleTiming(rule_id="L057", elapsed_ms=1.0, violations=1)],
                metadata={"cache_introspect_hits": 2},
            ),
            ansible_core_version="2.20",
        )
        with patch.object(mod, "_run_ansible_validate", return_value=fake):
            resp = await AnsibleValidatorServicer().Validate(req, MagicMock())
        assert resp.diagnostics.metadata["cache_introspect_hits"] == "2"  # type: ignore[attr-defined]
        assert resp.diagnostics.metadata["ansible_core_version"] == "2.20"  # type: ignore[attr-defined]

    asyncio.run(_run())


def test_ansible_server_validate_exception_returns_infra() -> None:
    """Unhandled executor errors return the infra violation."""

    async def _run() -> None:
        from apme_engine.daemon import ansible_validator_server as mod
        from apme_engine.daemon.ansible_validator_server import AnsibleValidatorServicer
        from apme_engine.daemon.validator_errors import RULE_VALIDATOR_FAILURE

        req = validate_pb2.ValidateRequest(
            request_id="exc-1",
            files=[common_pb2.File(path="a.yml", content=b"x\n")],
            hierarchy_payload=b"{}",
        )
        with patch.object(mod, "_run_ansible_validate", side_effect=RuntimeError("boom")):
            resp = await AnsibleValidatorServicer().Validate(req, MagicMock())
        assert resp.violations[0].rule_id == RULE_VALIDATOR_FAILURE  # type: ignore[attr-defined]

    asyncio.run(_run())


def test_ansible_server_health_and_serve() -> None:
    """Health is ok and serve delegates to the shared starter."""

    async def _run() -> None:
        from apme_engine.daemon import ansible_validator_server as mod
        from apme_engine.daemon.ansible_validator_server import AnsibleValidatorServicer

        resp = await AnsibleValidatorServicer().Health(common_pb2.HealthRequest(), MagicMock())
        assert resp.status == "ok"
        sentinel = MagicMock()
        with patch(
            "apme_engine.daemon.validator_grpc.start_validator_server", new=AsyncMock(return_value=sentinel)
        ) as starter:
            out = await mod.serve("127.0.0.1:50053")
        assert out is sentinel
        assert starter.await_count == 1

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# session runtime (gaps 248-280, 344-383)
# ---------------------------------------------------------------------------


def test_session_start_operation_alias() -> None:
    """Deprecated start_operation delegates to begin_operation_phase."""
    state = SessionState(session_id="alias-1")
    state.start_operation(120)
    assert state.operation_budget_s == 120
    assert state.operation_started_at > 0
    assert state.operation_generation == 1


def test_session_record_progress_linked_and_unlinked() -> None:
    """Task-linked progress resets the stall clock; heartbeats do not."""
    state = SessionState(session_id="prog-1")
    state.begin_operation_phase(600)
    first = state.last_progress_at
    state.last_progress_at = first - 10.0
    state.record_progress(task_linked=True)
    assert state.last_progress_at > first - 10.0
    pinned = state.last_progress_at
    state.record_progress(task_linked=False)
    assert state.last_progress_at == pinned


def test_session_budget_remaining_unset_is_zero() -> None:
    """Zero budget or zero start returns zero remaining."""
    state = SessionState(session_id="budget-0")
    assert state.operation_budget_remaining() == 0
    state.operation_budget_s = 100
    assert state.operation_budget_remaining() == 0


def test_session_cleanup_galaxy_and_temp(tmp_path: Path) -> None:
    """Galaxy config parent and temp dir are both removed.

    Args:
        tmp_path: Pytest temporary directory.
    """
    state = SessionState(session_id="clean-1")
    cfg_dir = tmp_path / "galaxy"
    cfg_dir.mkdir()
    cfg = cfg_dir / "ansible.cfg"
    cfg.write_text("[galaxy]\n")
    temp = tmp_path / "work"
    temp.mkdir()
    state.galaxy_cfg_path = cfg
    state.temp_dir = temp
    state.cleanup()
    assert not cfg_dir.exists()
    assert not temp.exists()
    assert state.galaxy_cfg_path is None
    assert state.temp_dir is None


def test_session_store_touch_missing_is_noop() -> None:
    """Touching an unknown session does not raise."""
    store = SessionStore()
    store.touch("missing")
    assert store.count == 0


def test_session_store_remove_unknown_is_false() -> None:
    """Removing an unknown session returns False."""
    assert SessionStore().remove("missing") is False


def test_session_store_reaper_lifecycle() -> None:
    """start/stop manage the background task without leaks."""

    async def _run() -> None:
        store = SessionStore()
        store.start_reaper()
        assert store._reaper_task is not None
        store.start_reaper()
        assert store._reaper_task is not None
        store.stop_reaper()
        assert store._reaper_task is None
        store.stop_reaper()
        assert store._reaper_task is None

    asyncio.run(_run())


def test_session_store_reap_loop_removes_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reap loop evicts expired sessions on its first sweep.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import session as session_mod

    async def _run() -> None:
        monkeypatch.setattr(session_mod, "_REAP_INTERVAL", 0.01)
        store = SessionStore()
        stale = store.create()
        stale.last_activity_at = datetime.now(UTC) - timedelta(seconds=session_mod._DEFAULT_TTL + 5)
        live = store.create()
        assert store.count == 2
        store.start_reaper()
        for _ in range(100):
            await asyncio.sleep(0.02)
            if store.count == 1:
                break
        assert store.get(live.session_id) is not None
        assert store.count == 1
        store.stop_reaper()
        await asyncio.sleep(0)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# collection_health + dep_audit servers
# ---------------------------------------------------------------------------


def test_collection_health_run_scan_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Outside-root and non-dir venvs short-circuit; valid venv scans.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import collection_health_server as mod

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setattr(mod, "_SESSIONS_ROOT", sessions)

    assert mod._run_scan("/etc/passwd", False) == []
    assert mod._run_scan(str(sessions / "missing"), False) == []

    venv = sessions / "v1"
    venv.mkdir()
    with patch.object(mod, "scan_collections", return_value=[{"rule_id": "X"}]) as scan_mock:
        out = mod._run_scan(str(venv), True)
    assert out == [{"rule_id": "X"}]
    scan_mock.assert_called_once()


def test_collection_health_validate_no_venv() -> None:
    """Empty venv_path returns empty violations."""

    async def _run() -> None:
        from apme_engine.daemon.collection_health_server import CollectionHealthValidatorServicer

        req = validate_pb2.ValidateRequest(request_id="ch-empty")
        resp = await CollectionHealthValidatorServicer().Validate(req, MagicMock())
        assert resp.violations == []  # type: ignore[attr-defined]

    asyncio.run(_run())


def test_collection_health_validate_success() -> None:
    """Scan findings convert to protos with diagnostics."""

    async def _run() -> None:
        from apme_engine.daemon import collection_health_server as mod
        from apme_engine.daemon.collection_health_server import CollectionHealthValidatorServicer

        req = validate_pb2.ValidateRequest(request_id="ch-1", venv_path="/sessions/v1")
        viol = {"rule_id": "L001", "severity": "low", "message": "m", "file": "c.yml", "line": 1, "path": ""}
        with patch.object(mod, "_run_scan", return_value=[viol]):
            resp = await CollectionHealthValidatorServicer().Validate(req, MagicMock())
        assert len(resp.violations) == 1  # type: ignore[attr-defined]
        assert resp.diagnostics.validator_name == "collection_health"  # type: ignore[attr-defined]

    asyncio.run(_run())


def test_collection_health_validate_exception_returns_infra() -> None:
    """Unexpected scan errors return the infra violation."""

    async def _run() -> None:
        from apme_engine.daemon import collection_health_server as mod
        from apme_engine.daemon.collection_health_server import CollectionHealthValidatorServicer
        from apme_engine.daemon.validator_errors import RULE_VALIDATOR_FAILURE

        req = validate_pb2.ValidateRequest(request_id="ch-exc", venv_path="/sessions/v1")
        with patch.object(mod, "_run_scan", side_effect=RuntimeError("boom")):
            resp = await CollectionHealthValidatorServicer().Validate(req, MagicMock())
        assert resp.violations[0].rule_id == RULE_VALIDATOR_FAILURE  # type: ignore[attr-defined]

    asyncio.run(_run())


def test_collection_health_health_and_serve() -> None:
    """Health is ok and serve delegates to the shared starter."""

    async def _run() -> None:
        from apme_engine.daemon import collection_health_server as mod
        from apme_engine.daemon.collection_health_server import CollectionHealthValidatorServicer

        resp = await CollectionHealthValidatorServicer().Health(common_pb2.HealthRequest(), MagicMock())
        assert resp.status == "ok"
        sentinel = MagicMock()
        with patch("apme_engine.daemon.validator_grpc.start_validator_server", new=AsyncMock(return_value=sentinel)):
            assert await mod.serve("127.0.0.1:50058") is sentinel

    asyncio.run(_run())


def test_dep_audit_run_audit_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Outside-root and non-dir venvs short-circuit; valid venv audits.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import dep_audit_server as mod

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setattr(mod, "_SESSIONS_ROOT", sessions)

    assert mod._run_audit("/etc/passwd") == []
    assert mod._run_audit(str(sessions / "missing")) == []

    venv = sessions / "v1"
    venv.mkdir()
    with patch.object(mod, "run_pip_audit", return_value=[{"rule_id": "R200"}]) as audit_mock:
        assert mod._run_audit(str(venv)) == [{"rule_id": "R200"}]
    audit_mock.assert_called_once()


def test_dep_audit_validate_no_venv() -> None:
    """Empty venv_path returns empty violations."""

    async def _run() -> None:
        from apme_engine.daemon.dep_audit_server import DepAuditValidatorServicer

        req = validate_pb2.ValidateRequest(request_id="dep-empty")
        resp = await DepAuditValidatorServicer().Validate(req, MagicMock())
        assert resp.violations == []  # type: ignore[attr-defined]

    asyncio.run(_run())


def test_dep_audit_validate_success() -> None:
    """Audit findings convert to protos with diagnostics."""

    async def _run() -> None:
        from apme_engine.daemon import dep_audit_server as mod
        from apme_engine.daemon.dep_audit_server import DepAuditValidatorServicer

        req = validate_pb2.ValidateRequest(request_id="dep-1", venv_path="/sessions/v1")
        viol = {"rule_id": "R200", "severity": "high", "message": "cve", "file": "", "line": 1, "path": ""}
        with patch.object(mod, "_run_audit", return_value=[viol]):
            resp = await DepAuditValidatorServicer().Validate(req, MagicMock())
        assert len(resp.violations) == 1  # type: ignore[attr-defined]
        assert resp.diagnostics.validator_name == "dep_audit"  # type: ignore[attr-defined]

    asyncio.run(_run())


def test_dep_audit_validate_exception_returns_infra() -> None:
    """Unexpected audit errors return the infra violation."""

    async def _run() -> None:
        from apme_engine.daemon import dep_audit_server as mod
        from apme_engine.daemon.dep_audit_server import DepAuditValidatorServicer
        from apme_engine.daemon.validator_errors import RULE_VALIDATOR_FAILURE

        req = validate_pb2.ValidateRequest(request_id="dep-exc", venv_path="/sessions/v1")
        with patch.object(mod, "_run_audit", side_effect=RuntimeError("boom")):
            resp = await DepAuditValidatorServicer().Validate(req, MagicMock())
        assert resp.violations[0].rule_id == RULE_VALIDATOR_FAILURE  # type: ignore[attr-defined]

    asyncio.run(_run())


def test_dep_audit_health_available_and_missing() -> None:
    """Health reports pip-audit availability with version or error."""

    async def _run() -> None:
        from apme_engine.daemon import dep_audit_server as mod
        from apme_engine.daemon.dep_audit_server import DepAuditValidatorServicer

        with patch.object(mod, "pip_audit_available", return_value=(True, "25.0")):
            resp = await DepAuditValidatorServicer().Health(common_pb2.HealthRequest(), MagicMock())
        assert resp.status.startswith("ok")

        with patch.object(mod, "pip_audit_available", return_value=(False, "not found")):
            resp2 = await DepAuditValidatorServicer().Health(common_pb2.HealthRequest(), MagicMock())
        assert "not available" in resp2.status

        sentinel = MagicMock()
        with patch("apme_engine.daemon.validator_grpc.start_validator_server", new=AsyncMock(return_value=sentinel)):
            assert await mod.serve("127.0.0.1:50059") is sentinel

    asyncio.run(_run())


def test_launcher_save_load_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """DaemonState.save persists fields that load restores.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    data_dir = tmp_path / "data"
    monkeypatch.setattr(launcher, "_DATA_DIR", data_dir)
    monkeypatch.setattr(launcher, "_STATE_FILE", data_dir / "daemon.json")
    monkeypatch.setattr(launcher, "_MARKER_FILE", data_dir / "daemon.marker")
    state = launcher.DaemonState(
        pid=4242,
        engine="127.0.0.1:50051",
        version="9.9.9",
        started_at=datetime.now(UTC).isoformat(),
        services={"engine": "127.0.0.1:50051"},
    )
    state.save()
    loaded = launcher.DaemonState.load()
    assert loaded is not None
    assert loaded.pid == 4242
    assert loaded.services == {"engine": "127.0.0.1:50051"}
    launcher.DaemonState.remove()
    assert launcher.DaemonState.load() is None


def test_launcher_run_daemon_engine_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Engine-only services skip every validator and proxy branch.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    import apme_engine.daemon.launcher as launcher

    engine_server = MagicMock()
    engine_server.wait_for_termination = AsyncMock(return_value=None)
    monkeypatch.setattr("apme_engine.log_bridge.install_handler", lambda: None)

    async def _fake_engine(_addr: str) -> MagicMock:
        return engine_server

    monkeypatch.setattr("apme_engine.daemon.engine_server.serve", _fake_engine)
    old_env = dict(os.environ)
    try:
        asyncio.run(launcher._run_daemon({"engine": "127.0.0.1:50051"}))
    finally:
        os.environ.clear()
        os.environ.update(old_env)


def test_launcher_start_includes_optional_ports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """include_optional adds gitleaks/collection/dep-audit ports.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(launcher, "_DATA_DIR", data_dir)
    monkeypatch.setattr(launcher, "_STATE_FILE", data_dir / "daemon.json")
    monkeypatch.setattr(launcher, "_MARKER_FILE", data_dir / "daemon.marker")
    monkeypatch.setattr(launcher, "_HEALTH_POLL_INTERVAL", 0.001)
    seen: dict[str, dict[str, int]] = {}
    healthy = {
        "engine": {"ok": True},
        "native": {"ok": True},
        "opa": {"ok": True},
        "ansible": {"ok": True},
        "galaxy_proxy": {"ok": True},
    }

    def _capture(_host: str, ports: dict[str, int]) -> None:
        seen["ports"] = dict(ports)

    with (
        patch.object(launcher, "_require_proc_identity"),
        patch.object(launcher, "_daemon_status_unlocked", return_value=None),
        patch.object(launcher, "_assert_ports_free", side_effect=_capture),
        patch("apme_engine.daemon.launcher.os.fork", return_value=4242),
        patch.object(launcher, "_write_daemon_marker"),
        patch.object(launcher.DaemonState, "save"),
        patch("apme_engine.daemon.health_check.run_health_checks", return_value=healthy),
    ):
        state = launcher._start_daemon_unlocked(include_optional=True)
    assert state.pid == 4242
    assert "gitleaks" in seen["ports"]
    assert "dep_audit" in seen["ports"]


def test_launcher_stop_daemon_wrapper_no_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """stop_daemon wrapper returns False when no daemon is recorded.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(launcher, "_DATA_DIR", data_dir)
    monkeypatch.setattr(launcher, "_STATE_FILE", data_dir / "daemon.json")
    monkeypatch.setattr(launcher, "_MARKER_FILE", data_dir / "daemon.marker")
    assert launcher.stop_daemon() is False


def test_launcher_start_poll_retries_then_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Poll loop sleeps between an unhealthy and a healthy probe.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(launcher, "_DATA_DIR", data_dir)
    monkeypatch.setattr(launcher, "_STATE_FILE", data_dir / "daemon.json")
    monkeypatch.setattr(launcher, "_MARKER_FILE", data_dir / "daemon.marker")
    bad = {
        "engine": {"ok": False},
        "native": {"ok": True},
        "opa": {"ok": True},
        "ansible": {"ok": True},
        "galaxy_proxy": {"ok": True},
    }
    good = {
        "engine": {"ok": True},
        "native": {"ok": True},
        "opa": {"ok": True},
        "ansible": {"ok": True},
        "galaxy_proxy": {"ok": True},
    }
    with (
        patch.object(launcher, "_require_proc_identity"),
        patch.object(launcher, "_daemon_status_unlocked", return_value=None),
        patch.object(launcher, "_assert_ports_free"),
        patch("apme_engine.daemon.launcher.os.fork", return_value=4242),
        patch.object(launcher, "_write_daemon_marker"),
        patch.object(launcher.DaemonState, "save"),
        patch("apme_engine.daemon.health_check.run_health_checks", side_effect=[bad, good]),
        patch.object(launcher, "_pid_alive", return_value=True),
        patch("apme_engine.daemon.launcher.time.sleep", return_value=None) as sleep_mock,
    ):
        state = launcher._start_daemon_unlocked()
    assert state.pid == 4242
    sleep_mock.assert_called()


def test_chunked_build_bundle_read_error_skipped(tmp_path: Path) -> None:
    """Unreadable files are skipped without failing the bundle.

    Args:
        tmp_path: Pytest temporary directory.
    """
    from apme_engine.daemon.chunked_fs import build_scan_bundle

    target = tmp_path / "play.yml"
    target.write_text("- hosts: all\n")
    with patch.object(Path, "read_bytes", side_effect=OSError("denied")):
        bundle = build_scan_bundle(target, scan_id="r1")
    assert bundle.files == []


def test_chunked_build_bundle_skips_non_files(tmp_path: Path) -> None:
    """Dangling symlinks in the walk are skipped.

    Args:
        tmp_path: Pytest temporary directory.
    """
    from apme_engine.daemon.chunked_fs import build_scan_bundle

    root = tmp_path / "proj"
    root.mkdir()
    (root / "play.yml").write_text("- hosts: all\n")
    os.symlink(root / "does-not-exist.yml", root / "dangling.yml")
    bundle = build_scan_bundle(root, scan_id="d1")
    assert sorted(f.path for f in bundle.files) == ["play.yml"]


def test_session_reanchor_lifetime_deadline() -> None:
    """Re-anchor preserves remaining lifetime from creation age."""
    import time as _time

    from apme_engine.daemon import session as session_mod

    state = SessionState(session_id="reanchor-1")
    state.created_at = datetime.now(UTC) - timedelta(seconds=100)
    before = _time.monotonic()
    state.reanchor_lifetime_deadline()
    remaining = session_mod._MAX_LIFETIME - 100
    assert before + remaining - 5 <= state.max_lifetime_deadline_mono <= before + remaining + 5


def test_health_stub_protocol_body_executes() -> None:
    """Calling the Protocol method itself covers its placeholder body."""
    from typing import cast

    from apme_engine.daemon.health_check import _HealthStub

    result = _HealthStub.Health(cast(_HealthStub, MagicMock()), object(), timeout=1.0)
    assert result is None


def test_launcher_stop_breaks_when_child_dies_in_grace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Child dying during the grace loop avoids SIGKILL.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.daemon import launcher

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    state_file = data_dir / "daemon.json"
    state_file.write_text(
        json.dumps(
            {"pid": 4242, "engine": "127.0.0.1:50051", "version": "v", "started_at": datetime.now(UTC).isoformat()}
        )
    )
    monkeypatch.setattr(launcher, "_DATA_DIR", data_dir)
    monkeypatch.setattr(launcher, "_STATE_FILE", state_file)
    monkeypatch.setattr(launcher, "_MARKER_FILE", data_dir / "daemon.marker")
    kills: list[int] = []
    with (
        patch.object(launcher, "_verify_daemon_ownership", return_value=True),
        patch.object(launcher, "_pid_alive", side_effect=[True, False]),
        patch("apme_engine.daemon.launcher.os.kill", side_effect=lambda pid, sig: kills.append(int(sig))),
        patch("apme_engine.daemon.launcher.time.sleep", return_value=None),
    ):
        assert launcher._stop_daemon_unlocked() is True
    assert kills == [int(signal.SIGTERM)]


def test_grpc_sink_probe_state_combos() -> None:
    """Probe covers already-available success and already-down failure."""

    async def _run() -> None:
        from apme_engine.daemon.sinks.grpc_reporting import GrpcReportingSink

        sink = GrpcReportingSink("127.0.0.1:50051")
        sink._channel = MagicMock()
        sink._available = True
        with patch(
            "grpc_health.v1.health_pb2_grpc.HealthStub",
            return_value=MagicMock(Check=AsyncMock(return_value=MagicMock())),
        ):
            await sink._probe()
        assert sink._available is True

        sink._available = False
        with patch(
            "grpc_health.v1.health_pb2_grpc.HealthStub",
            return_value=MagicMock(Check=AsyncMock(side_effect=RuntimeError("down"))),
        ):
            await sink._probe()
        assert sink._available is False

    asyncio.run(_run())


def test_grpc_sink_emit_when_already_available() -> None:
    """Delivery on a healthy endpoint skips the recovery log branch."""

    async def _run() -> None:
        from apme.v1 import reporting_pb2
        from apme_engine.daemon.sinks.grpc_reporting import GrpcReportingSink

        sink = GrpcReportingSink("127.0.0.1:50051")
        stub = MagicMock()
        stub.ReportFixCompleted = AsyncMock(return_value=MagicMock())
        stub.RegisterRules = AsyncMock(return_value=MagicMock())
        sink._stub = stub
        sink._available = True
        await sink.on_fix_completed(reporting_pb2.FixCompletedEvent(scan_id="s"))
        assert sink._available is True
        resp = await sink.register_rules(reporting_pb2.RegisterRulesRequest())
        assert resp is not None
        assert sink._available is True

    asyncio.run(_run())


def test_ansible_resolve_wider_later_keeps_first() -> None:
    """A later containing range with a wider span does not replace best."""
    from apme_engine.validators.ansible import resolve_file_line_to_node

    lookup = {"a.yml": [(1, 10, "wide"), (2, 20, "wider")]}
    assert resolve_file_line_to_node(lookup, "a.yml", 5) == "wide"


def test_ansible_run_timing_l057_unresolvable_violations(tmp_path: Path) -> None:
    """L057 rows without file/line or outside the graph keep no path.

    Args:
        tmp_path: Pytest temporary directory.
    """
    import json as _json

    from apme_engine.validators.ansible import AnsibleValidator
    from apme_engine.validators.base import ScanContext

    root = tmp_path / "proj"
    root.mkdir()
    (root / "play.yml").write_text("- hosts: all\n")
    graph = {"nodes": [{"id": "node-1", "data": {"file_path": "play.yml", "line_start": 1, "line_end": 3}}]}
    l057_viol = [
        {"rule_id": "L057", "message": "no location"},
        {"rule_id": "L057", "file": "missing.yml", "line": 99, "message": "unknown file"},
        {"rule_id": "L057", "file": "play.yml", "line": 0, "message": "no line"},
    ]
    with (
        patch("apme_engine.validators.ansible.L057_syntax.run", return_value=l057_viol),
        patch("apme_engine.validators.ansible.M001_M004_introspect.run", return_value=[]),
        patch("apme_engine.validators.ansible.L058_argspec_doc.run", return_value=[]),
        patch("apme_engine.validators.ansible.L059_argspec_mock.run", return_value=[]),
    ):
        result = AnsibleValidator(venv_root=tmp_path).run_with_timing(
            ScanContext(hierarchy_payload={}, root_dir=str(root)),
            content_graph_data=_json.dumps(graph).encode(),
        )
    assert len(result.violations) == 3
    assert all("path" not in v for v in result.violations)
