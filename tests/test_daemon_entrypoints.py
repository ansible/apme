"""Unit tests for daemon entrypoints, validator gRPC helper, and CLI wrappers."""

from __future__ import annotations

import argparse
import asyncio
import json
import runpy
from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import grpc
import pytest
from pytest import CaptureFixture, MonkeyPatch

from apme_engine.cli.daemon_cmd import run_daemon
from apme_engine.cli.health import run_health_check
from apme_engine.daemon import (
    ansible_validator_main,
    collection_health_main,
    dep_audit_main,
    engine_main,
    gitleaks_validator_main,
    native_validator_main,
    opa_validator_main,
    validator_grpc,
)
from apme_engine.daemon.launcher import DaemonState


def _make_server() -> MagicMock:
    """Create a fake gRPC server with an awaitable termination method.

    Returns:
        Fake server mock with ``wait_for_termination`` as AsyncMock.
    """
    server: MagicMock = MagicMock()
    server.wait_for_termination = AsyncMock(return_value=None)
    return server


def _daemon_state(
    pid: int = 4242,
    engine: str = "127.0.0.1:50051",
    version: str = "0.1.0",
    started_at: str = "2026-01-01T00:00:00+00:00",
    services: dict[str, str] | None = None,
) -> DaemonState:
    """Build a DaemonState with sensible defaults.

    Args:
        pid: Daemon process ID.
        engine: Engine gRPC address.
        version: Installed version string.
        started_at: ISO-format start timestamp.
        services: Service name to address map.

    Returns:
        DaemonState instance for daemon_cmd tests.

    """
    return DaemonState(
        pid=pid,
        engine=engine,
        version=version,
        started_at=started_at,
        services=dict(services) if services is not None else {"engine": "127.0.0.1:50051"},
    )


def _health_args(**overrides: object) -> argparse.Namespace:
    """Build CLI args for the health-check subcommand.

    Args:
        **overrides: Argument overrides.

    Returns:
        Parsed-args namespace for ``run_health_check``.

    """
    defaults: dict[str, object] = {"json": False, "timeout": 5.0}
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestDaemonRun:
    """Tests for each daemon ``_run`` coroutine (serve + wait, no real bind)."""

    def test_ansible_run_starts_server(self, capsys: CaptureFixture[str]) -> None:
        """Ansible _run serves the listen address and waits.

        Args:
            capsys: Pytest capture fixture.
        """
        server = _make_server()
        with patch(
            "apme_engine.daemon.ansible_validator_main.serve",
            new=AsyncMock(return_value=server),
        ) as mock_serve:
            asyncio.run(ansible_validator_main._run("127.0.0.1:50053"))
        mock_serve.assert_called_once_with("127.0.0.1:50053")
        server.wait_for_termination.assert_awaited_once()
        assert "Ansible validator listening on 127.0.0.1:50053" in capsys.readouterr().err

    def test_collection_health_run_starts_server(self, capsys: CaptureFixture[str]) -> None:
        """Collection health _run serves the listen address and waits.

        Args:
            capsys: Pytest capture fixture.
        """
        server = _make_server()
        with patch(
            "apme_engine.daemon.collection_health_main.serve",
            new=AsyncMock(return_value=server),
        ) as mock_serve:
            asyncio.run(collection_health_main._run("127.0.0.1:50058"))
        mock_serve.assert_called_once_with("127.0.0.1:50058")
        server.wait_for_termination.assert_awaited_once()
        assert "Collection health validator listening on 127.0.0.1:50058" in capsys.readouterr().err

    def test_dep_audit_run_starts_server(self, capsys: CaptureFixture[str]) -> None:
        """Dep audit _run serves the listen address and waits.

        Args:
            capsys: Pytest capture fixture.
        """
        server = _make_server()
        with patch(
            "apme_engine.daemon.dep_audit_main.serve",
            new=AsyncMock(return_value=server),
        ) as mock_serve:
            asyncio.run(dep_audit_main._run("127.0.0.1:50059"))
        mock_serve.assert_called_once_with("127.0.0.1:50059")
        server.wait_for_termination.assert_awaited_once()
        assert "Dep audit validator listening on 127.0.0.1:50059" in capsys.readouterr().err

    def test_engine_run_starts_server_and_stops_sinks(self, capsys: CaptureFixture[str]) -> None:
        """Engine _run waits then stops event sinks on success.

        Args:
            capsys: Pytest capture fixture.
        """
        server = _make_server()
        with (
            patch(
                "apme_engine.daemon.engine_main.serve",
                new=AsyncMock(return_value=server),
            ) as mock_serve,
            patch(
                "apme_engine.daemon.engine_main.stop_sinks",
                new=AsyncMock(return_value=None),
            ) as mock_stop,
        ):
            asyncio.run(engine_main._run("127.0.0.1:50051"))
        mock_serve.assert_called_once_with("127.0.0.1:50051")
        server.wait_for_termination.assert_awaited_once()
        mock_stop.assert_awaited_once()
        assert "Engine daemon listening on 127.0.0.1:50051" in capsys.readouterr().err

    def test_engine_run_stops_sinks_on_failure(self) -> None:
        """Engine _run still stops sinks when termination waiting raises."""
        server: MagicMock = MagicMock()
        server.wait_for_termination = AsyncMock(side_effect=RuntimeError("boom"))
        with (
            patch(
                "apme_engine.daemon.engine_main.serve",
                new=AsyncMock(return_value=server),
            ),
            patch(
                "apme_engine.daemon.engine_main.stop_sinks",
                new=AsyncMock(return_value=None),
            ) as mock_stop,
            pytest.raises(RuntimeError, match="boom"),
        ):
            asyncio.run(engine_main._run("127.0.0.1:50051"))
        mock_stop.assert_awaited_once()

    def test_gitleaks_run_starts_server(self, capsys: CaptureFixture[str]) -> None:
        """Gitleaks _run serves the listen address and waits.

        Args:
            capsys: Pytest capture fixture.
        """
        server = _make_server()
        with patch(
            "apme_engine.daemon.gitleaks_validator_main.serve",
            new=AsyncMock(return_value=server),
        ) as mock_serve:
            asyncio.run(gitleaks_validator_main._run("127.0.0.1:50056"))
        mock_serve.assert_called_once_with("127.0.0.1:50056")
        server.wait_for_termination.assert_awaited_once()
        assert "Gitleaks validator listening on 127.0.0.1:50056" in capsys.readouterr().err

    def test_native_run_starts_server(self, capsys: CaptureFixture[str]) -> None:
        """Native _run serves the listen address and waits.

        Args:
            capsys: Pytest capture fixture.
        """
        server = _make_server()
        with patch(
            "apme_engine.daemon.native_validator_main.serve",
            new=AsyncMock(return_value=server),
        ) as mock_serve:
            asyncio.run(native_validator_main._run("127.0.0.1:50055"))
        mock_serve.assert_called_once_with("127.0.0.1:50055")
        server.wait_for_termination.assert_awaited_once()
        assert "Native validator listening on 127.0.0.1:50055" in capsys.readouterr().err

    def test_opa_run_starts_server(self, capsys: CaptureFixture[str]) -> None:
        """OPA _run serves the listen address and waits.

        Args:
            capsys: Pytest capture fixture.
        """
        server = _make_server()
        with patch(
            "apme_engine.daemon.opa_validator_main.serve",
            new=AsyncMock(return_value=server),
        ) as mock_serve:
            asyncio.run(opa_validator_main._run("127.0.0.1:50054"))
        mock_serve.assert_called_once_with("127.0.0.1:50054")
        server.wait_for_termination.assert_awaited_once()
        assert "OPA validator (gRPC wrapper) listening on 127.0.0.1:50054" in capsys.readouterr().err


class TestDaemonMainSuccess:
    """Tests for each daemon ``main`` happy path (env parsing + lifecycle hooks)."""

    def test_ansible_main_uses_default_listen(self, monkeypatch: MonkeyPatch) -> None:
        """Ansible main falls back to the default bind address.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.delenv("APME_ANSIBLE_VALIDATOR_LISTEN", raising=False)
        with (
            patch(
                "apme_engine.daemon.ansible_validator_main._run",
                new=AsyncMock(return_value=None),
            ) as mock_run,
            patch("apme_engine.log_bridge.install_handler") as mock_install,
            patch("apme_engine.observability.setup_otel") as mock_setup,
            patch("apme_engine.observability.shutdown_otel") as mock_shutdown,
        ):
            ansible_validator_main.main()
        mock_install.assert_called_once()
        mock_setup.assert_called_once()
        mock_run.assert_called_once_with("0.0.0.0:50053")
        mock_shutdown.assert_called_once()

    def test_collection_health_main_uses_default_listen(self, monkeypatch: MonkeyPatch) -> None:
        """Collection health main falls back to the default bind address.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.delenv("APME_COLLECTION_HEALTH_VALIDATOR_LISTEN", raising=False)
        with (
            patch(
                "apme_engine.daemon.collection_health_main._run",
                new=AsyncMock(return_value=None),
            ) as mock_run,
            patch("apme_engine.log_bridge.install_handler") as mock_install,
            patch("apme_engine.observability.setup_otel") as mock_setup,
            patch("apme_engine.observability.shutdown_otel") as mock_shutdown,
        ):
            collection_health_main.main()
        mock_install.assert_called_once()
        mock_setup.assert_called_once()
        mock_run.assert_called_once_with("0.0.0.0:50058")
        mock_shutdown.assert_called_once()

    def test_dep_audit_main_uses_default_listen(self, monkeypatch: MonkeyPatch) -> None:
        """Dep audit main falls back to the default bind address.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.delenv("APME_DEP_AUDIT_VALIDATOR_LISTEN", raising=False)
        with (
            patch(
                "apme_engine.daemon.dep_audit_main._run",
                new=AsyncMock(return_value=None),
            ) as mock_run,
            patch("apme_engine.log_bridge.install_handler") as mock_install,
            patch("apme_engine.observability.setup_otel") as mock_setup,
            patch("apme_engine.observability.shutdown_otel") as mock_shutdown,
        ):
            dep_audit_main.main()
        mock_install.assert_called_once()
        mock_setup.assert_called_once()
        mock_run.assert_called_once_with("0.0.0.0:50059")
        mock_shutdown.assert_called_once()

    def test_engine_main_uses_default_listen(self, monkeypatch: MonkeyPatch) -> None:
        """Engine main falls back to the default bind address.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.delenv("APME_ENGINE_LISTEN", raising=False)
        with (
            patch(
                "apme_engine.daemon.engine_main._run",
                new=AsyncMock(return_value=None),
            ) as mock_run,
            patch("apme_engine.log_bridge.install_handler") as mock_install,
            patch("apme_engine.observability.setup_otel") as mock_setup,
            patch("apme_engine.observability.shutdown_otel") as mock_shutdown,
        ):
            engine_main.main()
        mock_install.assert_called_once()
        mock_setup.assert_called_once()
        mock_run.assert_called_once_with("0.0.0.0:50051")
        mock_shutdown.assert_called_once()

    def test_gitleaks_main_uses_default_listen(self, monkeypatch: MonkeyPatch) -> None:
        """Gitleaks main falls back to the default bind address.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.delenv("APME_GITLEAKS_VALIDATOR_LISTEN", raising=False)
        with (
            patch(
                "apme_engine.daemon.gitleaks_validator_main._run",
                new=AsyncMock(return_value=None),
            ) as mock_run,
            patch("apme_engine.log_bridge.install_handler") as mock_install,
            patch("apme_engine.observability.setup_otel") as mock_setup,
            patch("apme_engine.observability.shutdown_otel") as mock_shutdown,
        ):
            gitleaks_validator_main.main()
        mock_install.assert_called_once()
        mock_setup.assert_called_once()
        mock_run.assert_called_once_with("0.0.0.0:50056")
        mock_shutdown.assert_called_once()

    def test_native_main_uses_default_listen(self, monkeypatch: MonkeyPatch) -> None:
        """Native main falls back to the default bind address.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.delenv("APME_NATIVE_VALIDATOR_LISTEN", raising=False)
        with (
            patch(
                "apme_engine.daemon.native_validator_main._run",
                new=AsyncMock(return_value=None),
            ) as mock_run,
            patch("apme_engine.log_bridge.install_handler") as mock_install,
            patch("apme_engine.observability.setup_otel") as mock_setup,
            patch("apme_engine.observability.shutdown_otel") as mock_shutdown,
        ):
            native_validator_main.main()
        mock_install.assert_called_once()
        mock_setup.assert_called_once()
        mock_run.assert_called_once_with("0.0.0.0:50055")
        mock_shutdown.assert_called_once()

    def test_opa_main_uses_default_listen(self, monkeypatch: MonkeyPatch) -> None:
        """OPA main falls back to the default bind address.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.delenv("APME_OPA_VALIDATOR_LISTEN", raising=False)
        with (
            patch(
                "apme_engine.daemon.opa_validator_main._run",
                new=AsyncMock(return_value=None),
            ) as mock_run,
            patch("apme_engine.log_bridge.install_handler") as mock_install,
            patch("apme_engine.observability.setup_otel") as mock_setup,
            patch("apme_engine.observability.shutdown_otel") as mock_shutdown,
        ):
            opa_validator_main.main()
        mock_install.assert_called_once()
        mock_setup.assert_called_once()
        mock_run.assert_called_once_with("0.0.0.0:50054")
        mock_shutdown.assert_called_once()

    def test_all_mains_honor_custom_listen_env(self, monkeypatch: MonkeyPatch) -> None:
        """Each main passes its env-configured address through to _run.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        cases: list[tuple[str, str, Callable[[], None], str]] = [
            (
                "apme_engine.daemon.ansible_validator_main._run",
                "APME_ANSIBLE_VALIDATOR_LISTEN",
                ansible_validator_main.main,
                "127.0.0.1:59901",
            ),
            (
                "apme_engine.daemon.collection_health_main._run",
                "APME_COLLECTION_HEALTH_VALIDATOR_LISTEN",
                collection_health_main.main,
                "127.0.0.1:59902",
            ),
            (
                "apme_engine.daemon.dep_audit_main._run",
                "APME_DEP_AUDIT_VALIDATOR_LISTEN",
                dep_audit_main.main,
                "127.0.0.1:59903",
            ),
            (
                "apme_engine.daemon.engine_main._run",
                "APME_ENGINE_LISTEN",
                engine_main.main,
                "127.0.0.1:59904",
            ),
            (
                "apme_engine.daemon.gitleaks_validator_main._run",
                "APME_GITLEAKS_VALIDATOR_LISTEN",
                gitleaks_validator_main.main,
                "127.0.0.1:59905",
            ),
            (
                "apme_engine.daemon.native_validator_main._run",
                "APME_NATIVE_VALIDATOR_LISTEN",
                native_validator_main.main,
                "127.0.0.1:59906",
            ),
            (
                "apme_engine.daemon.opa_validator_main._run",
                "APME_OPA_VALIDATOR_LISTEN",
                opa_validator_main.main,
                "127.0.0.1:59907",
            ),
        ]
        for target, env_var, main_fn, custom in cases:
            monkeypatch.setenv(env_var, custom)
            try:
                with (
                    patch(target, new=AsyncMock(return_value=None)) as mock_run,
                    patch("apme_engine.log_bridge.install_handler"),
                    patch("apme_engine.observability.setup_otel"),
                    patch("apme_engine.observability.shutdown_otel"),
                ):
                    main_fn()
                mock_run.assert_called_once_with(custom)
            finally:
                monkeypatch.delenv(env_var, raising=False)


class TestDaemonMainFailure:
    """Tests for each daemon ``main`` error path (exit 1 + OTel shutdown)."""

    def test_all_mains_exit_1_on_failure(self, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]) -> None:
        """Every main converts _run errors into SystemExit(1) and shuts down OTel.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
            capsys: Pytest capture fixture.
        """
        for env_var in (
            "APME_ANSIBLE_VALIDATOR_LISTEN",
            "APME_COLLECTION_HEALTH_VALIDATOR_LISTEN",
            "APME_DEP_AUDIT_VALIDATOR_LISTEN",
            "APME_ENGINE_LISTEN",
            "APME_GITLEAKS_VALIDATOR_LISTEN",
            "APME_NATIVE_VALIDATOR_LISTEN",
            "APME_OPA_VALIDATOR_LISTEN",
        ):
            monkeypatch.delenv(env_var, raising=False)
        cases: list[tuple[str, Callable[[], None]]] = [
            ("apme_engine.daemon.ansible_validator_main._run", ansible_validator_main.main),
            ("apme_engine.daemon.collection_health_main._run", collection_health_main.main),
            ("apme_engine.daemon.dep_audit_main._run", dep_audit_main.main),
            ("apme_engine.daemon.engine_main._run", engine_main.main),
            ("apme_engine.daemon.gitleaks_validator_main._run", gitleaks_validator_main.main),
            ("apme_engine.daemon.native_validator_main._run", native_validator_main.main),
            ("apme_engine.daemon.opa_validator_main._run", opa_validator_main.main),
        ]
        for target, main_fn in cases:
            with (
                patch(target, new=AsyncMock(side_effect=RuntimeError("boom"))),
                patch("apme_engine.log_bridge.install_handler"),
                patch("apme_engine.observability.setup_otel"),
                patch("apme_engine.observability.shutdown_otel") as mock_shutdown,
                pytest.raises(SystemExit) as exc_info,
            ):
                main_fn()
            assert exc_info.value.code == 1
            mock_shutdown.assert_called_once()
        assert "failed" in capsys.readouterr().err


class TestValidatorGrpc:
    """Tests for the shared validator gRPC server helper."""

    def test_start_validator_server_binds_and_starts(self) -> None:
        """Helper registers the servicer, binds the address, and starts."""
        servicer: MagicMock = MagicMock()
        fake_server: MagicMock = MagicMock()
        fake_server.add_insecure_port = MagicMock(return_value=0)
        fake_server.start = AsyncMock(return_value=None)
        with (
            patch(
                "apme_engine.daemon.validator_grpc.grpc.aio.server",
                return_value=fake_server,
            ),
            patch("apme_engine.daemon.validator_grpc.GrpcMetricsInterceptor"),
            patch(
                "apme_engine.daemon.validator_grpc.validate_pb2_grpc.add_ValidatorServicer_to_server",
            ) as mock_add,
        ):
            result = asyncio.run(
                validator_grpc.start_validator_server(
                    servicer,
                    "127.0.0.1:50059",
                    service="dep_audit",
                    max_concurrent_rpcs=10,
                )
            )
        assert result is fake_server
        mock_add.assert_called_once_with(servicer, fake_server)
        fake_server.add_insecure_port.assert_called_once_with("127.0.0.1:50059")
        fake_server.start.assert_awaited_once()

    def test_start_validator_server_passes_options_and_service_label(self) -> None:
        """Helper forwards message limits, RPC cap, and the service label."""
        servicer: MagicMock = MagicMock()
        fake_server: MagicMock = MagicMock()
        fake_server.add_insecure_port = MagicMock(return_value=0)
        fake_server.start = AsyncMock(return_value=None)
        with (
            patch(
                "apme_engine.daemon.validator_grpc.grpc.aio.server",
                return_value=fake_server,
            ) as mock_server_ctor,
            patch(
                "apme_engine.daemon.validator_grpc.GrpcMetricsInterceptor",
            ) as mock_interceptor,
            patch(
                "apme_engine.daemon.validator_grpc.validate_pb2_grpc.add_ValidatorServicer_to_server",
            ),
        ):
            asyncio.run(
                validator_grpc.start_validator_server(
                    servicer,
                    "0.0.0.0:50055",
                    service="native",
                    max_concurrent_rpcs=7,
                )
            )
        mock_interceptor.assert_called_once_with(service="native")
        _, kwargs = mock_server_ctor.call_args
        assert kwargs["maximum_concurrent_rpcs"] == 7
        options = dict(kwargs["options"])
        assert options["grpc.max_receive_message_length"] == 50 * 1024 * 1024
        assert options["grpc.max_send_message_length"] == 50 * 1024 * 1024


class TestCliMainModule:
    """Tests for the ``python -m apme_engine.cli`` entrypoint."""

    def test_cli_main_module_calls_main(self) -> None:
        """Executing the cli __main__ module delegates to cli.main."""
        with patch("apme_engine.cli.main") as mock_main:
            runpy.run_module("apme_engine.cli.__main__", run_name="__main__")
        mock_main.assert_called_once_with()


class TestDaemonCmd:
    """Tests for the daemon start/stop/status subcommand."""

    def test_start_already_running(self, capsys: CaptureFixture[str]) -> None:
        """Start reports the existing daemon instead of starting again.

        Args:
            capsys: Pytest capture fixture.
        """
        state = _daemon_state()
        with (
            patch(
                "apme_engine.cli.daemon_cmd.daemon_status",
                return_value=state,
            ) as mock_status,
            patch("apme_engine.cli.daemon_cmd.start_daemon") as mock_start,
        ):
            run_daemon(argparse.Namespace(daemon_command="start"))
        mock_status.assert_called_once()
        mock_start.assert_not_called()
        assert "already running" in capsys.readouterr().err

    def test_start_success(self, capsys: CaptureFixture[str]) -> None:
        """Start launches the daemon when none is running.

        Args:
            capsys: Pytest capture fixture.
        """
        state = _daemon_state(pid=7777)
        with (
            patch("apme_engine.cli.daemon_cmd.daemon_status", return_value=None),
            patch("apme_engine.cli.daemon_cmd.start_daemon", return_value=state) as mock_start,
        ):
            run_daemon(argparse.Namespace(daemon_command="start"))
        mock_start.assert_called_once()
        err = capsys.readouterr().err
        assert "Daemon started" in err
        assert "7777" in err

    def test_start_status_error_exits_1(self) -> None:
        """Start exits 1 when the pre-flight status check raises."""
        with (
            patch(
                "apme_engine.cli.daemon_cmd.daemon_status",
                side_effect=RuntimeError("lock broken"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            run_daemon(argparse.Namespace(daemon_command="start"))
        assert exc_info.value.code == 1

    def test_start_daemon_error_exits_1(self) -> None:
        """Start exits 1 when the launcher fails to start."""
        with (
            patch("apme_engine.cli.daemon_cmd.daemon_status", return_value=None),
            patch(
                "apme_engine.cli.daemon_cmd.start_daemon",
                side_effect=RuntimeError("port busy"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            run_daemon(argparse.Namespace(daemon_command="start"))
        assert exc_info.value.code == 1

    def test_stop_when_running(self, capsys: CaptureFixture[str]) -> None:
        """Stop reports success when a daemon was stopped.

        Args:
            capsys: Pytest capture fixture.
        """
        with patch("apme_engine.cli.daemon_cmd.stop_daemon", return_value=True) as mock_stop:
            run_daemon(argparse.Namespace(daemon_command="stop"))
        mock_stop.assert_called_once()
        assert "Daemon stopped." in capsys.readouterr().err

    def test_stop_when_not_running(self, capsys: CaptureFixture[str]) -> None:
        """Stop reports when no daemon was running.

        Args:
            capsys: Pytest capture fixture.
        """
        with patch("apme_engine.cli.daemon_cmd.stop_daemon", return_value=False):
            run_daemon(argparse.Namespace(daemon_command="stop"))
        assert "No daemon running." in capsys.readouterr().err

    def test_status_error_exits_1(self) -> None:
        """Status exits 1 when the status check raises."""
        with (
            patch(
                "apme_engine.cli.daemon_cmd.daemon_status",
                side_effect=RuntimeError("cannot verify"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            run_daemon(argparse.Namespace(daemon_command="status"))
        assert exc_info.value.code == 1

    def test_status_none_exits_1(self, capsys: CaptureFixture[str]) -> None:
        """Status exits 1 when no daemon state exists.

        Args:
            capsys: Pytest capture fixture.
        """
        with (
            patch("apme_engine.cli.daemon_cmd.daemon_status", return_value=None),
            pytest.raises(SystemExit) as exc_info,
        ):
            run_daemon(argparse.Namespace(daemon_command="status"))
        assert exc_info.value.code == 1
        assert "No daemon running." in capsys.readouterr().err

    def test_status_with_services(self, capsys: CaptureFixture[str]) -> None:
        """Status prints daemon details plus the sorted service table.

        Args:
            capsys: Pytest capture fixture.
        """
        state = _daemon_state(
            services={"engine": "127.0.0.1:50051", "native": "127.0.0.1:50055"},
        )
        with patch("apme_engine.cli.daemon_cmd.daemon_status", return_value=state):
            run_daemon(argparse.Namespace(daemon_command="status"))
        out = capsys.readouterr().out
        assert "PID:" in out
        assert "Engine:" in out
        assert "Version:" in out
        assert "Started:" in out
        assert "Services:" in out
        assert "engine" in out
        assert "native" in out

    def test_status_without_services(self, capsys: CaptureFixture[str]) -> None:
        """Status omits the service table when no services are recorded.

        Args:
            capsys: Pytest capture fixture.
        """
        state = _daemon_state(services={})
        with patch("apme_engine.cli.daemon_cmd.daemon_status", return_value=state):
            run_daemon(argparse.Namespace(daemon_command="status"))
        out = capsys.readouterr().out
        assert "PID:" in out
        assert "Services:" not in out

    def test_unknown_command_does_nothing(self) -> None:
        """An unknown daemon subcommand leaves launcher functions untouched."""
        with (
            patch("apme_engine.cli.daemon_cmd.daemon_status") as mock_status,
            patch("apme_engine.cli.daemon_cmd.start_daemon") as mock_start,
            patch("apme_engine.cli.daemon_cmd.stop_daemon") as mock_stop,
        ):
            run_daemon(argparse.Namespace(daemon_command="bogus"))
        mock_status.assert_not_called()
        mock_start.assert_not_called()
        mock_stop.assert_not_called()


class TestHealthCheck:
    """Tests for the health-check subcommand (stub/channel mocked, no network)."""

    def test_health_ok_text_output(self, capsys: CaptureFixture[str]) -> None:
        """Healthy engine plus downstream prints a checkmark table.

        Args:
            capsys: Pytest capture fixture.
        """
        svc: MagicMock = MagicMock()
        svc.name = "native"
        svc.status = "ok"
        svc.address = "127.0.0.1:50055"
        resp: MagicMock = MagicMock()
        resp.status = "ok"
        resp.downstream = [svc]
        stub: MagicMock = MagicMock()
        stub.Health = MagicMock(return_value=resp)
        channel: MagicMock = MagicMock()
        channel.close = MagicMock(return_value=None)
        with (
            patch(
                "apme_engine.cli.health.resolve_engine",
                return_value=(channel, "127.0.0.1:50051"),
            ),
            patch("apme_engine.cli.health.engine_pb2_grpc.EngineStub", return_value=stub),
        ):
            run_health_check(_health_args(json=False, timeout=5.0))
        out = capsys.readouterr().out
        assert "engine" in out
        assert "native" in out
        assert "\u2714" in out
        channel.close.assert_called_once()

    def test_health_ok_json_output(self, capsys: CaptureFixture[str]) -> None:
        """Healthy engine with --json prints machine-readable results.

        Args:
            capsys: Pytest capture fixture.
        """
        svc: MagicMock = MagicMock()
        svc.name = "opa"
        svc.status = "ok"
        svc.address = "127.0.0.1:50054"
        resp: MagicMock = MagicMock()
        resp.status = "ok"
        resp.downstream = [svc]
        stub: MagicMock = MagicMock()
        stub.Health = MagicMock(return_value=resp)
        channel: MagicMock = MagicMock()
        channel.close = MagicMock(return_value=None)
        with (
            patch(
                "apme_engine.cli.health.resolve_engine",
                return_value=(channel, "127.0.0.1:50051"),
            ),
            patch("apme_engine.cli.health.engine_pb2_grpc.EngineStub", return_value=stub),
        ):
            run_health_check(_health_args(json=True, timeout=5.0))
        payload = json.loads(capsys.readouterr().out)
        assert payload["engine"]["status"] == "ok"
        assert payload["opa"]["status"] == "ok"
        channel.close.assert_called_once()

    def test_health_not_ok_exits_1(self) -> None:
        """Any non-ok service status exits 1 even when the RPC succeeds."""
        svc: MagicMock = MagicMock()
        svc.name = "native"
        svc.status = "unhealthy"
        svc.address = "127.0.0.1:50055"
        resp: MagicMock = MagicMock()
        resp.status = "ok"
        resp.downstream = [svc]
        stub: MagicMock = MagicMock()
        stub.Health = MagicMock(return_value=resp)
        channel: MagicMock = MagicMock()
        channel.close = MagicMock(return_value=None)
        with (
            patch(
                "apme_engine.cli.health.resolve_engine",
                return_value=(channel, "127.0.0.1:50051"),
            ),
            patch("apme_engine.cli.health.engine_pb2_grpc.EngineStub", return_value=stub),
            pytest.raises(SystemExit) as exc_info,
        ):
            run_health_check(_health_args(json=False, timeout=5.0))
        assert exc_info.value.code == 1
        channel.close.assert_called_once()

    def test_health_rpc_error_text_exits_1(self, capsys: CaptureFixture[str]) -> None:
        """RPC failure without --json writes to stderr and exits 1.

        Args:
            capsys: Pytest capture fixture.
        """
        stub: MagicMock = MagicMock()
        stub.Health = MagicMock(side_effect=grpc.RpcError("boom"))
        channel: MagicMock = MagicMock()
        channel.close = MagicMock(return_value=None)
        with (
            patch(
                "apme_engine.cli.health.resolve_engine",
                return_value=(channel, "127.0.0.1:50051"),
            ),
            patch("apme_engine.cli.health.engine_pb2_grpc.EngineStub", return_value=stub),
            pytest.raises(SystemExit) as exc_info,
        ):
            run_health_check(_health_args(json=False, timeout=5.0))
        assert exc_info.value.code == 1
        assert "error" in capsys.readouterr().err
        channel.close.assert_called_once()

    def test_health_rpc_error_json_exits_1(self, capsys: CaptureFixture[str]) -> None:
        """RPC failure with --json prints an error payload and exits 1.

        Args:
            capsys: Pytest capture fixture.
        """
        stub: MagicMock = MagicMock()
        stub.Health = MagicMock(side_effect=grpc.RpcError("boom"))
        channel: MagicMock = MagicMock()
        channel.close = MagicMock(return_value=None)
        with (
            patch(
                "apme_engine.cli.health.resolve_engine",
                return_value=(channel, "127.0.0.1:50051"),
            ),
            patch("apme_engine.cli.health.engine_pb2_grpc.EngineStub", return_value=stub),
            pytest.raises(SystemExit) as exc_info,
        ):
            run_health_check(_health_args(json=True, timeout=5.0))
        assert exc_info.value.code == 1
        payload = json.loads(capsys.readouterr().out)
        assert "error" in payload["engine"]["status"]
        channel.close.assert_called_once()

    def test_health_uses_default_timeout(self) -> None:
        """Missing timeout attribute falls back to 5 seconds."""
        resp: MagicMock = MagicMock()
        resp.status = "ok"
        resp.downstream = []
        stub: MagicMock = MagicMock()
        stub.Health = MagicMock(return_value=resp)
        channel: MagicMock = MagicMock()
        channel.close = MagicMock(return_value=None)
        with (
            patch(
                "apme_engine.cli.health.resolve_engine",
                return_value=(channel, "127.0.0.1:50051"),
            ),
            patch("apme_engine.cli.health.engine_pb2_grpc.EngineStub", return_value=stub),
        ):
            run_health_check(argparse.Namespace(json=False))
        _, kwargs = stub.Health.call_args
        assert kwargs["timeout"] == 5.0
        channel.close.assert_called_once()

    def test_health_unhealthy_symbol_for_down_service(self, capsys: CaptureFixture[str]) -> None:
        """Down services render the cross mark before the command exits 1.

        Args:
            capsys: Pytest capture fixture.
        """
        svc: MagicMock = MagicMock()
        svc.name = "ansible"
        svc.status = "down"
        svc.address = "127.0.0.1:50053"
        resp: MagicMock = MagicMock()
        resp.status = "down"
        resp.downstream = [svc]
        stub: MagicMock = MagicMock()
        stub.Health = MagicMock(return_value=resp)
        channel: MagicMock = MagicMock()
        channel.close = MagicMock(return_value=None)
        with (
            patch(
                "apme_engine.cli.health.resolve_engine",
                return_value=(channel, "127.0.0.1:50051"),
            ),
            patch("apme_engine.cli.health.engine_pb2_grpc.EngineStub", return_value=stub),
            pytest.raises(SystemExit) as exc_info,
        ):
            run_health_check(_health_args(json=False, timeout=1.0))
        assert exc_info.value.code == 1
        assert "\u2718" in capsys.readouterr().out
