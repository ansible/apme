"""Tests for CLI exit code semantics (issue #227).

Verifies that check/remediate/format use:
  0 = success (no violations)
  1 = violations found
  2 = error (infrastructure / usage)
"""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from apme_engine.cli._exit_codes import EXIT_ERROR, EXIT_SUCCESS, EXIT_VIOLATIONS


def _check_args(**overrides: object) -> argparse.Namespace:
    defaults = {
        "command": "check",
        "target": ".",
        "verbose": 0,
        "json": False,
        "diff": False,
        "session": None,
        "timeout": 300,
        "ansible_version": None,
        "collections": None,
        "no_ansi": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _remediate_args(**overrides: object) -> argparse.Namespace:
    defaults = {
        "command": "remediate",
        "target": ".",
        "verbose": 0,
        "json": False,
        "session": None,
        "max_passes": 5,
        "auto_approve": False,
        "ai": False,
        "model": None,
        "ansible_version": None,
        "collections": None,
        "no_ansi": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestExitCodeConstants:
    """Sanity-check the constant values match the documented convention."""

    def test_values(self) -> None:
        """Exit code constants have the expected integer values."""
        assert EXIT_SUCCESS == 0
        assert EXIT_VIOLATIONS == 1
        assert EXIT_ERROR == 2


class TestCheckExitCodes:
    """Exit codes for ``apme check``."""

    def test_file_not_found_exits_2(self) -> None:
        """FileNotFoundError during chunk generation exits with EXIT_ERROR."""
        with (
            patch(
                "apme_engine.cli.check.discover_project_root",
                return_value=Path("/fake"),
            ),
            patch(
                "apme_engine.cli.check.discover_galaxy_servers",
                return_value=None,
            ),
            patch(
                "apme_engine.cli.check.load_rule_configs_from_project",
                return_value=None,
            ),
            patch(
                "apme_engine.cli.check.yield_scan_chunks",
                side_effect=FileNotFoundError("nope"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            from apme_engine.cli.check import run_check

            run_check(_check_args())
        assert exc_info.value.code == EXIT_ERROR

    def test_grpc_error_exits_2(self) -> None:
        """GRPC transport failure exits with EXIT_ERROR."""
        import grpc

        class _FakeRpcError(grpc.RpcError):
            def details(self) -> str:
                return "connection refused"

        mock_channel = MagicMock()
        with (
            patch(
                "apme_engine.cli.check.discover_project_root",
                return_value=Path("/fake"),
            ),
            patch(
                "apme_engine.cli.check.discover_galaxy_servers",
                return_value=None,
            ),
            patch(
                "apme_engine.cli.check.load_rule_configs_from_project",
                return_value=None,
            ),
            patch("apme_engine.cli.check.yield_scan_chunks", return_value=iter([])),
            patch(
                "apme_engine.cli.check.resolve_engine",
                return_value=(mock_channel, "localhost:50051"),
            ),
            patch("apme_engine.cli.check.engine_pb2_grpc.EngineStub") as mock_stub_cls,
            pytest.raises(SystemExit) as exc_info,
        ):
            mock_stub_cls.return_value.FixSession.side_effect = _FakeRpcError()
            from apme_engine.cli.check import run_check

            run_check(_check_args())
        assert exc_info.value.code == EXIT_ERROR

    def test_invalid_session_exits_2(self) -> None:
        """Invalid --session value exits with EXIT_ERROR."""
        with pytest.raises(SystemExit) as exc_info:
            from apme_engine.cli.check import run_check

            run_check(_check_args(session="has spaces!"))
        assert exc_info.value.code == EXIT_ERROR


class TestDiscoveryExitCodes:
    """Exit codes from discovery (daemon startup failure)."""

    def test_daemon_failure_exits_2(self) -> None:
        """Engine connection failure exits with EXIT_ERROR."""
        with (
            patch(
                "apme_engine.cli.discovery.ensure_daemon",
                side_effect=RuntimeError("cannot start"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            from apme_engine.cli.discovery import resolve_engine

            resolve_engine()
        assert exc_info.value.code == EXIT_ERROR


class TestRemediateExitCodes:
    """Exit codes for ``apme remediate``."""

    def test_target_not_found_exits_2(self) -> None:
        """Missing target path exits with EXIT_ERROR."""
        with pytest.raises(SystemExit) as exc_info:
            from apme_engine.cli.remediate import run_remediate

            run_remediate(_remediate_args(target="/nonexistent/path"))
        assert exc_info.value.code == EXIT_ERROR


class TestFormatExitCodes:
    """Exit codes for ``apme format``."""

    def test_target_not_found_exits_2(self) -> None:
        """Missing target path exits with EXIT_ERROR."""
        args = argparse.Namespace(
            command="format",
            target="/nonexistent/path",
            verbose=0,
            check=False,
            apply=False,
            exclude=None,
            session=None,
            no_ansi=False,
        )
        with pytest.raises(SystemExit) as exc_info:
            from apme_engine.cli.format_cmd import run_format

            run_format(args)
        assert exc_info.value.code == EXIT_ERROR


class TestMainExitCodes:
    """Exit codes for the top-level CLI dispatch."""

    def test_unknown_command_exits_2(self) -> None:
        """Unknown subcommand exits with EXIT_ERROR."""
        with (
            patch("sys.argv", ["apme", "bogus"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            from apme_engine.cli import main

            main()
        assert exc_info.value.code == EXIT_ERROR
