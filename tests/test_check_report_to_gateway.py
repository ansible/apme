"""Gateway import payload conversion for CLI check results."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

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
