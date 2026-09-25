"""Unit tests for CLI surface coverage: check, remediate, format, suppress, output."""

from __future__ import annotations

import argparse
import itertools
import json
import threading
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import grpc
import pytest

from apme.v1 import common_pb2, engine_pb2
from apme.v1.common_pb2 import ProgressUpdate
from apme.v1.engine_pb2 import (
    FileDiff,
    FilePatch,
    FixReport,
    ScanChunk,
    SessionResult,
    Tier1Summary,
)
from apme_engine.cli._exit_codes import EXIT_ERROR, EXIT_VIOLATIONS


class _FakeRpcError(grpc.RpcError):
    """Minimal RpcError carrying a status code and details string."""

    def __init__(self, code: grpc.StatusCode, details: str = "boom") -> None:
        """Store code and details.

        Args:
            code: gRPC status code to report.
            details: Human-readable error details.
        """
        super().__init__()
        self._code = code
        self._details = details

    def code(self) -> grpc.StatusCode:
        """Return the configured status code.

        Returns:
            The gRPC status code.
        """
        return self._code

    def details(self) -> str:
        """Return the configured details string.

        Returns:
            Details string.
        """
        return self._details


def _rem_args(target: str, **overrides: object) -> argparse.Namespace:
    """Build a remediate namespace with sane defaults.

    Args:
        target: Scan target path string.
        **overrides: Attribute overrides.

    Returns:
        Populated argparse namespace.

    """
    defaults: dict[str, object] = {
        "target": target,
        "session": None,
        "max_passes": 5,
        "ansible_version": None,
        "collections": None,
        "ai": False,
        "model": None,
        "interactive": False,
        "timeout": None,
        "json": False,
        "verbose": 0,
        "auto_approve": False,
        "show_suppressed": False,
        "skip_dep_scan": False,
        "skip_collection_scan": False,
        "skip_python_audit": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _check_args_ns(target: str, **overrides: object) -> argparse.Namespace:
    """Build a check namespace with sane defaults.

    Args:
        target: Scan target path string.
        **overrides: Attribute overrides.

    Returns:
        Populated argparse namespace.

    """
    defaults: dict[str, object] = {
        "command": "check",
        "target": target,
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
        "show_suppressed": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _format_args_ns(target: str, **overrides: object) -> argparse.Namespace:
    """Build a format namespace with sane defaults.

    Args:
        target: Format target path string.
        **overrides: Attribute overrides.

    Returns:
        Populated argparse namespace.

    """
    defaults: dict[str, object] = {
        "command": "format",
        "target": target,
        "verbose": 0,
        "check": False,
        "apply": False,
        "session": None,
        "no_ansi": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _mk_event(kind: str) -> MagicMock:
    """Create a SessionEvent mock reporting the given oneof kind.

    Args:
        kind: Value for ``WhichOneof("event")``.

    Returns:
        Configured MagicMock event.

    """
    ev = MagicMock()
    ev.WhichOneof.return_value = kind
    return ev


def _scan_chunk(scan_id: str = "scan-1") -> ScanChunk:
    """Build a minimal ScanChunk proto.

    Args:
        scan_id: Scan identifier.

    Returns:
        ScanChunk proto.

    """
    return ScanChunk(scan_id=scan_id, project_root="project", last=True)


# ── output.py ─────────────────────────────────────────────────────────────


def test_render_logs_verbosity_zero_filters_info(capsys: pytest.CaptureFixture[str]) -> None:
    """Verbosity 0 shows warning and above only.

    Args:
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli.output import render_logs

    logs = [
        ProgressUpdate(message="debug-msg", phase="engine", level=1),
        ProgressUpdate(message="info-msg", phase="engine", level=2),
        ProgressUpdate(message="warn-msg", phase="engine", level=3),
        ProgressUpdate(message="err-msg", phase="", level=4),
    ]
    render_logs(logs, 0)
    err = capsys.readouterr().err
    assert "debug-msg" not in err
    assert "info-msg" not in err
    assert "warn-msg" in err
    assert "err-msg" in err


def test_render_logs_verbosity_one_and_two(capsys: pytest.CaptureFixture[str]) -> None:
    """Verbosity 1 shows info+, verbosity 2 shows everything.

    Args:
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli.output import render_logs

    logs = [
        ProgressUpdate(message="m1", phase="p", level=1),
        ProgressUpdate(message="m2", phase="", level=2),
    ]
    render_logs(logs, 1)
    err1 = capsys.readouterr().err
    assert "m1" not in err1
    assert "m2" in err1
    render_logs(logs, 2)
    err2 = capsys.readouterr().err
    assert "m1" in err2
    assert "m2" in err2


def test_sort_violations_list_line_and_missing() -> None:
    """List/tuple lines sort by first element; missing lines sort as zero."""
    from apme_engine.cli.output import sort_violations

    violations = [
        {"rule_id": "L1", "file": "b.yml", "line": [10, 12]},
        {"rule_id": "L2", "file": "a.yml", "line": 3},
        {"rule_id": "L3", "file": "a.yml", "line": None},
        {"rule_id": "L4", "file": "a.yml", "line": (1, 2)},
        {"rule_id": "L5", "file": "a.yml", "line": ["x", "y"]},
        {"rule_id": "L6", "file": "a.yml", "line": 1.5},
    ]
    out = sort_violations(violations)  # type: ignore[arg-type]
    assert out[0]["file"] == "a.yml"
    assert out[-1]["file"] == "b.yml"


def test_deduplicate_violations_tuple_and_list() -> None:
    """List lines normalize to tuples so duplicates collapse."""
    from apme_engine.cli.output import deduplicate_violations

    violations = [
        {"rule_id": "L1", "file": "a.yml", "line": [1, 2]},
        {"rule_id": "L1", "file": "a.yml", "line": [1, 2]},
        {"rule_id": "L1", "file": "a.yml", "line": (1, 2)},
        {"rule_id": "L2", "file": "a.yml", "line": 5},
    ]
    out = deduplicate_violations(violations)  # type: ignore[arg-type]
    assert len(out) == 2


def test_count_by_severity_unknown_goes_info() -> None:
    """Unknown and missing severities count as info."""
    from apme_engine.cli.output import count_by_severity

    counts = count_by_severity(
        [
            {"severity": "critical"},
            {"severity": "bogus"},
            {"severity": ""},
            {"severity": "HIGH"},
            {},
        ]
    )
    assert counts["critical"] == 1
    assert counts["high"] == 1
    assert counts["info"] == 3


def test_format_remediation_summary_none() -> None:
    """None summary formats as 'none'."""
    from apme_engine.cli.output import format_remediation_summary

    assert format_remediation_summary(None) == "none"


def test_format_remediation_summary_counts_and_resolutions() -> None:
    """Auto/AI/manual counts and non-unresolved resolutions render."""
    from apme_engine.cli.output import format_remediation_summary

    class _S:
        auto_fixable = 2
        ai_candidate = 1
        manual_review = 0
        by_resolution = {"fixed": 2, "unresolved": 5, "manual": 1}

    text = format_remediation_summary(_S())
    assert "2 auto-fixable" in text
    assert "1 AI-candidate" in text
    assert "fixed" in text
    assert "unresolved" not in text


def test_format_remediation_summary_empty_is_none() -> None:
    """Zero counts with only unresolved resolutions formats as 'none'."""
    from apme_engine.cli.output import format_remediation_summary

    class _S:
        auto_fixable = 0
        ai_candidate = 0
        manual_review = 0
        by_resolution = {"unresolved": 3}

    assert format_remediation_summary(_S()) == "none"


def test_render_check_results_passed_no_violations(capsys: pytest.CaptureFixture[str]) -> None:
    """Empty violations render PASSED with no issues.

    Args:
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli.output import render_check_results

    render_check_results([], scan_id="s1", scan_time_ms=5.0, summary=None)
    out = capsys.readouterr().out
    assert "PASSED" in out
    assert "No issues found" in out


def test_render_check_results_failed_table_and_tree(capsys: pytest.CaptureFixture[str]) -> None:
    """Failed results render table rows, truncation, locations, and tree.

    Args:
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli.output import render_check_results

    violations = [
        {
            "rule_id": "L001",
            "severity": "error",
            "remediation_class": "auto-fixable",
            "message": "x" * 60,
            "file": "b.yml",
            "line": [3, 5],
        },
        {
            "rule_id": "L002",
            "severity": "medium",
            "remediation_class": "manual-review",
            "message": "short",
            "file": "a.yml",
            "line": 7,
        },
        {
            "rule_id": "L003",
            "severity": "low",
            "remediation_class": "ai-candidate",
            "message": "noloc",
            "file": "a.yml",
            "line": None,
        },
        {
            "rule_id": "L004",
            "severity": "critical",
            "remediation_class": "ai-candidate",
            "message": "single-list",
            "file": "a.yml",
            "line": [9],
        },
        {
            "rule_id": "L005",
            "severity": "info",
            "remediation_class": "ai-candidate",
            "message": "high-sev",
            "file": "c.yml",
            "line": 1,
        },
    ]
    render_check_results(violations, scan_id="abc", scan_time_ms=1500.0, summary=None)  # type: ignore[arg-type]
    out = capsys.readouterr().out
    assert "FAILED" in out
    assert "Issues" in out
    assert "Issues by File" in out
    assert "abc" in out
    assert "..." in out


def test_print_diagnostics_v_full(capsys: pytest.CaptureFixture[str]) -> None:
    """print_diagnostics_v covers engine detail, files, validators, top rules.

    Args:
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli.output import print_diagnostics_v

    vd1 = common_pb2.ValidatorDiagnostics(
        validator_name="native",
        total_ms=10.0,
        violations_found=2,
        rule_timings=[
            common_pb2.RuleTiming(rule_id="L001", elapsed_ms=5.0, violations=1),
            common_pb2.RuleTiming(rule_id="opa_query", elapsed_ms=9.0, violations=0),
            common_pb2.RuleTiming(rule_id="gitleaks_subprocess_x", elapsed_ms=8.0, violations=0),
        ],
        metadata={"k1": "v1", "opa_response_size": "big", "files_written": "0"},
    )
    vd2 = common_pb2.ValidatorDiagnostics(
        validator_name="opa",
        total_ms=3.0,
        violations_found=0,
        metadata={},
    )
    diag = engine_pb2.ScanDiagnostics(
        engine_parse_ms=2.0,
        engine_annotate_ms=1.0,
        engine_total_ms=5.0,
        files_scanned=4,
        validators=[vd1, vd2],
        fan_out_ms=6.0,
        total_ms=12.0,
    )
    print_diagnostics_v(diag)
    err = capsys.readouterr().err
    assert "Engine:" in err
    assert "Files:" in err
    assert "Top slowest rules" in err
    assert "L001" in err
    assert "k1=v1" in err
    assert "opa_response_size" not in err


def test_print_diagnostics_v_no_validators_no_top(capsys: pytest.CaptureFixture[str]) -> None:
    """No validators and no timings skips validator and top-rule sections.

    Args:
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli.output import print_diagnostics_v

    diag = engine_pb2.ScanDiagnostics(engine_total_ms=1.0, fan_out_ms=1.0, total_ms=2.0)
    print_diagnostics_v(diag)
    err = capsys.readouterr().err
    assert "Total:" in err
    assert "Top slowest rules" not in err


def test_print_diagnostics_vv_full(capsys: pytest.CaptureFixture[str]) -> None:
    """print_diagnostics_vv covers files, graph nodes, timings, metadata.

    Args:
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli.output import print_diagnostics_vv

    vd = common_pb2.ValidatorDiagnostics(
        validator_name="native",
        total_ms=4.0,
        violations_found=1,
        rule_timings=[
            common_pb2.RuleTiming(rule_id="L001", elapsed_ms=2.0, violations=1),
            common_pb2.RuleTiming(rule_id="L002", elapsed_ms=0.0, violations=0),
        ],
        metadata={"a": "b"},
    )
    diag = engine_pb2.ScanDiagnostics(
        engine_parse_ms=1.0,
        engine_annotate_ms=1.0,
        engine_total_ms=3.0,
        files_scanned=2,
        graph_nodes_built=7,
        validators=[vd],
        fan_out_ms=2.0,
        total_ms=5.0,
    )
    print_diagnostics_vv(diag)
    err = capsys.readouterr().err
    assert "file(s)" in err
    assert "graph node(s)" in err
    assert "metadata:" in err
    assert "Fan-out:" in err


def test_print_diagnostics_vv_no_metadata(capsys: pytest.CaptureFixture[str]) -> None:
    """Validators without metadata skip the metadata line.

    Args:
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli.output import print_diagnostics_vv

    vd = common_pb2.ValidatorDiagnostics(validator_name="opa", total_ms=1.0, violations_found=0)
    diag = engine_pb2.ScanDiagnostics(engine_total_ms=1.0, validators=[vd], fan_out_ms=1.0, total_ms=2.0)
    print_diagnostics_vv(diag)
    err = capsys.readouterr().err
    assert "metadata:" not in err


def test_diag_to_dict_rounds_and_lists() -> None:
    """diag_to_dict converts validators, timings, and metadata."""
    from apme_engine.cli.output import diag_to_dict

    vd = common_pb2.ValidatorDiagnostics(
        validator_name="native",
        total_ms=10.55,
        files_received=3,
        violations_found=1,
        rule_timings=[common_pb2.RuleTiming(rule_id="L001", elapsed_ms=1.234, violations=1)],
        metadata={"k": "v"},
    )
    diag = engine_pb2.ScanDiagnostics(
        engine_parse_ms=1.11,
        engine_annotate_ms=2.22,
        engine_total_ms=3.33,
        files_scanned=2,
        graph_nodes_built=5,
        total_violations=1,
        validators=[vd],
        fan_out_ms=4.44,
        total_ms=9.99,
    )
    d = diag_to_dict(diag)
    assert d["files_scanned"] == 2
    assert d["graph_nodes_built"] == 5
    validators = d["validators"]
    assert isinstance(validators, list)
    first = validators[0]
    assert isinstance(first, dict)
    assert first["validator_name"] == "native"
    assert first["metadata"] == {"k": "v"}


# ── format_cmd.py ─────────────────────────────────────────────────────────


def test_format_uses_explicit_session(tmp_path: Path) -> None:
    """Explicit --session bypasses project-root discovery.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.format_cmd import run_format

    target = tmp_path / "site.yml"
    target.write_text("a: 1\n", encoding="utf-8")
    resp = MagicMock()
    resp.logs = []
    resp.diffs = []
    channel = MagicMock()
    with (
        patch("apme_engine.cli.format_cmd.yield_scan_chunks", return_value=iter([_scan_chunk()])) as y,
        patch("apme_engine.cli.format_cmd.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.format_cmd.engine_pb2_grpc.EngineStub") as stub_cls,
        patch("apme_engine.cli.format_cmd.discover_project_root") as disc,
    ):
        stub_cls.return_value.FormatStream.return_value = resp
        run_format(_format_args_ns(str(target), session="explicit-1"))
        disc.assert_not_called()
        y.assert_called_once()
        assert y.call_args.kwargs.get("session_id") == "explicit-1"


def test_format_derives_session_when_missing(tmp_path: Path) -> None:
    """Missing --session derives the id from the project root.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.format_cmd import run_format

    target = tmp_path / "site.yml"
    target.write_text("a: 1\n", encoding="utf-8")
    resp = MagicMock()
    resp.logs = []
    resp.diffs = []
    channel = MagicMock()
    with (
        patch("apme_engine.cli.format_cmd.discover_project_root", return_value=tmp_path) as disc,
        patch("apme_engine.cli.format_cmd.derive_session_id", return_value="derived-1") as der,
        patch("apme_engine.cli.format_cmd.yield_scan_chunks", return_value=iter([_scan_chunk()])),
        patch("apme_engine.cli.format_cmd.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.format_cmd.engine_pb2_grpc.EngineStub") as stub_cls,
    ):
        stub_cls.return_value.FormatStream.return_value = resp
        run_format(_format_args_ns(str(target)))
        disc.assert_called_once()
        der.assert_called_once_with(tmp_path)


def test_format_chunk_not_found_exits_error(tmp_path: Path) -> None:
    """FileNotFoundError from chunking exits with EXIT_ERROR.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.format_cmd import run_format

    target = tmp_path / "site.yml"
    target.write_text("a: 1\n", encoding="utf-8")
    with (
        patch("apme_engine.cli.format_cmd.yield_scan_chunks", side_effect=FileNotFoundError("gone")),
        pytest.raises(SystemExit) as exc,
    ):
        run_format(_format_args_ns(str(target)))
    assert exc.value.code == EXIT_ERROR


def test_format_grpc_error_exits_error(tmp_path: Path) -> None:
    """FormatStream RpcError exits with EXIT_ERROR.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.format_cmd import run_format

    target = tmp_path / "site.yml"
    target.write_text("a: 1\n", encoding="utf-8")
    channel = MagicMock()
    with (
        patch("apme_engine.cli.format_cmd.yield_scan_chunks", return_value=iter([_scan_chunk()])),
        patch("apme_engine.cli.format_cmd.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.format_cmd.engine_pb2_grpc.EngineStub") as stub_cls,
        pytest.raises(SystemExit) as exc,
    ):
        stub_cls.return_value.FormatStream.side_effect = _FakeRpcError(grpc.StatusCode.UNAVAILABLE, "down")
        run_format(_format_args_ns(str(target)))
    assert exc.value.code == EXIT_ERROR
    channel.close.assert_called_once()


def test_format_no_diffs_reports_clean(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Empty diff list reports already-formatted and renders logs.

    Args:
        tmp_path: Temporary directory fixture.
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli.format_cmd import run_format

    target = tmp_path / "site.yml"
    target.write_text("a: 1\n", encoding="utf-8")
    resp = engine_pb2.FormatResponse(
        diffs=[],
        logs=[ProgressUpdate(message="hello", phase="engine", level=3)],
    )
    channel = MagicMock()
    with (
        patch("apme_engine.cli.format_cmd.yield_scan_chunks", return_value=iter([_scan_chunk()])),
        patch("apme_engine.cli.format_cmd.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.format_cmd.engine_pb2_grpc.EngineStub") as stub_cls,
    ):
        stub_cls.return_value.FormatStream.return_value = resp
        run_format(_format_args_ns(str(target), verbose=0))
    err = capsys.readouterr().err
    assert "already formatted" in err


def test_format_check_mode_exits_violations(tmp_path: Path) -> None:
    """--check lists would-reformat files and exits 1.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.format_cmd import run_format

    target = tmp_path / "site.yml"
    target.write_text("a: 1\n", encoding="utf-8")
    resp = engine_pb2.FormatResponse(
        diffs=[FileDiff(path="site.yml", original=b"a", formatted=b"b", diff="d")],
        logs=[],
    )
    channel = MagicMock()
    with (
        patch("apme_engine.cli.format_cmd.yield_scan_chunks", return_value=iter([_scan_chunk()])),
        patch("apme_engine.cli.format_cmd.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.format_cmd.engine_pb2_grpc.EngineStub") as stub_cls,
        pytest.raises(SystemExit) as exc,
    ):
        stub_cls.return_value.FormatStream.return_value = resp
        run_format(_format_args_ns(str(target), check=True))
    assert exc.value.code == EXIT_VIOLATIONS


def test_format_apply_writes_files(tmp_path: Path) -> None:
    """--apply writes formatted bytes via _safe_write.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.format_cmd import run_format

    target = tmp_path / "site.yml"
    target.write_bytes(b"orig")
    resp = engine_pb2.FormatResponse(
        diffs=[FileDiff(path="site.yml", original=b"orig", formatted=b"new", diff="d")],
        logs=[],
    )
    channel = MagicMock()
    with (
        patch("apme_engine.cli.format_cmd.yield_scan_chunks", return_value=iter([_scan_chunk()])),
        patch("apme_engine.cli.format_cmd.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.format_cmd.engine_pb2_grpc.EngineStub") as stub_cls,
    ):
        stub_cls.return_value.FormatStream.return_value = resp
        run_format(_format_args_ns(str(target), apply=True))
    assert target.read_bytes() == b"new"


def test_format_show_diffs_without_apply(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Default mode prints diffs to stdout without writing.

    Args:
        tmp_path: Temporary directory fixture.
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli.format_cmd import run_format

    target = tmp_path / "site.yml"
    target.write_bytes(b"orig")
    resp = engine_pb2.FormatResponse(
        diffs=[FileDiff(path="site.yml", original=b"orig", formatted=b"new", diff="DIFF-TEXT")],
        logs=[],
    )
    channel = MagicMock()
    with (
        patch("apme_engine.cli.format_cmd.yield_scan_chunks", return_value=iter([_scan_chunk()])),
        patch("apme_engine.cli.format_cmd.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.format_cmd.engine_pb2_grpc.EngineStub") as stub_cls,
    ):
        stub_cls.return_value.FormatStream.return_value = resp
        run_format(_format_args_ns(str(target)))
    out = capsys.readouterr().out
    assert "DIFF-TEXT" in out
    assert target.read_bytes() == b"orig"


def test_format_safe_write_match_and_mismatch(tmp_path: Path) -> None:
    """_safe_write writes on match and skips on mismatch.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.format_cmd import _safe_write

    p = tmp_path / "f.yml"
    p.write_bytes(b"orig")
    _safe_write(p, b"orig", b"new")
    assert p.read_bytes() == b"new"
    _safe_write(p, b"stale", b"other")
    assert p.read_bytes() == b"new"


# ── suppress_cmd.py ─────────────────────────────────────────────────────


def test_suppress_requires_subcommand() -> None:
    """Missing subcommand exits with EXIT_ERROR."""
    from apme_engine.cli.suppress_cmd import run_suppress

    with pytest.raises(SystemExit) as exc:
        run_suppress(argparse.Namespace())
    assert exc.value.code == EXIT_ERROR


def test_suppress_add_invalid_fingerprint(tmp_path: Path) -> None:
    """Non-hex fingerprint exits with EXIT_ERROR.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.suppress_cmd import _suppress_add

    args = argparse.Namespace(
        target=str(tmp_path),
        rule_id="L001",
        mode="full",
        reason="",
        original_yaml="x: 1",
        module_fqcn="",
        fingerprint="not-hex",
    )
    with pytest.raises(SystemExit) as exc:
        _suppress_add(args)
    assert exc.value.code == EXIT_ERROR


def test_suppress_add_full_requires_original_yaml(tmp_path: Path) -> None:
    """Full mode without yaml or fingerprint exits with EXIT_ERROR.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.suppress_cmd import _suppress_add

    args = argparse.Namespace(
        target=str(tmp_path),
        rule_id="L001",
        mode="full",
        reason="",
        original_yaml=None,
        module_fqcn="",
        fingerprint=None,
    )
    with pytest.raises(SystemExit) as exc:
        _suppress_add(args)
    assert exc.value.code == EXIT_ERROR


def test_suppress_add_rule_module_requires_fqcn(tmp_path: Path) -> None:
    """rule_module mode without fqcn exits with EXIT_ERROR.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.suppress_cmd import _suppress_add

    args = argparse.Namespace(
        target=str(tmp_path),
        rule_id="L001",
        mode="rule_module",
        reason="",
        original_yaml="",
        module_fqcn="",
        fingerprint=None,
    )
    with pytest.raises(SystemExit) as exc:
        _suppress_add(args)
    assert exc.value.code == EXIT_ERROR


def test_suppress_add_with_explicit_fingerprint(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Explicit fingerprint add writes the entry.

    Args:
        tmp_path: Temporary directory fixture.
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli._suppressions import load_suppressions
    from apme_engine.cli.suppress_cmd import _suppress_add

    fp = "a" * 64
    args = argparse.Namespace(
        target=str(tmp_path),
        rule_id="L001",
        mode="full",
        reason="test",
        original_yaml="",
        module_fqcn="",
        fingerprint=fp,
    )
    _suppress_add(args)
    out = capsys.readouterr().out
    assert "Added suppression" in out
    assert len(load_suppressions(tmp_path)) == 1


def test_suppress_add_duplicate_reports_exists(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Adding the same fingerprint twice reports already-exists.

    Args:
        tmp_path: Temporary directory fixture.
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli.suppress_cmd import _suppress_add

    fp = "b" * 64
    args = argparse.Namespace(
        target=str(tmp_path),
        rule_id="L001",
        mode="full",
        reason="",
        original_yaml="",
        module_fqcn="",
        fingerprint=fp,
    )
    _suppress_add(args)
    capsys.readouterr()
    _suppress_add(args)
    assert "already exists" in capsys.readouterr().err


def test_suppress_add_computed_full_and_rule_module(tmp_path: Path) -> None:
    """Computed fingerprints work for full and rule_module modes.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli._suppressions import load_suppressions
    from apme_engine.cli.suppress_cmd import _suppress_add

    _suppress_add(
        argparse.Namespace(
            target=str(tmp_path),
            rule_id="L001",
            mode="full",
            reason="r",
            original_yaml="key: value",
            module_fqcn="",
            fingerprint=None,
        )
    )
    _suppress_add(
        argparse.Namespace(
            target=str(tmp_path),
            rule_id="L002",
            mode="rule_module",
            reason="",
            original_yaml="",
            module_fqcn="ansible.builtin.debug",
            fingerprint=None,
        )
    )
    assert len(load_suppressions(tmp_path)) == 2


def test_suppress_list_empty_and_nonempty(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """List reports empty state and then populated entries.

    Args:
        tmp_path: Temporary directory fixture.
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli.suppress_cmd import _suppress_add, _suppress_list

    _suppress_list(argparse.Namespace(target=str(tmp_path)))
    assert "No suppressions" in capsys.readouterr().out
    _suppress_add(
        argparse.Namespace(
            target=str(tmp_path),
            rule_id="L001",
            mode="rule_only",
            reason="why",
            original_yaml="",
            module_fqcn="",
            fingerprint="c" * 64,
        )
    )
    capsys.readouterr()
    _suppress_list(argparse.Namespace(target=str(tmp_path)))
    out = capsys.readouterr().out
    assert "suppression(s) total" in out
    assert "why" in out


def test_suppress_remove_no_match_exits_error(tmp_path: Path) -> None:
    """Unknown prefix exits with EXIT_ERROR.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.suppress_cmd import _suppress_remove

    with pytest.raises(SystemExit) as exc:
        _suppress_remove(argparse.Namespace(target=str(tmp_path), fingerprint="deadbeef"))
    assert exc.value.code == EXIT_ERROR


def test_suppress_remove_ambiguous_exits_error(tmp_path: Path) -> None:
    """Prefix matching multiple entries exits with EXIT_ERROR.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.suppress_cmd import _suppress_add, _suppress_remove

    _suppress_add(
        argparse.Namespace(
            target=str(tmp_path),
            rule_id="L001",
            mode="full",
            reason="",
            original_yaml="",
            module_fqcn="",
            fingerprint="d" * 64,
        )
    )
    _suppress_add(
        argparse.Namespace(
            target=str(tmp_path),
            rule_id="L002",
            mode="full",
            reason="",
            original_yaml="",
            module_fqcn="",
            fingerprint="d" * 63 + "e",
        )
    )
    with pytest.raises(SystemExit) as exc:
        _suppress_remove(argparse.Namespace(target=str(tmp_path), fingerprint="d"))
    assert exc.value.code == EXIT_ERROR


def test_suppress_remove_success(tmp_path: Path) -> None:
    """Exact prefix removal deletes the entry.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli._suppressions import load_suppressions
    from apme_engine.cli.suppress_cmd import _suppress_add, _suppress_remove

    fp = "e" * 64
    _suppress_add(
        argparse.Namespace(
            target=str(tmp_path),
            rule_id="L001",
            mode="full",
            reason="",
            original_yaml="",
            module_fqcn="",
            fingerprint=fp,
        )
    )
    _suppress_remove(argparse.Namespace(target=str(tmp_path), fingerprint=fp[:12]))
    assert load_suppressions(tmp_path) == []


def test_suppress_dispatch_add_list_remove(tmp_path: Path) -> None:
    """run_suppress dispatches to add, list, and remove.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.suppress_cmd import run_suppress

    fp = "f" * 64
    run_suppress(
        argparse.Namespace(
            suppress_command="add",
            target=str(tmp_path),
            rule_id="L001",
            mode="full",
            reason="",
            original_yaml="",
            module_fqcn="",
            fingerprint=fp,
        )
    )
    run_suppress(argparse.Namespace(suppress_command="list", target=str(tmp_path)))
    run_suppress(argparse.Namespace(suppress_command="remove", target=str(tmp_path), fingerprint=fp))


# ── check.py ────────────────────────────────────────────────────────────


def test_scan_summary_compat_none_and_report() -> None:
    """Compat wraps None as zeros and FixReport fields otherwise."""
    from apme_engine.cli.check import _ScanSummaryCompat

    empty = _ScanSummaryCompat(None)
    assert empty.auto_fixable == 0
    assert empty.by_resolution == {}
    full = _ScanSummaryCompat(FixReport(fixed=3, remaining_ai=1, remaining_manual=2))
    assert full.auto_fixable == 3
    assert full.ai_candidate == 1
    assert full.manual_review == 2


def test_resolve_session_id_variants(tmp_path: Path) -> None:
    """Explicit valid passes through; invalid exits; missing derives.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.check import _resolve_session_id

    assert _resolve_session_id(argparse.Namespace(session="ok-123_abc", target=str(tmp_path))) == "ok-123_abc"
    with pytest.raises(SystemExit) as exc:
        _resolve_session_id(argparse.Namespace(session="bad value!", target=str(tmp_path)))
    assert exc.value.code == EXIT_ERROR
    derived = _resolve_session_id(argparse.Namespace(session=None, target=str(tmp_path)))
    assert len(derived) == 16


def test_apply_dep_scan_flags_strips_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip flags pop daemon env vars and return booleans.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    from apme_engine.cli.check import _apply_dep_scan_flags

    monkeypatch.setenv("COLLECTION_HEALTH_GRPC_ADDRESS", "x")
    monkeypatch.setenv("DEP_AUDIT_GRPC_ADDRESS", "y")
    import os

    skip_c, skip_p = _apply_dep_scan_flags(argparse.Namespace(skip_dep_scan=True))
    assert (skip_c, skip_p) == (True, True)
    assert "COLLECTION_HEALTH_GRPC_ADDRESS" not in os.environ
    assert "DEP_AUDIT_GRPC_ADDRESS" not in os.environ
    skip_c2, skip_p2 = _apply_dep_scan_flags(
        argparse.Namespace(skip_dep_scan=False, skip_collection_scan=False, skip_python_audit=False)
    )
    assert (skip_c2, skip_p2) == (False, False)


def test_check_full_event_flow(tmp_path: Path) -> None:
    """run_check handles created/progress/tier1/proposals/triage/result/expiring/closed.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.check import run_check

    target = tmp_path / "site.yml"
    target.write_text("a: 1\n", encoding="utf-8")
    created = _mk_event("created")
    progress = _mk_event("progress")
    progress.progress.level = 4
    progress.progress.phase = "engine"
    progress.progress.message = "working"
    tier1 = _mk_event("tier1_complete")
    tier1.tier1_complete.report = FixReport(fixed=1, remaining_ai=0, remaining_manual=0)
    proposals = _mk_event("proposals")
    proposals.proposals.proposals = []
    triage = _mk_event("ai_triage")
    triage.ai_triage.candidates = []
    result = _mk_event("result")
    result.result.remaining_violations = []
    result.result.patches = []
    expiring = _mk_event("expiring")
    expiring.expiring.ttl_seconds = 30
    closed = _mk_event("closed")
    events = [created, progress, tier1, proposals, triage, result, expiring, closed]
    channel = MagicMock()
    stub = MagicMock()
    stub.FixSession.return_value = events
    chunk = _scan_chunk()
    with (
        patch("apme_engine.cli.check.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.check.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.check.load_rule_configs_from_project", return_value=[]),
        patch("apme_engine.cli.check.yield_scan_chunks", return_value=iter([chunk])),
        patch("apme_engine.cli.check.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.check.engine_pb2_grpc.EngineStub", return_value=stub),
    ):
        run_check(_check_args_ns(str(target), show_suppressed=True))


def test_check_progress_filtered_and_proposals_with_ids(tmp_path: Path) -> None:
    """Low-level progress is filtered; proposals send empty approval.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.check import run_check

    target = tmp_path / "site.yml"
    target.write_text("a: 1\n", encoding="utf-8")
    low = _mk_event("progress")
    low.progress.level = 1
    low.progress.phase = ""
    low.progress.message = "debug-noise"
    prop_ev = _mk_event("proposals")
    prop = MagicMock()
    prop.id = "p1"
    prop_ev.proposals.proposals = [prop]
    result = _mk_event("result")
    result.result.remaining_violations = []
    result.result.patches = []
    closed = _mk_event("closed")
    channel = MagicMock()
    stub = MagicMock()
    stub.FixSession.return_value = [low, prop_ev, result, closed]
    with (
        patch("apme_engine.cli.check.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.check.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.check.load_rule_configs_from_project", return_value=[]),
        patch("apme_engine.cli.check.yield_scan_chunks", return_value=iter([_scan_chunk()])),
        patch("apme_engine.cli.check.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.check.engine_pb2_grpc.EngineStub", return_value=stub),
    ):
        run_check(_check_args_ns(str(target), verbose=0, show_suppressed=True))


def test_check_grpc_error_exits(tmp_path: Path) -> None:
    """FixSession RpcError exits with EXIT_ERROR.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.check import run_check

    target = tmp_path / "site.yml"
    target.write_text("a: 1\n", encoding="utf-8")
    channel = MagicMock()
    stub = MagicMock()
    stub.FixSession.side_effect = _FakeRpcError(grpc.StatusCode.UNAVAILABLE, "down")
    with (
        patch("apme_engine.cli.check.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.check.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.check.load_rule_configs_from_project", return_value=[]),
        patch("apme_engine.cli.check.yield_scan_chunks", return_value=iter([_scan_chunk()])),
        patch("apme_engine.cli.check.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.check.engine_pb2_grpc.EngineStub", return_value=stub),
        pytest.raises(SystemExit) as exc,
    ):
        run_check(_check_args_ns(str(target)))
    assert exc.value.code == EXIT_ERROR


def test_check_no_result_exits(tmp_path: Path) -> None:
    """Empty event stream exits with EXIT_ERROR.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.check import run_check

    target = tmp_path / "site.yml"
    target.write_text("a: 1\n", encoding="utf-8")
    channel = MagicMock()
    stub = MagicMock()
    stub.FixSession.return_value = [_mk_event("created"), _mk_event("closed")]
    with (
        patch("apme_engine.cli.check.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.check.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.check.load_rule_configs_from_project", return_value=[]),
        patch("apme_engine.cli.check.yield_scan_chunks", return_value=iter([_scan_chunk()])),
        patch("apme_engine.cli.check.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.check.engine_pb2_grpc.EngineStub", return_value=stub),
        pytest.raises(SystemExit) as exc,
    ):
        run_check(_check_args_ns(str(target)))
    assert exc.value.code == EXIT_ERROR


def test_check_json_output_and_violations_exit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """--json prints counts/diffs and exits 1 when violations remain.

    Args:
        tmp_path: Temporary directory fixture.
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli.check import run_check

    target = tmp_path / "site.yml"
    target.write_text("a: 1\n", encoding="utf-8")
    result = _mk_event("result")
    result.result.remaining_violations = [object(), object()]
    mpatch = MagicMock()
    mpatch.path = "a.yml"
    mpatch.diff = "DIFF"
    result.result.patches = [mpatch]
    closed = _mk_event("closed")
    channel = MagicMock()
    stub = MagicMock()
    stub.FixSession.return_value = [result, closed]
    violation_dicts = [
        {"rule_id": "L001", "severity": "error", "remediation_class": "ai-candidate", "file": "a.yml", "line": 1},
        {"rule_id": "L002", "severity": "low", "remediation_class": "manual-review", "file": "b.yml", "line": 2},
    ]
    with (
        patch("apme_engine.cli.check.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.check.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.check.load_rule_configs_from_project", return_value=[]),
        patch("apme_engine.cli.check.yield_scan_chunks", return_value=iter([_scan_chunk()])),
        patch("apme_engine.cli.check.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.check.engine_pb2_grpc.EngineStub", return_value=stub),
        patch("apme_engine.cli.check.violation_proto_to_dict", side_effect=violation_dicts),
        pytest.raises(SystemExit) as exc,
    ):
        run_check(_check_args_ns(str(target), json=True, show_suppressed=True))
    assert exc.value.code == EXIT_VIOLATIONS
    doc = json.loads(capsys.readouterr().out)
    assert doc["count"] == 2
    assert doc["diffs"] == [{"path": "a.yml", "diff": "DIFF"}]


def test_check_diff_flag_and_suppressed_message(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """--diff prints patch diffs; suppressed violations emit a hint.

    Args:
        tmp_path: Temporary directory fixture.
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli._suppressions import SuppressionResult
    from apme_engine.cli.check import run_check

    target = tmp_path / "site.yml"
    target.write_text("a: 1\n", encoding="utf-8")
    result = _mk_event("result")
    result.result.remaining_violations = [object()]
    dpatch = MagicMock()
    dpatch.diff = "PATCH-DIFF"
    result.result.patches = [dpatch]
    closed = _mk_event("closed")
    channel = MagicMock()
    stub = MagicMock()
    stub.FixSession.return_value = [result, closed]
    active = [{"rule_id": "L001", "severity": "error", "file": "a.yml", "line": 1, "message": "m"}]
    suppressed = [{"rule_id": "L002", "severity": "low", "file": "b.yml", "line": 2, "message": "s"}]
    with (
        patch("apme_engine.cli.check.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.check.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.check.load_rule_configs_from_project", return_value=[]),
        patch("apme_engine.cli.check.yield_scan_chunks", return_value=iter([_scan_chunk()])),
        patch("apme_engine.cli.check.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.check.engine_pb2_grpc.EngineStub", return_value=stub),
        patch("apme_engine.cli.check.violation_proto_to_dict", side_effect=active),
        patch("apme_engine.cli.check.load_suppressions", return_value=[]),
        patch(
            "apme_engine.cli.check.apply_suppressions",
            return_value=SuppressionResult(active=active, suppressed=suppressed),  # type: ignore[arg-type]
        ),
        pytest.raises(SystemExit) as exc,
    ):
        run_check(_check_args_ns(str(target), diff=True, show_suppressed=False))
    assert exc.value.code == EXIT_VIOLATIONS
    captured = capsys.readouterr()
    assert "PATCH-DIFF" in captured.out
    assert "suppressed violation(s) hidden" in captured.err


def test_check_sarif_flag_success_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """--sarif with no violations prints a document and returns.

    Args:
        tmp_path: Temporary directory fixture.
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli.check import run_check

    target = tmp_path / "site.yml"
    target.write_text("a: 1\n", encoding="utf-8")
    result = _mk_event("result")
    result.result.remaining_violations = []
    result.result.patches = []
    channel = MagicMock()
    stub = MagicMock()
    stub.FixSession.return_value = [result, _mk_event("closed")]
    with (
        patch("apme_engine.cli.check.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.check.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.check.load_rule_configs_from_project", return_value=[]),
        patch("apme_engine.cli.check.yield_scan_chunks", return_value=iter([_scan_chunk()])),
        patch("apme_engine.cli.check.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.check.engine_pb2_grpc.EngineStub", return_value=stub),
    ):
        run_check(_check_args_ns(str(target), sarif=True, show_suppressed=True))
    doc = json.loads(capsys.readouterr().out)
    assert doc["version"] == "2.1.0"


# ── remediate.py ────────────────────────────────────────────────────────


def test_remediate_target_missing() -> None:
    """Nonexistent target exits with EXIT_ERROR."""
    from apme_engine.cli.remediate import run_remediate

    with pytest.raises(SystemExit) as exc:
        run_remediate(_rem_args("/nonexistent/path-xyz"))
    assert exc.value.code == EXIT_ERROR


def test_remediate_full_event_flow_text(tmp_path: Path) -> None:
    """All event branches run in text mode and exit 0 with no violations.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.remediate import run_remediate

    target = tmp_path
    created = _mk_event("created")
    progress_hi = _mk_event("progress")
    progress_hi.progress.level = 4
    progress_hi.progress.phase = "engine"
    progress_hi.progress.message = "oops"
    progress_lo = _mk_event("progress")
    progress_lo.progress.level = 1
    progress_lo.progress.phase = ""
    progress_lo.progress.message = "skip-me"
    tier1 = _mk_event("tier1_complete")
    tier1.tier1_complete.report = FixReport(fixed=1, remaining_ai=0, remaining_manual=0)
    tier1.tier1_complete.format_diffs = []
    tier1.tier1_complete.applied_patches = []
    tier1.tier1_complete.idempotency_ok = True
    proposals = _mk_event("proposals")
    proposals.proposals.proposals = []
    triage = _mk_event("ai_triage")
    cand = MagicMock()
    cand.path = "site.yml"
    triage.ai_triage.candidates = [cand]
    ack = _mk_event("approval_ack")
    ack.approval_ack.applied_count = 2
    result = _mk_event("result")
    result.result.remaining_violations = []
    result.result.patches = []
    expiring = _mk_event("expiring")
    expiring.expiring.ttl_seconds = 10
    data = _mk_event("data")
    data.data.kind = "custom"
    closed = _mk_event("closed")
    events = [created, progress_hi, progress_lo, tier1, proposals, triage, ack, result, expiring, data, closed]
    channel = MagicMock()
    stub = MagicMock()
    stub.FixSession.return_value = events
    with (
        patch("apme_engine.cli.remediate.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.remediate.derive_session_id", return_value="sess-1"),
        patch("apme_engine.cli.remediate.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.remediate.load_rule_configs_from_project", return_value=[]),
        patch(
            "apme_engine.cli.remediate.yield_scan_chunks",
            side_effect=lambda *a, **k: iter([_scan_chunk()]),
        ),
        patch("apme_engine.cli.remediate.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.remediate.engine_pb2_grpc.EngineStub", return_value=stub),
    ):
        run_remediate(_rem_args(str(target), verbose=2, show_suppressed=True))


def test_remediate_explicit_session_and_verbose_filter(tmp_path: Path) -> None:
    """Explicit session is used; low progress filtered at verbosity 0.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.remediate import run_remediate

    target = tmp_path
    prog = _mk_event("progress")
    prog.progress.level = 1
    prog.progress.phase = "x"
    prog.progress.message = "hidden"
    result = _mk_event("result")
    result.result.remaining_violations = []
    result.result.patches = []
    channel = MagicMock()
    stub = MagicMock()
    stub.FixSession.return_value = [prog, result, _mk_event("closed")]
    with (
        patch("apme_engine.cli.remediate.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.remediate.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.remediate.load_rule_configs_from_project", return_value=[]),
        patch(
            "apme_engine.cli.remediate.yield_scan_chunks",
            side_effect=lambda *a, **k: iter([_scan_chunk()]),
        ),
        patch("apme_engine.cli.remediate.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.remediate.engine_pb2_grpc.EngineStub", return_value=stub),
        patch("apme_engine.cli.remediate.derive_session_id") as der,
    ):
        run_remediate(_rem_args(str(target), session="explicit-s", verbose=0, show_suppressed=True))
        der.assert_not_called()


def test_remediate_error_event_exits(tmp_path: Path) -> None:
    """Error event exits with EXIT_ERROR.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.remediate import run_remediate

    err = _mk_event("error")
    err.error.code = "BUDGET"
    err.error.message = "nope"
    channel = MagicMock()
    stub = MagicMock()
    stub.FixSession.return_value = [err]
    with (
        patch("apme_engine.cli.remediate.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.remediate.derive_session_id", return_value="s"),
        patch("apme_engine.cli.remediate.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.remediate.load_rule_configs_from_project", return_value=[]),
        patch(
            "apme_engine.cli.remediate.yield_scan_chunks",
            side_effect=lambda *a, **k: iter([_scan_chunk()]),
        ),
        patch("apme_engine.cli.remediate.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.remediate.engine_pb2_grpc.EngineStub", return_value=stub),
        pytest.raises(SystemExit) as exc,
    ):
        run_remediate(_rem_args(str(tmp_path)))
    assert exc.value.code == EXIT_ERROR


def test_remediate_proposals_auto_approve(tmp_path: Path) -> None:
    """auto_approve sends all proposal ids without prompting.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.remediate import run_remediate

    prop1 = MagicMock()
    prop1.id = "a1"
    prop2 = MagicMock()
    prop2.id = "a2"
    proposals = _mk_event("proposals")
    proposals.proposals.proposals = [prop1, prop2]
    result = _mk_event("result")
    result.result.remaining_violations = []
    result.result.patches = []
    channel = MagicMock()
    stub = MagicMock()
    stub.FixSession.return_value = [proposals, result, _mk_event("closed")]
    with (
        patch("apme_engine.cli.remediate.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.remediate.derive_session_id", return_value="s"),
        patch("apme_engine.cli.remediate.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.remediate.load_rule_configs_from_project", return_value=[]),
        patch(
            "apme_engine.cli.remediate.yield_scan_chunks",
            side_effect=lambda *a, **k: iter([_scan_chunk()]),
        ),
        patch("apme_engine.cli.remediate.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.remediate.engine_pb2_grpc.EngineStub", return_value=stub),
        patch("apme_engine.cli.remediate._interactive_review") as review,
    ):
        run_remediate(_rem_args(str(tmp_path), auto_approve=True, show_suppressed=True))
        review.assert_not_called()


def test_remediate_proposals_json_skips_review(tmp_path: Path) -> None:
    """JSON mode approves nothing and skips the interactive prompt.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.remediate import run_remediate

    prop = MagicMock()
    prop.id = "x1"
    proposals = _mk_event("proposals")
    proposals.proposals.proposals = [prop]
    result = _mk_event("result")
    result.result.remaining_violations = []
    result.result.patches = []
    channel = MagicMock()
    stub = MagicMock()
    stub.FixSession.return_value = [proposals, result, _mk_event("closed")]
    with (
        patch("apme_engine.cli.remediate.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.remediate.derive_session_id", return_value="s"),
        patch("apme_engine.cli.remediate.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.remediate.load_rule_configs_from_project", return_value=[]),
        patch(
            "apme_engine.cli.remediate.yield_scan_chunks",
            side_effect=lambda *a, **k: iter([_scan_chunk()]),
        ),
        patch("apme_engine.cli.remediate.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.remediate.engine_pb2_grpc.EngineStub", return_value=stub),
        patch("apme_engine.cli.remediate._interactive_review") as review,
    ):
        run_remediate(_rem_args(str(tmp_path), json=True, show_suppressed=True))
        review.assert_not_called()


def test_remediate_proposals_interactive_review(tmp_path: Path) -> None:
    """Interactive proposals call _interactive_review when not auto/json.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.remediate import run_remediate

    prop = MagicMock()
    prop.id = "y1"
    proposals = _mk_event("proposals")
    proposals.proposals.proposals = [prop]
    result = _mk_event("result")
    result.result.remaining_violations = []
    result.result.patches = []
    channel = MagicMock()
    stub = MagicMock()
    stub.FixSession.return_value = [proposals, result, _mk_event("closed")]
    with (
        patch("apme_engine.cli.remediate.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.remediate.derive_session_id", return_value="s"),
        patch("apme_engine.cli.remediate.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.remediate.load_rule_configs_from_project", return_value=[]),
        patch(
            "apme_engine.cli.remediate.yield_scan_chunks",
            side_effect=lambda *a, **k: iter([_scan_chunk()]),
        ),
        patch("apme_engine.cli.remediate.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.remediate.engine_pb2_grpc.EngineStub", return_value=stub),
        patch("apme_engine.cli.remediate._interactive_review", return_value=["y1"]) as review,
    ):
        run_remediate(_rem_args(str(tmp_path), show_suppressed=True))
        review.assert_called_once()


def test_remediate_retry_on_transient_then_success(tmp_path: Path) -> None:
    """UNAVAILABLE before any result retries once and succeeds.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.remediate import run_remediate

    result = _mk_event("result")
    result.result.remaining_violations = []
    result.result.patches = []
    events2 = [result, _mk_event("closed")]
    channel1 = MagicMock()
    channel2 = MagicMock()
    stub = MagicMock()
    stub.FixSession.side_effect = [_FakeRpcError(grpc.StatusCode.UNAVAILABLE, "blip"), events2]
    with (
        patch("apme_engine.cli.remediate.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.remediate.derive_session_id", return_value="s"),
        patch("apme_engine.cli.remediate.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.remediate.load_rule_configs_from_project", return_value=[]),
        patch(
            "apme_engine.cli.remediate.yield_scan_chunks",
            side_effect=lambda *a, **k: iter([_scan_chunk()]),
        ),
        patch(
            "apme_engine.cli.remediate.resolve_engine",
            side_effect=[(channel1, "a1"), (channel2, "a2")],
        ),
        patch("apme_engine.cli.remediate.engine_pb2_grpc.EngineStub", return_value=stub),
        patch("apme_engine.cli.remediate.time.sleep", return_value=None),
        patch("apme_engine.cli.remediate.random.uniform", return_value=0.0),
    ):
        run_remediate(_rem_args(str(tmp_path), show_suppressed=True))
    channel1.close.assert_called_once()
    channel2.close.assert_called_once()


def test_remediate_deadline_exceeded_never_retries(tmp_path: Path) -> None:
    """DEADLINE_EXCEEDED never retries (server budget or user timeout).

    With ``stream_timeout=None`` a DEADLINE_EXCEEDED is the server
    adaptive budget expiring — retrying would re-run the whole scan and
    double load. Only UNAVAILABLE is retried.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.remediate import run_remediate

    for timeout in (None, 5):
        channel = MagicMock()
        stub = MagicMock()
        stub.FixSession.side_effect = _FakeRpcError(grpc.StatusCode.DEADLINE_EXCEEDED, "slow")
        with (
            patch("apme_engine.cli.remediate.discover_project_root", return_value=tmp_path),
            patch("apme_engine.cli.remediate.derive_session_id", return_value="s"),
            patch("apme_engine.cli.remediate.discover_galaxy_servers", return_value=[]),
            patch("apme_engine.cli.remediate.load_rule_configs_from_project", return_value=[]),
            patch(
                "apme_engine.cli.remediate.yield_scan_chunks",
                side_effect=lambda *a, **k: iter([_scan_chunk()]),
            ),
            patch("apme_engine.cli.remediate.resolve_engine", return_value=(channel, "addr")),
            patch("apme_engine.cli.remediate.engine_pb2_grpc.EngineStub", return_value=stub),
            patch("apme_engine.cli.remediate.time.sleep") as slp,
            pytest.raises(SystemExit) as exc,
        ):
            run_remediate(_rem_args(str(tmp_path), timeout=timeout))
        assert exc.value.code == EXIT_ERROR
        slp.assert_not_called()
        assert stub.FixSession.call_count == 1


def test_remediate_user_deadline_no_retry(tmp_path: Path) -> None:
    """DEADLINE_EXCEEDED with user --timeout does not retry.

    Kept as an alias for the never-retry behavior: DEADLINE_EXCEEDED is
    never retried regardless of ``--timeout`` (see
    ``test_remediate_deadline_exceeded_never_retries``).

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.remediate import run_remediate

    channel = MagicMock()
    stub = MagicMock()
    stub.FixSession.side_effect = _FakeRpcError(grpc.StatusCode.DEADLINE_EXCEEDED, "slow")
    with (
        patch("apme_engine.cli.remediate.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.remediate.derive_session_id", return_value="s"),
        patch("apme_engine.cli.remediate.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.remediate.load_rule_configs_from_project", return_value=[]),
        patch(
            "apme_engine.cli.remediate.yield_scan_chunks",
            side_effect=lambda *a, **k: iter([_scan_chunk()]),
        ),
        patch("apme_engine.cli.remediate.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.remediate.engine_pb2_grpc.EngineStub", return_value=stub),
        patch("apme_engine.cli.remediate.time.sleep") as slp,
        pytest.raises(SystemExit) as exc,
    ):
        run_remediate(_rem_args(str(tmp_path), timeout=5))
    assert exc.value.code == EXIT_ERROR
    slp.assert_not_called()


def test_remediate_producer_error_exits(tmp_path: Path) -> None:
    """Upload producer failure exits with EXIT_ERROR.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.remediate import run_remediate

    channel = MagicMock()
    stub = MagicMock()
    stub.FixSession.return_value = []
    with (
        patch("apme_engine.cli.remediate.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.remediate.derive_session_id", return_value="s"),
        patch("apme_engine.cli.remediate.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.remediate.load_rule_configs_from_project", return_value=[]),
        patch("apme_engine.cli.remediate.yield_scan_chunks", side_effect=RuntimeError("disk-gone")),
        patch("apme_engine.cli.remediate.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.remediate.engine_pb2_grpc.EngineStub", return_value=stub),
        pytest.raises(SystemExit) as exc,
    ):
        run_remediate(_rem_args(str(tmp_path)))
    assert exc.value.code == EXIT_ERROR


def test_remediate_producer_error_wins_over_result(tmp_path: Path) -> None:
    """Mid-upload producer death fails even when the server yields a result.

    A partial upload must never silently pass: the producer error takes
    precedence over an arrived result.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.remediate import run_remediate

    def _flaky_chunks(*a: object, **k: object) -> Iterator[ScanChunk]:
        """Yield one chunk, then fail like a mid-upload disk error.

        Args:
            *a: Positional args ignored (mirrors yield_scan_chunks).
            **k: Keyword args ignored (mirrors yield_scan_chunks).

        Yields:
            ScanChunk: A single upload chunk before failing.

        Raises:
            RuntimeError: Simulated mid-upload disk failure.
        """
        yield _scan_chunk("scan-prior")
        raise RuntimeError("mid-upload boom")

    result = _mk_event("result")
    result.result.remaining_violations = []
    result.result.patches = []
    channel = MagicMock()
    stub = MagicMock()
    stub.FixSession.return_value = [result, _mk_event("closed")]
    with (
        patch("apme_engine.cli.remediate.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.remediate.derive_session_id", return_value="s"),
        patch("apme_engine.cli.remediate.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.remediate.load_rule_configs_from_project", return_value=[]),
        patch("apme_engine.cli.remediate.yield_scan_chunks", side_effect=_flaky_chunks),
        patch("apme_engine.cli.remediate.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.remediate.engine_pb2_grpc.EngineStub", return_value=stub),
        pytest.raises(SystemExit) as exc,
    ):
        run_remediate(_rem_args(str(tmp_path), show_suppressed=True))
    assert exc.value.code == EXIT_ERROR


def test_remediate_producer_error_writes_nothing(tmp_path: Path) -> None:
    """A producer failure leaves disk untouched even with server patches.

    Patches stay buffered until the producer is verified clean, so a
    partial upload that still yields a result must not mutate files.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.remediate import run_remediate

    target_file = tmp_path / "site.yml"
    target_file.write_bytes(b"orig")

    def _flaky_chunks(*a: object, **k: object) -> Iterator[ScanChunk]:
        """Yield one chunk, then fail like a mid-upload disk error.

        Args:
            *a: Positional args ignored (mirrors yield_scan_chunks).
            **k: Keyword args ignored (mirrors yield_scan_chunks).

        Yields:
            ScanChunk: A single upload chunk before failing.

        Raises:
            RuntimeError: Simulated mid-upload disk failure.
        """
        yield _scan_chunk("scan-flaky")
        raise RuntimeError("mid-upload boom")

    result = _mk_event("result")
    result.result.remaining_violations = []
    result.result.patches = [
        FilePatch(path="site.yml", original=b"orig", patched=b"new", diff="d", applied_rules=["L001"]),
    ]
    channel = MagicMock()
    stub = MagicMock()
    stub.FixSession.return_value = [result, _mk_event("closed")]
    with (
        patch("apme_engine.cli.remediate.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.remediate.derive_session_id", return_value="s"),
        patch("apme_engine.cli.remediate.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.remediate.load_rule_configs_from_project", return_value=[]),
        patch("apme_engine.cli.remediate.yield_scan_chunks", side_effect=_flaky_chunks),
        patch("apme_engine.cli.remediate.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.remediate.engine_pb2_grpc.EngineStub", return_value=stub),
        pytest.raises(SystemExit) as exc,
    ):
        run_remediate(_rem_args(str(tmp_path), show_suppressed=True))
    assert exc.value.code == EXIT_ERROR
    assert target_file.read_bytes() == b"orig"


def test_remediate_post_result_transport_drop_uses_result(tmp_path: Path) -> None:
    """A transport drop after a result keeps the buffered result.

    The run completes on the success path instead of discarding the
    received result with an error exit.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.remediate import run_remediate

    result = _mk_event("result")
    result.result.remaining_violations = []
    result.result.patches = []

    def _drop_after_result(cmd_iter: Iterable[object], timeout: object = None) -> Iterator[MagicMock]:
        """Yield a result, then fail like a post-result transport drop.

        Args:
            cmd_iter: Command iterator from run_remediate.
            timeout: Stream timeout.

        Yields:
            MagicMock: The buffered result event before failing.

        Raises:
            _FakeRpcError: UNAVAILABLE transport drop after the result.
        """
        yield result
        raise _FakeRpcError(grpc.StatusCode.UNAVAILABLE, "drop")

    channel = MagicMock()
    stub = MagicMock()
    stub.FixSession.side_effect = _drop_after_result
    with (
        patch("apme_engine.cli.remediate.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.remediate.derive_session_id", return_value="s"),
        patch("apme_engine.cli.remediate.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.remediate.load_rule_configs_from_project", return_value=[]),
        patch(
            "apme_engine.cli.remediate.yield_scan_chunks",
            side_effect=lambda *a, **k: iter([_scan_chunk()]),
        ),
        patch("apme_engine.cli.remediate.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.remediate.engine_pb2_grpc.EngineStub", return_value=stub),
    ):
        run_remediate(_rem_args(str(tmp_path), show_suppressed=True))


def test_remediate_retry_logs_prior_scan_id_and_attempt(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Retry log carries the failed attempt's scan_id and attempt number.

    Two server-side scan rows result from one CLI invocation; the log
    must attribute the retry to the prior row. Each attempt mints a fresh
    scan_id, so the two attempts must differ.

    Args:
        tmp_path: Temporary directory fixture.
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli.remediate import run_remediate

    calls: list[int] = []
    minted: list[str] = []

    def _fresh_chunks(*a: object, **k: object) -> Iterator[ScanChunk]:
        """Mint a fresh scan_id per attempt, mirroring yield_scan_chunks.

        Args:
            *a: Positional args ignored (mirrors yield_scan_chunks).
            **k: Keyword args ignored (mirrors yield_scan_chunks).

        Returns:
            Iterator with a single chunk carrying the fresh scan_id.
        """
        sid = f"scan-prior-{len(minted) + 1}"
        minted.append(sid)
        return iter([_scan_chunk(sid)])

    def _blip(cmd_iter: Iterable[object], timeout: object = None) -> list[MagicMock]:
        """Drain one upload (pinning the attempt scan_id), then fail transiently.

        First call drains a single upload command so the producer has
        recorded the attempt scan_id, then raises; the second call
        returns the success events.

        Args:
            cmd_iter: Command iterator from run_remediate.
            timeout: Stream timeout.

        Returns:
            Success events on the retry call.

        Raises:
            _FakeRpcError: UNAVAILABLE transport blip before any result.
        """
        if not calls:
            calls.append(1)
            list(itertools.islice(cmd_iter, 1))
            raise _FakeRpcError(grpc.StatusCode.UNAVAILABLE, "blip")
        return events2

    result = _mk_event("result")
    result.result.remaining_violations = []
    result.result.patches = []
    events2 = [result, _mk_event("closed")]
    channel1 = MagicMock()
    channel2 = MagicMock()
    stub = MagicMock()
    stub.FixSession.side_effect = _blip
    with (
        patch("apme_engine.cli.remediate.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.remediate.derive_session_id", return_value="s"),
        patch("apme_engine.cli.remediate.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.remediate.load_rule_configs_from_project", return_value=[]),
        patch(
            "apme_engine.cli.remediate.yield_scan_chunks",
            side_effect=_fresh_chunks,
        ),
        patch(
            "apme_engine.cli.remediate.resolve_engine",
            side_effect=[(channel1, "a1"), (channel2, "a2")],
        ),
        patch("apme_engine.cli.remediate.engine_pb2_grpc.EngineStub", return_value=stub),
        patch("apme_engine.cli.remediate.time.sleep", return_value=None),
        patch("apme_engine.cli.remediate.random.uniform", return_value=0.0),
    ):
        run_remediate(_rem_args(str(tmp_path), show_suppressed=True))
    assert minted == ["scan-prior-1", "scan-prior-2"]
    assert minted[0] != minted[1]
    err = capsys.readouterr().err
    assert minted[0] in err
    assert "attempt 1" in err
    assert "retrying session once" in err


def test_remediate_no_result_exits(tmp_path: Path) -> None:
    """Stream ending without result exits with EXIT_ERROR.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.remediate import run_remediate

    channel = MagicMock()
    stub = MagicMock()
    stub.FixSession.return_value = [_mk_event("created"), _mk_event("closed")]
    with (
        patch("apme_engine.cli.remediate.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.remediate.derive_session_id", return_value="s"),
        patch("apme_engine.cli.remediate.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.remediate.load_rule_configs_from_project", return_value=[]),
        patch(
            "apme_engine.cli.remediate.yield_scan_chunks",
            side_effect=lambda *a, **k: iter([_scan_chunk()]),
        ),
        patch("apme_engine.cli.remediate.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.remediate.engine_pb2_grpc.EngineStub", return_value=stub),
        pytest.raises(SystemExit) as exc,
    ):
        run_remediate(_rem_args(str(tmp_path)))
    assert exc.value.code == EXIT_ERROR


def test_remediate_json_and_violations_exit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """JSON mode emits structured output and exits 1 on remaining violations.

    Args:
        tmp_path: Temporary directory fixture.
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli.remediate import run_remediate

    result = _mk_event("result")
    result.result.remaining_violations = [object()]
    result.result.patches = []
    channel = MagicMock()
    stub = MagicMock()
    stub.FixSession.return_value = [result, _mk_event("closed")]
    with (
        patch("apme_engine.cli.remediate.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.remediate.derive_session_id", return_value="s"),
        patch("apme_engine.cli.remediate.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.remediate.load_rule_configs_from_project", return_value=[]),
        patch(
            "apme_engine.cli.remediate.yield_scan_chunks",
            side_effect=lambda *a, **k: iter([_scan_chunk()]),
        ),
        patch("apme_engine.cli.remediate.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.remediate.engine_pb2_grpc.EngineStub", return_value=stub),
        patch(
            "apme_engine.cli.remediate.violation_proto_to_dict",
            return_value={"rule_id": "L001", "remediation_class": "ai-candidate", "file": "a.yml", "line": 1},
        ),
        pytest.raises(SystemExit) as exc,
    ):
        run_remediate(_rem_args(str(tmp_path), json=True, show_suppressed=True))
    assert exc.value.code == EXIT_VIOLATIONS
    doc = json.loads(capsys.readouterr().out)
    assert doc["count"] == 1
    assert "remediation_summary" in doc


def test_remediate_text_counts_and_suppressed_hint(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Text mode reports AI/manual counts and suppressed hint, then exits 1.

    Args:
        tmp_path: Temporary directory fixture.
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli._suppressions import SuppressionResult
    from apme_engine.cli.remediate import run_remediate

    result = _mk_event("result")
    result.result.remaining_violations = [object(), object()]
    result.result.patches = []
    channel = MagicMock()
    stub = MagicMock()
    stub.FixSession.return_value = [result, _mk_event("closed")]
    active = [
        {"rule_id": "L001", "remediation_class": "ai-candidate"},
        {"rule_id": "L002", "remediation_class": "manual-review"},
    ]
    with (
        patch("apme_engine.cli.remediate.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.remediate.derive_session_id", return_value="s"),
        patch("apme_engine.cli.remediate.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.remediate.load_rule_configs_from_project", return_value=[]),
        patch(
            "apme_engine.cli.remediate.yield_scan_chunks",
            side_effect=lambda *a, **k: iter([_scan_chunk()]),
        ),
        patch("apme_engine.cli.remediate.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.remediate.engine_pb2_grpc.EngineStub", return_value=stub),
        patch("apme_engine.cli.remediate.violation_proto_to_dict", side_effect=active),
        patch("apme_engine.cli.remediate.load_suppressions", return_value=[]),
        patch(
            "apme_engine.cli.remediate.apply_suppressions",
            return_value=SuppressionResult(active=active, suppressed=[{"rule_id": "L003"}]),  # type: ignore[arg-type]
        ),
        pytest.raises(SystemExit) as exc,
    ):
        run_remediate(_rem_args(str(tmp_path), show_suppressed=False))
    assert exc.value.code == EXIT_VIOLATIONS
    err = capsys.readouterr().err
    assert "Tier 2" in err
    assert "Tier 3" in err
    assert "suppressed" in err


def test_emit_json_writes_structured_output(capsys: pytest.CaptureFixture[str]) -> None:
    """_emit_json deduplicates, sorts, counts, and prints diffs.

    Args:
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli.remediate import _emit_json

    violations = [
        {"rule_id": "L002", "file": "b.yml", "line": 2, "remediation_class": "manual-review"},
        {"rule_id": "L001", "file": "a.yml", "line": 1, "remediation_class": "ai-candidate"},
        {"rule_id": "L001", "file": "a.yml", "line": 1, "remediation_class": "ai-candidate"},
    ]
    patches = [
        FilePatch(path="a.yml", original=b"o", patched=b"n", diff="D1", applied_rules=["L001"]),
        FilePatch(path="b.yml", original=b"o", patched=b"n", diff="", applied_rules=[]),
    ]
    _emit_json(violations, patches, FixReport(fixed=2))  # type: ignore[arg-type]
    doc = json.loads(capsys.readouterr().out)
    assert doc["count"] == 2
    assert doc["remediation_summary"]["auto_fixable"] == 2
    assert doc["diffs"] == [{"path": "a.yml", "diff": "D1"}]
    assert doc["files_updated"] == 2


def test_emit_json_no_report(capsys: pytest.CaptureFixture[str]) -> None:
    """_emit_json with None report reports zero auto-fixable.

    Args:
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli.remediate import _emit_json

    _emit_json([], [], None)
    doc = json.loads(capsys.readouterr().out)
    assert doc["count"] == 0
    assert doc["remediation_summary"]["auto_fixable"] == 0


def test_render_tier1_full(capsys: pytest.CaptureFixture[str]) -> None:
    """_render_tier1 covers format diffs, idempotency warning, oscillation, applied.

    Args:
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli.remediate import _render_tier1

    summary = Tier1Summary(
        format_diffs=[FileDiff(path="a.yml", original=b"o", formatted=b"n", diff="d")],
        applied_patches=[FilePatch(path="a.yml", original=b"o", patched=b"n", diff="d")],
        idempotency_ok=False,
        report=FixReport(passes=2, fixed=1, remaining_ai=1, remaining_manual=1, oscillation_detected=True),
    )
    _render_tier1(summary)
    err = capsys.readouterr().err
    assert "Formatted 1" in err
    assert "idempotent" in err
    assert "oscillation" in err
    assert "Tier 1 patch" in err


def test_render_tier1_empty(capsys: pytest.CaptureFixture[str]) -> None:
    """Unset report field produces no remediation line.

    Args:
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli.remediate import _render_tier1

    _render_tier1(Tier1Summary(idempotency_ok=True))
    err = capsys.readouterr().err
    assert "Remediation:" not in err
    assert "Formatted" not in err
    assert "idempotent" not in err
    assert "Applied" not in err


def test_render_tier1_no_report_object(capsys: pytest.CaptureFixture[str]) -> None:
    """Falsy report attribute skips the remediation line.

    Args:
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli.remediate import _render_tier1

    summary = MagicMock()
    summary.format_diffs = []
    summary.applied_patches = []
    summary.idempotency_ok = True
    summary.report = None
    _render_tier1(summary)
    assert capsys.readouterr().err == ""


def test_interactive_review_all_branches(capsys: pytest.CaptureFixture[str]) -> None:
    """Review loop covers y/n/a/s/q answers.

    Args:
        capsys: Pytest capture fixture.
    """
    from apme.v1.engine_pb2 import Proposal
    from apme_engine.cli.remediate import _interactive_review

    proposals = [
        Proposal(
            id="p1",
            rule_id="L001",
            file="a.yml",
            line_start=1,
            line_end=2,
            confidence=0.9,
            explanation="e",
            diff_hunk="d",
        ),
        Proposal(id="p2", rule_id="L002", file="b.yml", line_start=3, line_end=4),
        Proposal(id="p3", rule_id="L003", file="c.yml", line_start=5, line_end=6),
        Proposal(id="p4", rule_id="L004", file="d.yml", line_start=7, line_end=8),
        Proposal(id="p5", rule_id="L005", file="e.yml", line_start=9, line_end=10),
    ]
    with patch("apme_engine.cli.remediate._prompt_ynasq", side_effect=["y", "n", "s", "y", "q"]):
        approved = _interactive_review(proposals)
    assert approved == ["p1"]


def test_interactive_review_accept_all_and_quit() -> None:
    """'a' accepts the remainder; 'q' aborts the loop."""
    from apme.v1.engine_pb2 import Proposal
    from apme_engine.cli.remediate import _interactive_review

    proposals = [
        Proposal(id="p1", rule_id="L001", file="a.yml", line_start=1, line_end=2),
        Proposal(id="p2", rule_id="L002", file="b.yml", line_start=3, line_end=4),
    ]
    with patch("apme_engine.cli.remediate._prompt_ynasq", side_effect=["a"]):
        assert _interactive_review(proposals) == ["p1", "p2"]
    with patch("apme_engine.cli.remediate._prompt_ynasq", side_effect=["q"]):
        assert _interactive_review(proposals) == []


def test_prompt_ynasq_variants() -> None:
    """_prompt_ynasq maps yes/no/accept/skip/quit and reprompts invalid."""
    from apme_engine.cli.remediate import _prompt_ynasq

    with patch("builtins.input", return_value="yes"):
        assert _prompt_ynasq() == "y"
    with patch("builtins.input", return_value="NO"):
        assert _prompt_ynasq() == "n"
    with patch("builtins.input", return_value="accept"):
        assert _prompt_ynasq() == "a"
    with patch("builtins.input", return_value="skip"):
        assert _prompt_ynasq() == "s"
    with patch("builtins.input", return_value="quit"):
        assert _prompt_ynasq() == "q"
    with patch("builtins.input", side_effect=["bogus", "y"]):
        assert _prompt_ynasq() == "y"
    with patch("builtins.input", side_effect=EOFError):
        assert _prompt_ynasq() == "q"
    with patch("builtins.input", side_effect=KeyboardInterrupt):
        assert _prompt_ynasq() == "q"


def test_write_patches_dir_and_file_targets(tmp_path: Path) -> None:
    """_write_patches writes via target dir join and via direct file target.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.remediate import _write_patches

    dfile = tmp_path / "play.yml"
    dfile.write_bytes(b"orig")
    patches = [FilePatch(path="play.yml", original=b"orig", patched=b"new", diff="d", applied_rules=["L001"])]
    _write_patches(tmp_path, patches)
    assert dfile.read_bytes() == b"new"
    solo = tmp_path / "solo.yml"
    solo.write_bytes(b"o2")
    _write_patches(solo, [FilePatch(path="ignored.yml", original=b"o2", patched=b"n2", diff="d")])
    assert solo.read_bytes() == b"n2"


def test_write_patches_oserror_skips(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """OSError from _safe_write is reported and skipped.

    Args:
        tmp_path: Temporary directory fixture.
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli.remediate import _write_patches

    patches = [FilePatch(path="x.yml", original=b"o", patched=b"n", diff="d")]
    with patch("apme_engine.cli.remediate._safe_write", side_effect=OSError("denied")):
        written, failed = _write_patches(tmp_path, patches)
    assert written == 0
    assert failed is True
    assert "WARNING: skipping" in capsys.readouterr().err


def test_render_remaining_counts() -> None:
    """_render_remaining reports AI and manual buckets."""
    from apme.v1.common_pb2 import Violation
    from apme_engine.cli.remediate import _render_remaining

    result = SessionResult(
        remaining_violations=[
            Violation(rule_id="L1", remediation_class=common_pb2.REMEDIATION_CLASS_AI_CANDIDATE),  # type: ignore[attr-defined]
            Violation(rule_id="L2", remediation_class=common_pb2.REMEDIATION_CLASS_MANUAL_REVIEW),  # type: ignore[attr-defined]
        ]
    )
    _render_remaining(result)
    _render_remaining(SessionResult())


def test_rem_safe_write_match_and_skip(tmp_path: Path) -> None:
    """_safe_write writes on match and skips when modified.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.remediate import _safe_write

    p = tmp_path / "w.yml"
    p.write_bytes(b"orig")
    assert _safe_write(p, b"orig", b"new") is True
    assert p.read_bytes() == b"new"
    assert _safe_write(p, b"stale", b"other") is False
    assert p.read_bytes() == b"new"


def test_check_chunk_not_found_exits(tmp_path: Path) -> None:
    """Missing target chunks exit check with EXIT_ERROR.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.check import run_check

    target = tmp_path / "site.yml"
    target.write_text("a: 1\n", encoding="utf-8")
    with (
        patch("apme_engine.cli.check.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.check.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.check.load_rule_configs_from_project", return_value=[]),
        patch("apme_engine.cli.check.yield_scan_chunks", side_effect=FileNotFoundError("gone")),
        pytest.raises(SystemExit) as exc,
    ):
        run_check(_check_args_ns(str(target)))
    assert exc.value.code == EXIT_ERROR


def test_check_two_chunks_and_draining_stub(tmp_path: Path) -> None:
    """Two upload chunks cover first and subsequent producer iterations.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.check import run_check

    target = tmp_path / "site.yml"
    target.write_text("a: 1\n", encoding="utf-8")
    c1 = _scan_chunk("s1")
    c2 = _scan_chunk("s2")
    channel = MagicMock()
    stub = MagicMock()
    result = _mk_event("result")
    result.result.remaining_violations = []
    result.result.patches = []
    # NOTE: the stub intentionally does not drain cmd_iter. Draining here would
    # deadlock because check's producer never emits the None sentinel; the
    # sentinel is only queued in the finally block after FixSession returns.
    stub.FixSession.return_value = [result, _mk_event("closed")]
    with (
        patch("apme_engine.cli.check.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.check.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.check.load_rule_configs_from_project", return_value=[]),
        patch("apme_engine.cli.check.yield_scan_chunks", return_value=iter([c1, c2])),
        patch("apme_engine.cli.check.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.check.engine_pb2_grpc.EngineStub", return_value=stub),
    ):
        run_check(_check_args_ns(str(target), show_suppressed=True))


def test_remediate_two_chunks_and_draining_stub(tmp_path: Path) -> None:
    """Two scan chunks and a draining stub cover first/non-first upload paths.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.remediate import run_remediate

    c1 = _scan_chunk("s1")
    c2 = _scan_chunk("s2")
    channel = MagicMock()
    stub = MagicMock()

    def _drain_and_result(cmd_iter: Iterable[object], timeout: object = None) -> list[MagicMock]:
        """Drain uploads then emit an empty result.

        Only the two upload chunks are consumed: the producer no longer
        emits a terminating None on success, so a full ``list(cmd_iter)``
        would block forever waiting for the teardown sentinel.

        Args:
            cmd_iter: Command iterator from run_remediate.
            timeout: Stream timeout.

        Returns:
            Event list with result and close.

        """
        drained = list(itertools.islice(cmd_iter, 2))
        assert len(drained) >= 2
        result = _mk_event("result")
        result.result.remaining_violations = []
        result.result.patches = []
        return [result, _mk_event("closed")]

    stub.FixSession.side_effect = _drain_and_result
    with (
        patch("apme_engine.cli.remediate.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.remediate.derive_session_id", return_value="s"),
        patch("apme_engine.cli.remediate.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.remediate.load_rule_configs_from_project", return_value=[]),
        patch(
            "apme_engine.cli.remediate.yield_scan_chunks",
            side_effect=lambda *a, **k: iter([c1, c2]),
        ),
        patch("apme_engine.cli.remediate.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.remediate.engine_pb2_grpc.EngineStub", return_value=stub),
    ):
        run_remediate(_rem_args(str(tmp_path), show_suppressed=True))


def test_render_remaining_manual_only(capsys: pytest.CaptureFixture[str]) -> None:
    """Manual-only remaining skips the AI hint but shows the manual hint.

    Args:
        capsys: Pytest capture fixture.
    """
    from apme.v1.common_pb2 import Violation
    from apme_engine.cli.remediate import _render_remaining

    result = SessionResult(
        remaining_violations=[
            Violation(rule_id="L9", remediation_class=common_pb2.REMEDIATION_CLASS_MANUAL_REVIEW),  # type: ignore[attr-defined]
        ]
    )
    _render_remaining(result)
    err = capsys.readouterr().err
    assert "manual review" in err
    assert "Tier 2" not in err


def test_check_json_no_violations_returns_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """JSON mode with no violations prints zero count and returns.

    Args:
        tmp_path: Temporary directory fixture.
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli.check import run_check

    target = tmp_path / "site.yml"
    target.write_text("a: 1\n", encoding="utf-8")
    result = _mk_event("result")
    result.result.remaining_violations = []
    result.result.patches = []
    channel = MagicMock()
    stub = MagicMock()
    stub.FixSession.return_value = [result, _mk_event("closed")]
    with (
        patch("apme_engine.cli.check.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.check.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.check.load_rule_configs_from_project", return_value=[]),
        patch("apme_engine.cli.check.yield_scan_chunks", return_value=iter([_scan_chunk()])),
        patch("apme_engine.cli.check.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.check.engine_pb2_grpc.EngineStub", return_value=stub),
    ):
        run_check(_check_args_ns(str(target), json=True, show_suppressed=True))
    assert json.loads(capsys.readouterr().out)["count"] == 0


def test_remediate_producer_success_leaves_stream_open(tmp_path: Path) -> None:
    """Producer success leaves the FixSession stream open for approvals.

    Drains the two upload chunks then probes the request generator from a
    background thread: it must stay blocked (stream open) instead of
    raising StopIteration (premature half-close).

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.remediate import run_remediate

    c1 = _scan_chunk("s1")
    c2 = _scan_chunk("s2")
    channel = MagicMock()
    stub = MagicMock()
    probe_outcome: list[str] = []
    probe_thread_holder: list[threading.Thread] = []

    def _capture_and_probe(cmd_iter: Iterable[object], timeout: object = None) -> list[MagicMock]:
        """Drain uploads, probe openness, then emit an empty result.

        Args:
            cmd_iter: Command iterator from run_remediate.
            timeout: Stream timeout.

        Returns:
            Event list with result and close.
        """
        it: Iterator[object] = iter(cmd_iter)
        first = next(it)
        second = next(it)
        assert first is not None
        assert second is not None
        # Give a buggy producer time to enqueue its premature None sentinel;
        # with the fix no sentinel ever arrives on success.
        time.sleep(0.5)

        def _probe() -> None:
            """Attempt one more next; blocked means open, StopIteration means closed.

            Returns:
                None.
            """
            try:
                next(it)
            except StopIteration:
                probe_outcome.append("stopped")
            else:
                probe_outcome.append("value")

        probe = threading.Thread(target=_probe, daemon=True)
        probe_thread_holder.append(probe)
        probe.start()
        probe.join(timeout=0.5)
        assert probe.is_alive(), "request stream closed before approvals (premature None)"
        assert probe_outcome == []
        result = _mk_event("result")
        result.result.remaining_violations = []
        result.result.patches = []
        return [result, _mk_event("closed")]

    def _chunks(*args: object, **kwargs: object) -> Iterator[ScanChunk]:
        """Return two upload chunks.

        Args:
            *args: Positional chunk args.
            **kwargs: Chunk keyword args.

        Returns:
            Iterator of two ScanChunk protos.
        """
        return iter([c1, c2])

    stub.FixSession.side_effect = _capture_and_probe
    with (
        patch("apme_engine.cli.remediate.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.remediate.derive_session_id", return_value="s"),
        patch("apme_engine.cli.remediate.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.remediate.load_rule_configs_from_project", return_value=[]),
        patch("apme_engine.cli.remediate.yield_scan_chunks", side_effect=_chunks),
        patch("apme_engine.cli.remediate.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.remediate.engine_pb2_grpc.EngineStub", return_value=stub),
    ):
        run_remediate(_rem_args(str(tmp_path), show_suppressed=True))
    for probe in probe_thread_holder:
        probe.join(timeout=5.0)


def test_remediate_producer_error_terminates_stream(tmp_path: Path) -> None:
    """Producer error terminates the request stream with a sentinel.

    Args:
        tmp_path: Temporary directory fixture.

    Raises:
        AssertionError: If remediate does not exit as expected.
    """
    from apme_engine.cli.remediate import run_remediate

    channel = MagicMock()
    stub = MagicMock()

    def _capture_terminating(cmd_iter: Iterable[object], timeout: object = None) -> list[MagicMock]:
        """Drain the error-path stream; it must terminate via sentinel.

        Args:
            cmd_iter: Command iterator from run_remediate.
            timeout: Stream timeout.

        Returns:
            Empty event list (triggers no-result exit).
        """
        drained = list(cmd_iter)
        assert drained == []
        return []

    stub.FixSession.side_effect = _capture_terminating
    with (
        patch("apme_engine.cli.remediate.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.remediate.derive_session_id", return_value="s"),
        patch("apme_engine.cli.remediate.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.remediate.load_rule_configs_from_project", return_value=[]),
        patch("apme_engine.cli.remediate.yield_scan_chunks", side_effect=RuntimeError("disk-gone")),
        patch("apme_engine.cli.remediate.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.remediate.engine_pb2_grpc.EngineStub", return_value=stub),
        patch("apme_engine.cli.remediate.time.sleep", return_value=None),
    ):
        try:
            run_remediate(_rem_args(str(tmp_path), show_suppressed=True))
        except SystemExit as exc:
            assert exc.code == EXIT_ERROR
        else:
            raise AssertionError("expected SystemExit")


def test_remediate_retry_resets_state_and_mints_fresh_scan_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Retry resets per-attempt state and mints a fresh scan_id per attempt.

    First attempt reports tier1 fixed=99 then a transient UNAVAILABLE;
    second attempt reports a result with no tier1. JSON must show
    auto_fixable 0 (not stale 99). Each attempt calls
    ``yield_scan_chunks`` without a shared scan_id so the server mints a
    fresh session (``SessionStore.create`` has no dedup on ``scan_id``);
    the retry therefore produces distinct chunk scan_ids.

    Args:
        tmp_path: Temporary directory fixture.
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli.remediate import run_remediate

    tier1_ev = _mk_event("tier1_complete")
    tier1_ev.tier1_complete.report = FixReport(fixed=99, remaining_ai=0, remaining_manual=0)

    def _first_attempt() -> Iterator[MagicMock]:
        """Yield stale tier1 then raise transient UNAVAILABLE.

        Yields:
            MagicMock: Tier1 event from the failed first attempt.

        Raises:
            _FakeRpcError: Transient UNAVAILABLE to trigger retry.
        """
        yield tier1_ev
        raise _FakeRpcError(grpc.StatusCode.UNAVAILABLE, "blip")

    result = _mk_event("result")
    result.result.remaining_violations = []
    result.result.patches = []
    events2 = [result, _mk_event("closed")]
    channel1 = MagicMock()
    channel2 = MagicMock()
    stub = MagicMock()
    stub.FixSession.side_effect = [_first_attempt(), events2]
    seen_scan_kwargs: list[object] = []
    seen_chunk_ids: list[str] = []
    call_index = 0

    def _chunks(*args: object, **kwargs: object) -> Iterator[ScanChunk]:
        """Record scan_id kwarg and return one chunk with a fresh ID.

        Mimics real ``yield_scan_chunks`` (mints a fresh scan_id when the
        caller omits it) so the test proves attempts do not share an ID.

        Args:
            *args: Positional chunk args.
            **kwargs: Chunk keyword args.

        Returns:
            Iterator with a single ScanChunk carrying a fresh scan_id.
        """
        nonlocal call_index
        call_index += 1
        seen_scan_kwargs.append(kwargs.get("scan_id"))
        fresh_id = f"fresh-scan-{call_index}"
        seen_chunk_ids.append(fresh_id)
        return iter([_scan_chunk(fresh_id)])

    with (
        patch("apme_engine.cli.remediate.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.remediate.derive_session_id", return_value="s"),
        patch("apme_engine.cli.remediate.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.remediate.load_rule_configs_from_project", return_value=[]),
        patch("apme_engine.cli.remediate.yield_scan_chunks", side_effect=_chunks),
        patch(
            "apme_engine.cli.remediate.resolve_engine",
            side_effect=[(channel1, "a1"), (channel2, "a2")],
        ),
        patch("apme_engine.cli.remediate.engine_pb2_grpc.EngineStub", return_value=stub),
        patch("apme_engine.cli.remediate.time.sleep", return_value=None),
        patch("apme_engine.cli.remediate.random.uniform", return_value=0.0),
    ):
        run_remediate(_rem_args(str(tmp_path), json=True, show_suppressed=True))
    doc = json.loads(capsys.readouterr().out)
    assert doc["remediation_summary"]["auto_fixable"] == 0
    assert len(seen_scan_kwargs) == 2
    # No shared scan_id is passed: each attempt lets yield_scan_chunks mint one.
    assert seen_scan_kwargs[0] is None
    assert seen_scan_kwargs[1] is None
    assert len(seen_chunk_ids) == 2
    assert seen_chunk_ids[0] != ""
    assert seen_chunk_ids[0] != seen_chunk_ids[1]


def test_write_patches_stale_skip_not_counted(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Stale files skipped by _safe_write are not counted as updated.

    Args:
        tmp_path: Temporary directory fixture.
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli.remediate import _write_patches

    p = tmp_path / "stale.yml"
    p.write_bytes(b"changed")
    patches = [FilePatch(path="stale.yml", original=b"orig", patched=b"new", diff="d")]
    written, failed = _write_patches(tmp_path, patches)
    assert written == 0
    assert failed is True
    assert "Fixed:" not in capsys.readouterr().err


def test_write_patches_returns_written_count(tmp_path: Path) -> None:
    """_write_patches returns files actually written, skipping OSError.

    Args:
        tmp_path: Temporary directory fixture.
    """
    from apme_engine.cli.remediate import _write_patches

    patches = [
        FilePatch(path="a.yml", original=b"o", patched=b"n", diff="D1"),
        FilePatch(path="b.yml", original=b"o", patched=b"n", diff="D2"),
    ]
    with patch(
        "apme_engine.cli.remediate._safe_write",
        side_effect=[True, OSError("denied")],
    ):
        written, failed = _write_patches(tmp_path, patches)
    assert written == 1
    assert failed is True


def test_emit_json_uses_written_count(capsys: pytest.CaptureFixture[str]) -> None:
    """_emit_json prefers explicit written count over patch length.

    Args:
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli.remediate import _emit_json

    patches = [
        FilePatch(path="a.yml", original=b"o", patched=b"n", diff="D1"),
        FilePatch(path="b.yml", original=b"o", patched=b"n", diff="D2"),
    ]
    _emit_json([], patches, None, 1)
    assert json.loads(capsys.readouterr().out)["files_updated"] == 1
    _emit_json([], patches, None)
    assert json.loads(capsys.readouterr().out)["files_updated"] == 2


def test_remediate_json_files_updated_reflects_written(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """End-to-end JSON files_updated reflects actually written files.

    Args:
        tmp_path: Temporary directory fixture.
        capsys: Pytest capture fixture.
    """
    from apme_engine.cli.remediate import run_remediate

    result = _mk_event("result")
    result.result.remaining_violations = []
    result.result.patches = [
        FilePatch(path="a.yml", original=b"o", patched=b"n", diff="D1"),
        FilePatch(path="b.yml", original=b"o", patched=b"n", diff="D2"),
    ]
    channel = MagicMock()
    stub = MagicMock()
    stub.FixSession.return_value = [result, _mk_event("closed")]

    def _chunks(*args: object, **kwargs: object) -> Iterator[ScanChunk]:
        """Return one upload chunk.

        Args:
            *args: Positional chunk args.
            **kwargs: Chunk keyword args.

        Returns:
            Iterator with a single ScanChunk.
        """
        return iter([_scan_chunk()])

    with (
        patch("apme_engine.cli.remediate.discover_project_root", return_value=tmp_path),
        patch("apme_engine.cli.remediate.derive_session_id", return_value="s"),
        patch("apme_engine.cli.remediate.discover_galaxy_servers", return_value=[]),
        patch("apme_engine.cli.remediate.load_rule_configs_from_project", return_value=[]),
        patch("apme_engine.cli.remediate.yield_scan_chunks", side_effect=_chunks),
        patch("apme_engine.cli.remediate.resolve_engine", return_value=(channel, "addr")),
        patch("apme_engine.cli.remediate.engine_pb2_grpc.EngineStub", return_value=stub),
        patch(
            "apme_engine.cli.remediate._safe_write",
            side_effect=[True, OSError("denied")],
        ),
        pytest.raises(SystemExit) as exc,
    ):
        run_remediate(_rem_args(str(tmp_path), json=True, show_suppressed=True))
    assert exc.value.code == EXIT_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["files_updated"] == 1
