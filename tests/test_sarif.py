"""Tests for SARIF 2.1.0 output generation."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from apme_engine.cli._exit_codes import EXIT_VIOLATIONS
from apme_engine.cli.sarif import violations_to_sarif

_SarifNode = dict[str, object]


def _violation(
    *,
    rule_id: str = "L003",
    severity: str = "low",
    message: str = "Use FQCN",
    file: str = "playbooks/site.yml",
    line: int | list[int] | None = 10,
) -> dict[str, str | int | list[int] | bool | None]:
    return {
        "rule_id": rule_id,
        "severity": severity,
        "message": message,
        "file": file,
        "line": line,
        "path": "",
        "remediation_class": "auto-fixable",
        "remediation_resolution": "unresolved",
        "scope": "task",
    }


def _run(doc: _SarifNode) -> _SarifNode:
    """Extract the first run from a SARIF document.

    Args:
        doc: SARIF document dict.

    Returns:
        The first run object.
    """
    runs = cast(list[_SarifNode], doc["runs"])
    return runs[0]


def _results(doc: _SarifNode) -> list[_SarifNode]:
    """Extract results from the first run.

    Args:
        doc: SARIF document dict.

    Returns:
        List of result objects.
    """
    return cast(list[_SarifNode], _run(doc)["results"])


def _rules(doc: _SarifNode) -> list[_SarifNode]:
    """Extract rule descriptors from the first run.

    Args:
        doc: SARIF document dict.

    Returns:
        List of reporting descriptor objects.
    """
    driver = cast(_SarifNode, cast(_SarifNode, _run(doc)["tool"])["driver"])
    return cast(list[_SarifNode], driver["rules"])


def _driver(doc: _SarifNode) -> _SarifNode:
    """Extract the tool driver from the first run.

    Args:
        doc: SARIF document dict.

    Returns:
        Driver object.
    """
    return cast(_SarifNode, cast(_SarifNode, _run(doc)["tool"])["driver"])


def _location(result: _SarifNode) -> _SarifNode:
    """Extract the first location from a result.

    Args:
        result: SARIF result object.

    Returns:
        First location object.
    """
    locs = cast(list[_SarifNode], result["locations"])
    return locs[0]


def _phys(result: _SarifNode) -> _SarifNode:
    """Extract physicalLocation from a result's first location.

    Args:
        result: SARIF result object.

    Returns:
        Physical location object.
    """
    return cast(_SarifNode, _location(result)["physicalLocation"])


class TestSarifStructure:
    """Basic SARIF document structure."""

    def test_empty_violations_produces_valid_sarif(self) -> None:
        """An empty violation list produces a valid SARIF with zero results."""
        doc = violations_to_sarif([])
        assert doc["version"] == "2.1.0"
        assert cast(str, doc["$schema"]).endswith("sarif-schema-2.1.0.json")
        runs = cast(list[_SarifNode], doc["runs"])
        assert len(runs) == 1
        assert _results(doc) == []
        assert _rules(doc) == []

    def test_single_violation(self) -> None:
        """One violation maps to one result and one rule descriptor."""
        doc = violations_to_sarif([_violation()])
        assert len(_results(doc)) == 1
        assert len(_rules(doc)) == 1

        result = _results(doc)[0]
        assert result["ruleId"] == "L003"
        assert cast(_SarifNode, result["message"])["text"] == "Use FQCN"

    def test_tool_version_included(self) -> None:
        """Tool version appears in the driver when provided."""
        doc = violations_to_sarif([], tool_version="1.2.3")
        driver = _driver(doc)
        assert driver["version"] == "1.2.3"
        assert driver["semanticVersion"] == "1.2.3"

    def test_tool_version_absent_when_not_provided(self) -> None:
        """Driver omits version fields when tool_version is None."""
        doc = violations_to_sarif([])
        driver = _driver(doc)
        assert "version" not in driver


class TestSeverityMapping:
    """APME severity labels map to SARIF levels."""

    def test_critical_maps_to_error(self) -> None:
        """Critical severity maps to SARIF 'error'."""
        doc = violations_to_sarif([_violation(severity="critical")])
        assert _results(doc)[0]["level"] == "error"

    def test_high_maps_to_error(self) -> None:
        """High severity maps to SARIF 'error'."""
        doc = violations_to_sarif([_violation(severity="high")])
        assert _results(doc)[0]["level"] == "error"

    def test_medium_maps_to_warning(self) -> None:
        """Medium severity maps to SARIF 'warning'."""
        doc = violations_to_sarif([_violation(severity="medium")])
        assert _results(doc)[0]["level"] == "warning"

    def test_low_maps_to_note(self) -> None:
        """Low severity maps to SARIF 'note'."""
        doc = violations_to_sarif([_violation(severity="low")])
        assert _results(doc)[0]["level"] == "note"

    def test_info_maps_to_note(self) -> None:
        """Info severity maps to SARIF 'note'."""
        doc = violations_to_sarif([_violation(severity="info")])
        assert _results(doc)[0]["level"] == "note"

    def test_unknown_severity_defaults_to_warning(self) -> None:
        """Unknown severity strings default to SARIF 'warning'."""
        doc = violations_to_sarif([_violation(severity="banana")])
        assert _results(doc)[0]["level"] == "warning"


class TestLocationMapping:
    """File paths and line numbers in SARIF locations."""

    def test_single_line(self) -> None:
        """Integer line becomes startLine in region."""
        doc = violations_to_sarif([_violation(line=42)])
        phys = _phys(_results(doc)[0])
        region = cast(_SarifNode, phys["region"])
        assert region["startLine"] == 42

    def test_line_range(self) -> None:
        """List [start, end] becomes startLine + endLine."""
        doc = violations_to_sarif([_violation(line=[10, 20])])
        region = cast(_SarifNode, _phys(_results(doc)[0])["region"])
        assert region["startLine"] == 10
        assert region["endLine"] == 20

    def test_no_line(self) -> None:
        """None line omits the region entirely."""
        doc = violations_to_sarif([_violation(line=None)])
        phys = _phys(_results(doc)[0])
        assert "region" not in phys

    def test_file_uri_strips_dot_slash(self) -> None:
        """Leading './' is stripped from file URIs."""
        doc = violations_to_sarif([_violation(file="./roles/main.yml")])
        phys = _phys(_results(doc)[0])
        artifact = cast(_SarifNode, phys["artifactLocation"])
        assert artifact["uri"] == "roles/main.yml"

    def test_srcroot_base_id(self) -> None:
        """Artifact location uses %SRCROOT% as uriBaseId."""
        doc = violations_to_sarif([_violation()])
        phys = _phys(_results(doc)[0])
        artifact = cast(_SarifNode, phys["artifactLocation"])
        assert artifact["uriBaseId"] == "%SRCROOT%"

    def test_empty_file_omits_artifact_location(self) -> None:
        """Empty file path produces physicalLocation without artifactLocation."""
        doc = violations_to_sarif([_violation(file="")])
        phys = _phys(_results(doc)[0])
        assert "artifactLocation" not in phys

    def test_dot_slash_only_omits_artifact_location(self) -> None:
        """File path of just './' is treated as empty."""
        doc = violations_to_sarif([_violation(file="./")])
        phys = _phys(_results(doc)[0])
        assert "artifactLocation" not in phys

    def test_line_range_clamps_zero_start(self) -> None:
        """Line range with start=0 is clamped to 1."""
        doc = violations_to_sarif([_violation(line=[0, 5])])
        region = cast(_SarifNode, _phys(_results(doc)[0])["region"])
        assert region["startLine"] == 1
        assert region["endLine"] == 5

    def test_line_range_clamps_negative(self) -> None:
        """Negative line values are clamped to 1."""
        doc = violations_to_sarif([_violation(line=[-3, -1])])
        region = cast(_SarifNode, _phys(_results(doc)[0])["region"])
        assert region["startLine"] == 1
        assert region["endLine"] == 1

    def test_line_range_end_less_than_start(self) -> None:
        """End < start is clamped so end >= start."""
        doc = violations_to_sarif([_violation(line=[10, 5])])
        region = cast(_SarifNode, _phys(_results(doc)[0])["region"])
        assert region["startLine"] == 10
        assert region["endLine"] == 10

    def test_zero_line_omits_region(self) -> None:
        """Line value of 0 omits the region."""
        doc = violations_to_sarif([_violation(line=0)])
        phys = _phys(_results(doc)[0])
        assert "region" not in phys


class TestRuleDeduplication:
    """Multiple violations with the same rule_id produce one rule descriptor."""

    def test_duplicate_rules_deduplicated(self) -> None:
        """Two violations with the same rule_id produce one rule entry."""
        doc = violations_to_sarif(
            [
                _violation(rule_id="L003", message="first"),
                _violation(rule_id="L003", message="second"),
            ]
        )
        assert len(_results(doc)) == 2
        assert len(_rules(doc)) == 1

    def test_different_rules_both_listed(self) -> None:
        """Violations with different rule_ids produce separate rule entries."""
        doc = violations_to_sarif(
            [
                _violation(rule_id="L003"),
                _violation(rule_id="M005"),
            ]
        )
        rule_ids = [r["id"] for r in _rules(doc)]
        assert "L003" in rule_ids
        assert "M005" in rule_ids


class TestRuleHelpText:
    """Rule ID prefixes map to descriptive help text."""

    def test_lint_prefix(self) -> None:
        """L-prefix rules get lint help text."""
        doc = violations_to_sarif([_violation(rule_id="L003")])
        rule = _rules(doc)[0]
        assert "Lint" in cast(str, cast(_SarifNode, rule["help"])["text"])

    def test_modernize_prefix(self) -> None:
        """M-prefix rules get modernization help text."""
        doc = violations_to_sarif([_violation(rule_id="M005")])
        rule = _rules(doc)[0]
        assert "Modernization" in cast(str, cast(_SarifNode, rule["help"])["text"])

    def test_security_prefix(self) -> None:
        """SEC-prefix rules get security help text."""
        doc = violations_to_sarif([_violation(rule_id="SEC:generic-api-key")])
        rule = _rules(doc)[0]
        assert "Security" in cast(str, cast(_SarifNode, rule["help"])["text"])

    def test_empty_message_falls_back_to_rule_id(self) -> None:
        """Empty message text falls back to rule_id for SARIF compliance."""
        doc = violations_to_sarif([_violation(rule_id="L003", message="")])
        result = _results(doc)[0]
        assert cast(_SarifNode, result["message"])["text"] == "L003"

    def test_help_uri_url_encodes_rule_id(self) -> None:
        """Rule IDs with special characters are URL-encoded in helpUri."""
        doc = violations_to_sarif([_violation(rule_id="SEC:generic-api-key")])
        rule = _rules(doc)[0]
        help_uri = cast(str, rule["helpUri"])
        assert "SEC%3Ageneric-api-key" in help_uri
        assert ":" not in help_uri.split("/rules/")[1]


def _check_args(**overrides: object) -> argparse.Namespace:
    """Build an argparse namespace suitable for run_check().

    Args:
        **overrides: Override defaults (e.g. ``sarif=True``).

    Returns:
        Namespace with every attribute run_check reads.
    """
    defaults: dict[str, object] = {
        "command": "check",
        "target": ".",
        "verbose": 0,
        "json": False,
        "sarif": False,
        "diff": False,
        "session": None,
        "timeout": 300,
        "ansible_version": None,
        "collections": None,
        "no_ansi": False,
        "skip_dep_scan": False,
        "skip_collection_scan": False,
        "skip_python_audit": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _fake_fix_session(
    violations: list[dict[str, object]],
    scan_id: str = "scan-123",
) -> tuple[MagicMock, MagicMock]:
    """Build a mock channel and stub whose FixSession yields a single result event.

    Args:
        violations: Violation dicts to surface as remaining_violations.
        scan_id: Scan id to emit from the first uploaded chunk.

    Returns:
        Tuple of ``(channel_mock, stub_class_mock)`` suitable for patch targets.
    """
    created_event = MagicMock()
    created_event.WhichOneof.return_value = "created"

    result_event = MagicMock()
    result_event.WhichOneof.return_value = "result"
    result_event.result.remaining_violations = [object() for _ in violations]
    result_event.result.patches = []

    closed_event = MagicMock()
    closed_event.WhichOneof.return_value = "closed"

    def _yield_events(
        cmd_iter: Iterable[object],  # noqa: ARG001
        timeout: float | None = None,  # noqa: ARG001
    ) -> Iterator[object]:
        """Emit the canned event sequence without draining commands.

        We intentionally do not iterate ``cmd_iter`` — draining would block
        because the real ``command_iter`` pulls from a ``queue.Queue`` that
        the main loop only feeds in reaction to our yielded events.

        Args:
            cmd_iter: Command iterator from the CLI; intentionally unused.
            timeout: FixSession timeout; intentionally unused.

        Yields:
            object: Canned gRPC response events (created, result, closed).
        """
        yield created_event
        yield result_event
        yield closed_event

    stub = MagicMock()
    stub.FixSession.side_effect = _yield_events

    channel = MagicMock()
    return channel, stub


class TestSarifCliFlag:
    """End-to-end tests for the ``apme check --sarif`` flag (run_check)."""

    @staticmethod
    def _patched_run_check(
        violations: list[dict[str, object]],
        args: argparse.Namespace,
    ) -> int:
        """Execute run_check with external dependencies mocked.

        Args:
            violations: Violation dicts to inject via the FixSession result event.
            args: CLI args namespace passed to run_check.

        Returns:
            The exit code raised by run_check (0 for success, else ``SystemExit.code``).
        """
        channel, stub = _fake_fix_session(violations)

        upload_chunk = MagicMock()
        upload_chunk.scan_id = "scan-123"

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
                return_value=iter([upload_chunk]),
            ),
            patch(
                "apme_engine.cli.check.resolve_engine",
                return_value=(channel, "localhost:50051"),
            ),
            patch(
                "apme_engine.cli.check.engine_pb2_grpc.EngineStub",
                return_value=stub,
            ),
            patch(
                "apme_engine.cli.check.violation_proto_to_dict",
                side_effect=list(violations),
            ),
        ):
            from apme_engine.cli.check import run_check

            try:
                run_check(args)
            except SystemExit as e:
                code = e.code
                return int(code) if isinstance(code, int) else 0
            return 0

    def test_sarif_emits_valid_json_to_stdout(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--sarif prints a parseable SARIF 2.1.0 document to stdout.

        Args:
            capsys: pytest fixture for capturing stdout/stderr.
        """
        violation = {
            "rule_id": "L003",
            "severity": "low",
            "message": "Use FQCN",
            "file": "playbooks/site.yml",
            "line": 10,
            "path": "",
        }
        exit_code = self._patched_run_check([violation], _check_args(sarif=True))
        stdout = capsys.readouterr().out

        assert exit_code == EXIT_VIOLATIONS
        doc = json.loads(stdout)
        assert doc["version"] == "2.1.0"
        assert doc["$schema"].endswith("sarif-schema-2.1.0.json")
        assert len(doc["runs"]) == 1
        results = doc["runs"][0]["results"]
        assert len(results) == 1
        assert results[0]["ruleId"] == "L003"
        assert results[0]["level"] == "note"

    def test_sarif_exit_zero_when_no_violations(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--sarif exits 0 when there are no violations.

        Args:
            capsys: pytest fixture for capturing stdout/stderr.
        """
        exit_code = self._patched_run_check([], _check_args(sarif=True))
        stdout = capsys.readouterr().out

        assert exit_code == 0
        doc = json.loads(stdout)
        assert doc["version"] == "2.1.0"
        assert doc["runs"][0]["results"] == []

    def test_sarif_exit_one_when_violations_present(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--sarif exits 1 (EXIT_VIOLATIONS) when violations exist.

        Args:
            capsys: pytest fixture for capturing stdout/stderr.
        """
        violation = {
            "rule_id": "SEC:generic-api-key",
            "severity": "high",
            "message": "API key found",
            "file": "roles/example/tasks/main.yml",
            "line": 42,
            "path": "",
        }
        exit_code = self._patched_run_check([violation], _check_args(sarif=True))
        capsys.readouterr()

        assert exit_code == EXIT_VIOLATIONS

    def test_sarif_suppresses_human_readable_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--sarif suppresses the human-readable render and emits only SARIF JSON.

        Args:
            capsys: pytest fixture for capturing stdout/stderr.
        """
        violation = {
            "rule_id": "M005",
            "severity": "medium",
            "message": "Deprecated module",
            "file": "tasks.yml",
            "line": 1,
            "path": "",
        }
        exit_code = self._patched_run_check([violation], _check_args(sarif=True))
        stdout = capsys.readouterr().out

        assert exit_code == EXIT_VIOLATIONS
        stdout_stripped = stdout.strip()
        assert stdout_stripped.startswith("{")
        assert stdout_stripped.endswith("}")
        json.loads(stdout_stripped)
