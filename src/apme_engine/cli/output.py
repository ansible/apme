"""Shared output rendering for CLI: violation tables, diagnostics, diffs."""

from __future__ import annotations

import sys
from collections.abc import Iterable
from typing import cast

from apme.v1.common_pb2 import ProgressUpdate
from apme.v1.primary_pb2 import ScanDiagnostics
from apme_engine.cli._models import ViolationDict, YAMLDict
from apme_engine.cli.ansi import (
    TREE_LAST,
    TREE_MID,
    TREE_PIPE,
    TREE_SPACE,
    bold,
    box,
    cyan,
    dim,
    gray,
    green,
    magenta,
    red,
    remediation_badge,
    severity_badge,
    severity_indicator,
    table,
    yellow,
)


def render_logs(logs: Iterable[ProgressUpdate], verbosity: int) -> None:
    """Render ProgressUpdate log entries to stderr based on verbosity level.

    Verbosity mapping:
        0 (no flag)  -> show WARNING and above (level >= 3)
        1 (-v)       -> show INFO and above (level >= 2)
        2+ (-vv)     -> show everything including DEBUG (level >= 1)

    Args:
        logs: Iterable of ProgressUpdate protos with ``level``, ``phase``, ``message``.
        verbosity: CLI verbosity count (0, 1, 2+).
    """
    min_level = {0: 3, 1: 2}.get(verbosity, 1)
    for log in logs:
        if log.level >= min_level:
            prefix = dim(f"[{log.phase}]") if log.phase else ""
            sys.stderr.write(f"  {prefix} {log.message}\n")


def sort_violations(violations: list[ViolationDict]) -> list[ViolationDict]:
    """Sort violations by file path and line for stable display.

    Args:
        violations: Violation dicts to sort.

    Returns:
        Sorted list of violations.
    """

    def key(v: ViolationDict) -> tuple[str, int | float]:
        f = str(v.get("file") or "")
        line = v.get("line")
        resolved: int | float = 0
        if isinstance(line, int | float):
            resolved = line
        elif isinstance(line, list | tuple) and line:
            first = line[0]
            resolved = first if isinstance(first, int | float) else 0
        return (f, resolved)

    return sorted(violations, key=key)


def deduplicate_violations(violations: list[ViolationDict]) -> list[ViolationDict]:
    """Drop duplicate violations that share rule id, file, and line.

    Args:
        violations: Violation dicts to deduplicate.

    Returns:
        Deduplicated list.
    """
    seen: set[tuple[str, str, str | int | list[int] | tuple[int, ...] | bool | None]] = set()
    out: list[ViolationDict] = []
    for v in violations:
        line: str | int | list[int] | tuple[int, ...] | bool | None = v.get("line")
        if isinstance(line, list | tuple):
            line = tuple(line)
        dedup_key = (str(v.get("rule_id", "")), str(v.get("file", "")), line)
        if dedup_key not in seen:
            seen.add(dedup_key)
            out.append(v)
    return out


def fmt_ms(ms: float) -> str:
    """Format a duration in milliseconds for human-readable output.

    Args:
        ms: Duration in milliseconds.

    Returns:
        Human-readable duration string.
    """
    if ms < 1:
        return "<1ms"
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.1f}s"


def count_by_severity(violations: list[ViolationDict]) -> dict[str, int]:
    """Count violations grouped into error, warning, info, and hint buckets.

    Args:
        violations: Violation dicts to count.

    Returns:
        Dict mapping severity bucket to count.
    """
    counts = {"error": 0, "warning": 0, "info": 0, "hint": 0}
    for v in violations:
        level = str(v.get("level") or "").lower()
        if level in ("very_high", "high", "error"):
            counts["error"] += 1
        elif level in ("medium", "low", "warning"):
            counts["warning"] += 1
        elif level == "info":
            counts["info"] += 1
        else:
            counts["hint"] += 1
    return counts


def format_remediation_summary(
    summary: object | None = None,
) -> str:
    """Format remediation counts from a ScanSummary proto (server-provided).

    Args:
        summary: ScanSummary proto or None.

    Returns:
        Formatted remediation summary string.
    """
    if summary is None:
        return "none"
    parts = []
    auto = getattr(summary, "auto_fixable", 0)
    ai = getattr(summary, "ai_candidate", 0)
    manual = getattr(summary, "manual_review", 0)
    if auto:
        parts.append(green(f"{auto} auto-fixable"))
    if ai:
        parts.append(cyan(f"{ai} AI-candidate"))
    if manual:
        parts.append(magenta(f"{manual} manual-review"))

    by_res = getattr(summary, "by_resolution", {})
    res_parts = []
    for res_name, cnt in sorted(by_res.items()):
        if res_name == "unresolved":
            continue
        if cnt:
            res_parts.append(f"{cnt} {res_name}")
    if res_parts:
        parts.append(dim("(" + ", ".join(res_parts) + ")"))

    return ", ".join(parts) if parts else "none"


def render_check_results(
    violations: list[ViolationDict],
    scan_id: str = "",
    scan_time_ms: float | None = None,
    summary: object | None = None,
) -> None:
    """Print scan summary, issue table, and per-file breakdown to stdout.

    Args:
        violations: Violations to render.
        scan_id: Optional scan identifier for the header.
        scan_time_ms: Optional total scan time in milliseconds.
        summary: Optional remediation summary proto from the server.
    """
    counts = count_by_severity(violations)
    has_errors = counts["error"] > 0
    passed = not has_errors

    status = green(bold("PASSED")) if passed else red(bold("FAILED"))
    summary_lines = [f"Status: {status}"]

    if scan_id:
        summary_lines.append(f"Scan ID: {dim(scan_id)}")

    counts_line = []
    if counts["error"]:
        counts_line.append(red(f"{counts['error']} error(s)"))
    if counts["warning"]:
        counts_line.append(yellow(f"{counts['warning']} warning(s)"))
    if counts["info"]:
        counts_line.append(magenta(f"{counts['info']} info(s)"))
    if counts["hint"]:
        counts_line.append(cyan(f"{counts['hint']} hint(s)"))
    if counts_line:
        summary_lines.append("Issues: " + ", ".join(counts_line))
    else:
        summary_lines.append(green("No issues found"))

    if violations:
        summary_lines.append("Remediation: " + format_remediation_summary(summary))

    if scan_time_ms is not None:
        summary_lines.append(f"Time: {fmt_ms(scan_time_ms)}")

    print(box("\n".join(summary_lines), title="Check Results"))
    print()

    if not violations:
        return

    headers = ["Rule", "Severity", "Remediation", "Message", "Location"]
    rows = []
    for v in violations:
        rule_id = str(v.get("rule_id") or "?")
        level = str(v.get("level") or "none")
        rem_raw = v.get("remediation_class") or "ai-candidate"
        rem_class = rem_raw.value if hasattr(rem_raw, "value") else str(rem_raw)
        message = str(v.get("message") or "")
        if len(message) > 50:
            message = message[:47] + "..."

        file_path = str(v.get("file") or "")
        line = v.get("line")
        if isinstance(line, list | tuple) and len(line) >= 2:
            location = f"{file_path}:{line[0]}-{line[1]}"
        elif line is not None:
            location = f"{file_path}:{line}"
        else:
            location = file_path

        rows.append([rule_id, severity_badge(level), remediation_badge(rem_class), message, dim(location)])

    print(bold("Issues"))
    print(table(headers, rows))
    print()

    grouped: dict[str, list[ViolationDict]] = {}
    for v in violations:
        f = str(v.get("file") or "(unknown)")
        grouped.setdefault(f, []).append(v)

    files = sorted(grouped.keys())
    print(bold("Issues by File"))
    for i, f in enumerate(files):
        is_last_file = i == len(files) - 1
        file_prefix = TREE_LAST if is_last_file else TREE_MID
        file_violations = grouped[f]
        print(f"{file_prefix}{bold(f)} ({len(file_violations)})")

        for j, v in enumerate(file_violations):
            is_last_v = j == len(file_violations) - 1
            indent = TREE_SPACE if is_last_file else TREE_PIPE
            v_prefix = TREE_LAST if is_last_v else TREE_MID
            indicator = severity_indicator(str(v.get("level") or "none"))
            line = v.get("line")
            if isinstance(line, list | tuple) and len(line) >= 2:
                line_str = f"{line[0]}-{line[1]}"
            elif isinstance(line, list | tuple):
                line_str = str(line[0])
            elif line is not None:
                line_str = str(line)
            else:
                line_str = "?"
            rule_id = str(v.get("rule_id") or "?")
            print(f"{indent}{v_prefix}{indicator} {gray(f'L{line_str}')} [{rule_id}] {v.get('message', '')}")

    print()


def print_diagnostics_v(diag: ScanDiagnostics) -> None:
    """Print concise scan timing diagnostics (-v) to stderr.

    Args:
        diag: Scan timing diagnostics from the engine.
    """
    w = sys.stderr.write

    engine_detail = ""
    if diag.engine_parse_ms or diag.engine_annotate_ms:
        parts = []
        if diag.engine_parse_ms:
            parts.append(f"parse: {fmt_ms(diag.engine_parse_ms)}")
        if diag.engine_annotate_ms:
            parts.append(f"annotate: {fmt_ms(diag.engine_annotate_ms)}")
        engine_detail = f" ({', '.join(parts)})"
    w(f"\n  Engine:       {fmt_ms(diag.engine_total_ms)}{engine_detail}\n")

    if diag.files_scanned:
        w(f"  Files:        {diag.files_scanned}\n")

    w(f"  Fan-out:      {fmt_ms(diag.fan_out_ms)}\n")
    validators = list(diag.validators)
    for i, vd in enumerate(validators):
        connector = "\u2514\u2500\u2500" if i == len(validators) - 1 else "\u251c\u2500\u2500"
        meta_parts = []
        for k, v in sorted(vd.metadata.items()):
            if k not in ("opa_response_size", "files_written"):
                meta_parts.append(f"{k}={v}")
        meta_str = f" | {', '.join(meta_parts)}" if meta_parts else ""
        w(
            f"  {connector} {vd.validator_name.title():10s} {fmt_ms(vd.total_ms):>8s} | "
            f"{vd.violations_found:3d} violation(s){meta_str}\n"
        )

    w(f"  Total:        {fmt_ms(diag.total_ms)}\n")

    all_timings = []
    for vd in validators:
        for rt in vd.rule_timings:
            if rt.rule_id.startswith(("opa_query", "gitleaks_subprocess")):
                continue
            all_timings.append((rt.elapsed_ms, rt.rule_id, vd.validator_name, rt.violations))
    all_timings.sort(reverse=True)

    if all_timings:
        top = all_timings[:10]
        w("\n  Top slowest rules:\n")
        for rank, (ms, rid, vname, viols) in enumerate(top, 1):
            w(f"    {rank:2d}. {rid:15s} ({vname:8s}) {fmt_ms(ms):>8s}   {viols} violation(s)\n")
    w("\n")


def print_diagnostics_vv(diag: ScanDiagnostics) -> None:
    """Print detailed scan timing diagnostics (-vv) to stderr.

    Args:
        diag: Scan timing diagnostics from the engine.
    """
    w = sys.stderr.write

    engine_detail = ""
    if diag.engine_parse_ms or diag.engine_annotate_ms:
        parts = []
        if diag.engine_parse_ms:
            parts.append(f"parse: {fmt_ms(diag.engine_parse_ms)}")
        if diag.engine_annotate_ms:
            parts.append(f"annotate: {fmt_ms(diag.engine_annotate_ms)}")
        engine_detail = f" ({', '.join(parts)})"
    w(f"\n  Engine:       {fmt_ms(diag.engine_total_ms)}{engine_detail}")
    if diag.files_scanned:
        w(f", {diag.files_scanned} file(s)")
    if diag.graph_nodes_built:
        w(f", {diag.graph_nodes_built} graph node(s)")
    w("\n\n")

    for vd in diag.validators:
        w(f"  {vd.validator_name.title()} ({fmt_ms(vd.total_ms)}, {vd.violations_found} violation(s)):\n")
        for rt in vd.rule_timings:
            ms_str = fmt_ms(rt.elapsed_ms) if rt.elapsed_ms > 0 else "-"
            w(f"    {rt.rule_id:20s} {ms_str:>8s}   {rt.violations} violation(s)\n")
        if vd.metadata:
            meta = ", ".join(f"{k}={v}" for k, v in sorted(vd.metadata.items()))
            w(f"    metadata: {meta}\n")
        w("\n")

    w(f"  Fan-out:      {fmt_ms(diag.fan_out_ms)}\n")
    w(f"  Total:        {fmt_ms(diag.total_ms)}\n\n")


def diag_to_dict(diag: ScanDiagnostics) -> YAMLDict:
    """Convert scan diagnostics to a JSON-serializable dict structure.

    Args:
        diag: Scan timing diagnostics from the engine.

    Returns:
        Dict of diagnostic data suitable for JSON output.
    """
    validators = []
    for vd in diag.validators:
        validators.append(
            cast(
                YAMLDict,
                {
                    "validator_name": vd.validator_name,
                    "total_ms": round(vd.total_ms, 1),
                    "files_received": vd.files_received,
                    "violations_found": vd.violations_found,
                    "rule_timings": [
                        {"rule_id": rt.rule_id, "elapsed_ms": round(rt.elapsed_ms, 1), "violations": rt.violations}
                        for rt in vd.rule_timings
                    ],
                    "metadata": dict(vd.metadata),
                },
            )
        )
    return cast(
        YAMLDict,
        {
            "engine_parse_ms": round(diag.engine_parse_ms, 1),
            "engine_annotate_ms": round(diag.engine_annotate_ms, 1),
            "engine_total_ms": round(diag.engine_total_ms, 1),
            "files_scanned": diag.files_scanned,
            "graph_nodes_built": diag.graph_nodes_built,
            "total_violations": diag.total_violations,
            "fan_out_ms": round(diag.fan_out_ms, 1),
            "total_ms": round(diag.total_ms, 1),
            "validators": validators,
        },
    )
