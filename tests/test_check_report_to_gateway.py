"""Gateway import payload conversion for CLI check results."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest

from apme_engine.cli.check import _import_line, _report_check_to_gateway


def test_import_line_uses_first_line_from_range() -> None:
    """The integer import schema receives the start of a ranged finding."""
    assert _import_line([8, 12]) == 8
    assert _import_line((4, 6)) == 4
    assert _import_line([]) is None
    assert _import_line(True) is None
    assert _import_line("8") is None


def test_report_check_to_gateway_normalizes_line_range(tmp_path: Path) -> None:
    """A ranged violation is mirrored with a schema-compatible integer line.

    Args:
        tmp_path: Temporary project directory.
    """
    args = Namespace(gateway_url=None, project_id=None)
    violations = [
        {
            "rule_id": "L001",
            "level": "warning",
            "message": "test finding",
            "file": "play.yml",
            "line": [8, 12],
            "path": "play.yml:task[0]",
        }
    ]

    with patch("apme_engine.cli.gateway_client.GatewayClient") as gateway_client:
        _report_check_to_gateway(args, tmp_path, violations)  # type: ignore[arg-type]

    payload = gateway_client.return_value.import_scan.call_args.args[0]
    assert payload["violations"][0]["line"] == 8


def test_report_check_to_gateway_truncates_fields_to_import_schema(tmp_path: Path) -> None:
    """Oversized finding fields are bounded before the Gateway request.

    Args:
        tmp_path: Temporary project directory.
    """
    args = Namespace(gateway_url=None, project_id=None)
    violations = [
        {
            "rule_id": "r" * 65,
            "level": "warning-level-too-long",
            "message": "m" * 4001,
            "file": "f" * 1001,
            "line": 1,
            "path": "p" * 2001,
            "remediation_class": "c" * 33,
        }
    ]

    with patch("apme_engine.cli.gateway_client.GatewayClient") as gateway_client:
        _report_check_to_gateway(args, tmp_path, violations)  # type: ignore[arg-type]

    payload = gateway_client.return_value.import_scan.call_args.args[0]
    row = payload["violations"][0]
    assert {key: len(value) for key, value in row.items() if isinstance(value, str)} == {
        "rule_id": 64,
        "level": 16,
        "message": 4000,
        "file": 1000,
        "path": 2000,
        "remediation_class": 32,
    }


def test_report_check_to_gateway_keeps_json_body_within_import_limit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Long findings are omitted before the serialized body exceeds 16 MiB.

    Args:
        tmp_path: Temporary project directory.
        capsys: Pytest output capture fixture.
    """
    args = Namespace(gateway_url=None, project_id=None)
    violations = [{"message": "m" * 4000} for _ in range(5000)]

    with patch("apme_engine.cli.gateway_client.GatewayClient") as gateway_client:
        _report_check_to_gateway(args, tmp_path, violations)  # type: ignore[arg-type]

    payload = gateway_client.return_value.import_scan.call_args.args[0]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert len(encoded) <= 16 * 1024 * 1024
    assert len(payload["violations"]) < 5000
    assert "omitted" in capsys.readouterr().err


def test_report_check_to_gateway_caps_rows_and_warns(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The import mirrors at most 5000 rows and reports any omitted findings.

    Args:
        tmp_path: Temporary project directory.
        capsys: Pytest output capture fixture.
    """
    args = Namespace(gateway_url=None, project_id=None)
    violations = [{"rule_id": f"L{index}"} for index in range(5001)]

    with patch("apme_engine.cli.gateway_client.GatewayClient") as gateway_client:
        _report_check_to_gateway(args, tmp_path, violations)  # type: ignore[arg-type]

    payload = gateway_client.return_value.import_scan.call_args.args[0]
    assert len(payload["violations"]) == 5000
    assert "omitted 1 violation(s)" in capsys.readouterr().err
