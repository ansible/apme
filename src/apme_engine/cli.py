"""CLI: run engine + validators and print violations; collection cache commands; YAML formatting."""

import argparse
import json
import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import grpc

from apme.v1 import primary_pb2_grpc
from apme.v1.primary_pb2 import ScanDiagnostics
from apme_engine.ansi import (
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
from apme_engine.collection_cache import (
    get_cache_root,
    pull_galaxy_collection,
    pull_galaxy_requirements,
    pull_github_org,
    pull_github_repos,
)
from apme_engine.daemon.chunked_fs import yield_scan_chunks
from apme_engine.daemon.health_check import run_health_checks
from apme_engine.daemon.violation_convert import violation_proto_to_dict
from apme_engine.engine.models import RemediationClass, RemediationResolution, ViolationDict, YAMLDict
from apme_engine.engine.node_index import NodeIndex
from apme_engine.formatter import format_directory, format_file
from apme_engine.remediation.ai_provider import AIPatch, AIProposal, apply_patches
from apme_engine.remediation.engine import RemediationEngine
from apme_engine.remediation.partition import (
    add_classification_to_violations,
    count_by_remediation_class,
    count_by_resolution,
)
from apme_engine.remediation.transforms import build_default_registry
from apme_engine.runner import run_scan
from apme_engine.validators.ansible import AnsibleValidator
from apme_engine.validators.ansible._venv import DEFAULT_VERSION as ANSIBLE_DEFAULT_VERSION
from apme_engine.validators.ansible._venv import release_session, resolve_session, resolve_venv_root
from apme_engine.validators.native import NativeValidator
from apme_engine.validators.opa import OpaValidator


def _sort_violations(violations: list[ViolationDict]) -> list[ViolationDict]:
    """Sort by file, then line for stable output.

    Args:
        violations: List of violation dicts from validators.

    Returns:
        The same violations sorted by file path then line number.

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


def _deduplicate_violations(violations: list[ViolationDict]) -> list[ViolationDict]:
    """Remove duplicate violations sharing the same (rule_id, file, line).

    Args:
        violations: List of violation dicts, may contain duplicates.

    Returns:
        New list with duplicates removed, first occurrence kept.

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


def _fmt_ms(ms: float) -> str:
    """Format milliseconds for human display.

    Args:
        ms: Duration in milliseconds.

    Returns:
        Human-readable string such as '<1ms', '42ms', or '1.2s'.

    """
    if ms < 1:
        return "<1ms"
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.1f}s"


def _print_diagnostics_v(diag: ScanDiagnostics) -> None:
    """Print -v level diagnostics: validator summaries + top 10 slowest rules.

    Args:
        diag: Scan diagnostics from the primary daemon.

    """
    w = sys.stderr.write

    engine_detail = ""
    if diag.engine_parse_ms or diag.engine_annotate_ms:
        parts = []
        if diag.engine_parse_ms:
            parts.append(f"parse: {_fmt_ms(diag.engine_parse_ms)}")
        if diag.engine_annotate_ms:
            parts.append(f"annotate: {_fmt_ms(diag.engine_annotate_ms)}")
        engine_detail = f" ({', '.join(parts)})"
    w(f"\n  Engine:       {_fmt_ms(diag.engine_total_ms)}{engine_detail}\n")

    if diag.files_scanned:
        w(f"  Files:        {diag.files_scanned}\n")

    w(f"  Fan-out:      {_fmt_ms(diag.fan_out_ms)}\n")
    validators = list(diag.validators)
    for i, vd in enumerate(validators):
        connector = "\u2514\u2500\u2500" if i == len(validators) - 1 else "\u251c\u2500\u2500"
        meta_parts = []
        for k, v in sorted(vd.metadata.items()):
            if k not in ("opa_response_size", "files_written"):
                meta_parts.append(f"{k}={v}")
        meta_str = f" | {', '.join(meta_parts)}" if meta_parts else ""
        w(
            f"  {connector} {vd.validator_name.title():10s} {_fmt_ms(vd.total_ms):>8s} | "
            f"{vd.violations_found:3d} violation(s){meta_str}\n"
        )

    w(f"  Total:        {_fmt_ms(diag.total_ms)}\n")

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
            w(f"    {rank:2d}. {rid:15s} ({vname:8s}) {_fmt_ms(ms):>8s}   {viols} violation(s)\n")
    w("\n")


def _print_diagnostics_vv(diag: ScanDiagnostics) -> None:
    """Print -vv level diagnostics: full per-rule breakdown for every validator.

    Args:
        diag: Scan diagnostics from the primary daemon.

    """
    w = sys.stderr.write

    engine_detail = ""
    if diag.engine_parse_ms or diag.engine_annotate_ms:
        parts = []
        if diag.engine_parse_ms:
            parts.append(f"parse: {_fmt_ms(diag.engine_parse_ms)}")
        if diag.engine_annotate_ms:
            parts.append(f"annotate: {_fmt_ms(diag.engine_annotate_ms)}")
        engine_detail = f" ({', '.join(parts)})"
    w(f"\n  Engine:       {_fmt_ms(diag.engine_total_ms)}{engine_detail}")
    if diag.files_scanned:
        w(f", {diag.files_scanned} file(s)")
    if diag.trees_built:
        w(f", {diag.trees_built} tree(s)")
    w("\n\n")

    for vd in diag.validators:
        w(f"  {vd.validator_name.title()} ({_fmt_ms(vd.total_ms)}, {vd.violations_found} violation(s)):\n")
        for rt in vd.rule_timings:
            ms_str = _fmt_ms(rt.elapsed_ms) if rt.elapsed_ms > 0 else "-"
            w(f"    {rt.rule_id:20s} {ms_str:>8s}   {rt.violations} violation(s)\n")
        if vd.metadata:
            meta = ", ".join(f"{k}={v}" for k, v in sorted(vd.metadata.items()))
            w(f"    metadata: {meta}\n")
        w("\n")

    w(f"  Fan-out:      {_fmt_ms(diag.fan_out_ms)}\n")
    w(f"  Total:        {_fmt_ms(diag.total_ms)}\n\n")


def _diag_to_dict(diag: ScanDiagnostics) -> YAMLDict:
    """Convert ScanDiagnostics proto to a JSON-serializable dict.

    Args:
        diag: Scan diagnostics from the primary daemon.

    Returns:
        A dict suitable for JSON serialization with engine and validator timings.

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
            "trees_built": diag.trees_built,
            "total_violations": diag.total_violations,
            "fan_out_ms": round(diag.fan_out_ms, 1),
            "total_ms": round(diag.total_ms, 1),
            "validators": validators,
        },
    )


def _count_by_severity(violations: list[ViolationDict]) -> dict[str, int]:
    """Count violations by severity level.

    Args:
        violations: List of violation dictionaries.

    Returns:
        Counts keyed by severity bucket.
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


def _format_remediation_summary(violations: list[ViolationDict]) -> str:
    """Format remediation summary line.

    Args:
        violations: List of violations with remediation_class and remediation_resolution fields.

    Returns:
        Summary string like "3 auto-fixable, 2 AI-candidate, 1 manual-review".
    """
    counts = count_by_remediation_class(violations)
    parts = []
    if counts[RemediationClass.AUTO_FIXABLE.value]:
        parts.append(green(f"{counts[RemediationClass.AUTO_FIXABLE.value]} auto-fixable"))
    if counts[RemediationClass.AI_CANDIDATE.value]:
        parts.append(cyan(f"{counts[RemediationClass.AI_CANDIDATE.value]} AI-candidate"))
    if counts[RemediationClass.MANUAL_REVIEW.value]:
        parts.append(magenta(f"{counts[RemediationClass.MANUAL_REVIEW.value]} manual-review"))

    res_counts = count_by_resolution(violations)
    res_parts = []
    for res_val in RemediationResolution:
        if res_val == RemediationResolution.UNRESOLVED:
            continue
        cnt = res_counts.get(res_val.value, 0)
        if cnt:
            res_parts.append(f"{cnt} {res_val.value}")
    if res_parts:
        parts.append(dim("(" + ", ".join(res_parts) + ")"))

    return ", ".join(parts) if parts else "none"


def _group_by_file(violations: list[ViolationDict]) -> dict[str, list[ViolationDict]]:
    """Group violations by file.

    Args:
        violations: List of violation dictionaries.

    Returns:
        Violations grouped by file path.
    """
    grouped: dict[str, list[ViolationDict]] = {}
    for v in violations:
        f = str(v.get("file") or "(unknown)")
        if f not in grouped:
            grouped[f] = []
        grouped[f].append(v)
    return grouped


def _render_scan_results(
    violations: list[ViolationDict],
    scan_id: str = "",
    scan_time_ms: float | None = None,
) -> None:
    """Render scan results with ANSI styling.

    Displays:
    1. Summary box with pass/fail status and counts
    2. Issues table with rule, severity badge, message, location
    3. Issues-by-file tree view

    Args:
        violations: List of violation dictionaries.
        scan_id: Scan identifier for display.
        scan_time_ms: Scan duration in milliseconds.
    """
    counts = _count_by_severity(violations)
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
        summary_lines.append("Remediation: " + _format_remediation_summary(violations))

    if scan_time_ms is not None:
        summary_lines.append(f"Time: {_fmt_ms(scan_time_ms)}")

    print(box("\n".join(summary_lines), title="Scan Results"))
    print()

    if not violations:
        return

    headers = ["Rule", "Severity", "Remediation", "Message", "Location"]
    rows = []
    for v in violations:
        rule_id = str(v.get("rule_id") or "?")
        level = str(v.get("level") or "none")
        rem_raw = v.get("remediation_class") or RemediationClass.AI_CANDIDATE
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

    grouped = _group_by_file(violations)
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


def _run_scan(args: argparse.Namespace) -> None:
    """Run scan: engine + validators on target path, or call Primary daemon over gRPC.

    Args:
        args: Parsed CLI namespace with target, primary_addr, json, no_opa, no_native, opa_bundle, etc.

    """
    primary_addr = getattr(args, "primary_addr", None) or os.environ.get("APME_PRIMARY_ADDRESS")
    if primary_addr:
        _run_scan_grpc(args, primary_addr)
        return

    repo_root = Path(__file__).resolve().parent.parent

    try:
        context = run_scan(args.target, str(repo_root), include_scandata=True)
    except Exception as e:
        sys.stderr.write(f"Scan failed: {e}\n")
        sys.exit(1)

    if not context.hierarchy_payload:
        sys.stderr.write("No hierarchy payload from engine (no contexts?).\n")
        if args.json:
            print(json.dumps({"violations": [], "hierarchy_payload": context.hierarchy_payload}))
        sys.exit(0)

    validators: list[tuple[str, OpaValidator | NativeValidator | AnsibleValidator]] = []
    if not args.no_opa:
        validators.append(("OPA", OpaValidator(args.opa_bundle)))
    if not args.no_native:
        validators.append(("Native", NativeValidator()))
    if not getattr(args, "no_ansible", False):
        av = getattr(args, "ansible_version", None) or ANSIBLE_DEFAULT_VERSION
        collections = getattr(args, "collections", None)
        sid = getattr(args, "session", None)
        venv_root = resolve_venv_root(av, collection_specs=collections, session_id=sid)
        if venv_root is not None:
            validators.append(("Ansible", AnsibleValidator(venv_root)))
        else:
            sys.stderr.write(f"Ansible validator skipped: no venv found for ansible-core {av}\n")

    violations: list[ViolationDict] = []
    for name, v in validators:
        result = v.run(context)
        sys.stderr.write(f"{name}: {len(result)} violation(s)\n")
        violations.extend(result)  # type: ignore[arg-type]
    violations = _deduplicate_violations(_sort_violations(violations))

    # Add remediation classification to each violation
    registry = build_default_registry()
    add_classification_to_violations(violations, registry)

    if not validators:
        if args.json:
            print(json.dumps({"hierarchy_payload": context.hierarchy_payload}))
        else:
            print("Hierarchy payload built (use --json to dump). All validators skipped.")
        return

    if args.json:
        rem_counts = count_by_remediation_class(violations)
        res_counts = count_by_resolution(violations)
        unresolved = RemediationResolution.UNRESOLVED.value
        print(
            json.dumps(
                {
                    "violations": violations,
                    "count": len(violations),
                    "remediation_summary": {
                        "auto_fixable": rem_counts[RemediationClass.AUTO_FIXABLE.value],
                        "ai_candidate": rem_counts[RemediationClass.AI_CANDIDATE.value],
                        "manual_review": rem_counts[RemediationClass.MANUAL_REVIEW.value],
                    },
                    "resolution_summary": {k: v for k, v in res_counts.items() if k != unresolved},
                },
                indent=2,
            )
        )
        return

    payload: YAMLDict = context.hierarchy_payload or {}
    _render_scan_results(
        violations,
        scan_id=str(payload.get("scan_id", "")),
    )

    # Handle --auto-fix: apply Tier 1 fixes
    if getattr(args, "auto_fix", False):
        rem_counts = count_by_remediation_class(violations)
        fixable = rem_counts[RemediationClass.AUTO_FIXABLE.value]
        if fixable > 0:
            sys.stderr.write(f"\nApplying {fixable} auto-fixable remediation(s)...\n")
            _apply_remediation(
                Path(args.target).resolve(),
                apply=True,
                opa_bundle=getattr(args, "opa_bundle", None),
                ansible_version=getattr(args, "ansible_version", None),
                collection_specs=getattr(args, "collections", None),
                session_id=getattr(args, "session", None),
            )
        else:
            sys.stderr.write("\nNo auto-fixable issues found.\n")


def _run_scan_grpc(args: argparse.Namespace, primary_addr: str) -> None:
    """Send chunked fs to Primary daemon and print violations.

    Args:
        args: Parsed CLI namespace with target, json, verbose, ansible_version, collections.
        primary_addr: gRPC address of the Primary daemon.

    """
    verbosity = getattr(args, "verbose", 0) or 0

    try:
        chunks = yield_scan_chunks(
            args.target,
            project_root_name="project",
            ansible_core_version=getattr(args, "ansible_version", None),
            collection_specs=getattr(args, "collections", None),
        )
    except FileNotFoundError as e:
        sys.stderr.write(f"{e}\n")
        sys.exit(1)

    channel = grpc.insecure_channel(primary_addr)
    stub = primary_pb2_grpc.PrimaryStub(channel)  # type: ignore[no-untyped-call]
    try:
        resp = stub.ScanStream(chunks, timeout=120)
    except grpc.RpcError as e:
        sys.stderr.write(f"Primary daemon error: {e.details()}\n")
        sys.exit(1)
    finally:
        channel.close()

    violations: list[ViolationDict] = [violation_proto_to_dict(v) for v in resp.violations]
    violations = _deduplicate_violations(_sort_violations(violations))

    has_diag = resp.HasField("diagnostics")

    if args.json:
        rem_counts = count_by_remediation_class(violations)
        res_counts = count_by_resolution(violations)
        unresolved = RemediationResolution.UNRESOLVED.value
        out: dict[str, object] = {
            "violations": violations,
            "count": len(violations),
            "scan_id": resp.scan_id,
            "remediation_summary": {
                "auto_fixable": rem_counts[RemediationClass.AUTO_FIXABLE.value],
                "ai_candidate": rem_counts[RemediationClass.AI_CANDIDATE.value],
                "manual_review": rem_counts[RemediationClass.MANUAL_REVIEW.value],
            },
            "resolution_summary": {k: v for k, v in res_counts.items() if k != unresolved},
        }
        if verbosity and has_diag:
            out["diagnostics"] = _diag_to_dict(resp.diagnostics)
        print(json.dumps(out, indent=2))
        return

    # Render styled output
    scan_time_ms = resp.diagnostics.total_ms if has_diag else None
    _render_scan_results(
        violations,
        scan_id=resp.scan_id,
        scan_time_ms=scan_time_ms,
    )

    # Print diagnostics after the main output if requested
    if verbosity >= 2 and has_diag:
        _print_diagnostics_vv(resp.diagnostics)
    elif verbosity >= 1 and has_diag:
        _print_diagnostics_v(resp.diagnostics)


def _run_cache(args: argparse.Namespace) -> None:
    """Run a collection cache command (pull-galaxy, pull-requirements, clone-org).

    Args:
        args: Parsed CLI namespace with cache_command, cache_root, spec, requirements_path, org, etc.

    """
    cache_root = get_cache_root() if args.cache_root is None else Path(args.cache_root)

    if args.cache_command == "pull-galaxy":
        pull_galaxy_collection(
            args.spec,
            cache_root=cache_root,
            galaxy_server=getattr(args, "galaxy_server", None),
        )
        print(f"Installed {args.spec} into {cache_root}")
    elif args.cache_command == "pull-requirements":
        pull_galaxy_requirements(
            args.requirements_path,
            cache_root=cache_root,
            galaxy_server=getattr(args, "galaxy_server", None),
        )
        print(f"Installed requirements from {args.requirements_path} into {cache_root}")
    elif args.cache_command == "clone-org":
        if getattr(args, "repos", None):
            pull_github_repos(
                args.org,
                args.repos,
                cache_root=cache_root,
                clone_depth=getattr(args, "depth", 1),
            )
        else:
            pull_github_org(
                args.org,
                cache_root=cache_root,
                clone_depth=getattr(args, "depth", 1),
                token=getattr(args, "token", None),
            )
        print(f"Cloned org {args.org} into {cache_root}")
    else:
        sys.stderr.write(f"Unknown cache command: {args.cache_command}\n")
        sys.exit(1)


def _run_format(args: argparse.Namespace) -> None:
    """Format YAML files: normalize indentation, key order, jinja spacing, tabs.

    Args:
        args: Parsed CLI namespace with target, exclude, apply, check.

    """
    target = Path(args.target).resolve()
    exclude = getattr(args, "exclude", None) or []
    apply_changes = getattr(args, "apply", False)
    check_only = getattr(args, "check", False)

    if target.is_file():
        results = [format_file(target)]
    elif target.is_dir():
        results = format_directory(target, exclude_patterns=exclude)
    else:
        sys.stderr.write(f"Path not found: {target}\n")
        sys.exit(1)

    changed = [r for r in results if r.changed]

    if check_only:
        if changed:
            sys.stderr.write(f"{len(changed)} file(s) would be reformatted\n")
            for r in changed:
                sys.stderr.write(f"  {r.path}\n")
            sys.exit(1)
        else:
            sys.stderr.write("All files already formatted\n")
            sys.exit(0)

    if not changed:
        print("All files already formatted.")
        return

    if apply_changes:
        for r in changed:
            r.path.write_text(r.formatted, encoding="utf-8")
            print(f"  formatted: {r.path}")
        print(f"\n{len(changed)} file(s) reformatted.")
    else:
        for r in changed:
            sys.stdout.write(r.diff)
        sys.stderr.write(f"\n{len(changed)} file(s) would be reformatted (use --apply to write)\n")


def _scan_files_grpc(
    file_paths: list[str],
    primary_addr: str,
    ansible_version: str | None = None,
    collection_specs: list[str] | None = None,
) -> list[ViolationDict]:
    """Scan files via Primary gRPC daemon (fans out to all validators including OPA).

    Each file gets its own ScanStream call so only the requested files are
    scanned — avoids pulling in unrelated siblings from a shared parent
    directory, which would inflate violation counts in the remediation loop.

    Args:
        file_paths: Paths to YAML files to scan.
        primary_addr: gRPC address of the Primary daemon (e.g. 127.0.0.1:50051).
        ansible_version: ansible-core version for plugin introspection; None uses default.
        collection_specs: Optional collection specifiers for venv provisioning.

    Returns:
        Deduplicated, sorted list of violation dicts.

    """
    yaml_files = [f for f in file_paths if f.endswith((".yml", ".yaml"))]
    if not yaml_files:
        return []

    all_violations: list[ViolationDict] = []
    channel = grpc.insecure_channel(primary_addr)
    stub = primary_pb2_grpc.PrimaryStub(channel)  # type: ignore[no-untyped-call]
    try:
        for fpath in yaml_files:
            chunks = yield_scan_chunks(
                fpath,
                project_root_name="project",
                ansible_core_version=ansible_version,
                collection_specs=collection_specs,
            )
            try:
                resp = stub.ScanStream(chunks, timeout=120)
            except grpc.RpcError as e:
                sys.stderr.write(f"gRPC scan error for {fpath}: {e.details()}\n")
                continue
            all_violations.extend(violation_proto_to_dict(v) for v in resp.violations)
    finally:
        channel.close()

    return _deduplicate_violations(_sort_violations(all_violations))


def _scan_files_local(
    file_paths: list[str],
    repo_root: str,
    opa_bundle: str | None,
    ansible_version: str | None = None,
    collection_specs: list[str] | None = None,
    session_id: str | None = None,
    hierarchy_sink: list[Mapping[str, object]] | None = None,
) -> list[ViolationDict]:
    """In-process scan: engine + OPA + native + ansible validators. Returns violation dicts.

    Args:
        file_paths: Paths to YAML files to scan.
        repo_root: Project root path for engine context.
        opa_bundle: Optional path to OPA bundle; None uses built-in.
        ansible_version: ansible-core version for plugin introspection; None uses default.
        collection_specs: Optional collection specifiers for venv.
        session_id: Optional session ID for venv reuse across invocations.
        hierarchy_sink: If provided, each scan's hierarchy_payload is
            appended here so callers can build a NodeIndex without a
            redundant second scan pass.

    Returns:
        Deduplicated, sorted list of violation dicts.

    """
    from apme_engine.runner import run_scan as _run_scan

    yaml_files = [f for f in file_paths if f.endswith((".yml", ".yaml"))]
    if not yaml_files:
        return []

    venv_root = resolve_venv_root(
        ansible_version or ANSIBLE_DEFAULT_VERSION,
        collection_specs=collection_specs,
        session_id=session_id,
    )
    ansible_validator: AnsibleValidator | None = None
    if venv_root is not None:
        ansible_validator = AnsibleValidator(venv_root)

    all_violations: list[ViolationDict] = []
    for fpath in yaml_files:
        try:
            context = _run_scan(fpath, repo_root, include_scandata=True)
        except Exception:
            continue

        if not context.hierarchy_payload:
            continue

        if hierarchy_sink is not None:
            hierarchy_sink.append(context.hierarchy_payload)

        validators: list[tuple[str, OpaValidator | NativeValidator | AnsibleValidator]] = [
            ("OPA", OpaValidator(opa_bundle)),
            ("Native", NativeValidator()),
        ]
        if ansible_validator is not None:
            validators.append(("Ansible", ansible_validator))
        for _name, v in validators:
            all_violations.extend(v.run(context))  # type: ignore[arg-type]

    return _deduplicate_violations(_sort_violations(all_violations))


def _build_node_index_from_payloads(
    payloads: list[Mapping[str, object]],
) -> NodeIndex | None:
    """Build a NodeIndex from already-collected hierarchy payloads.

    This avoids a redundant second scan by reusing the payloads
    captured during ``_scan_files_local`` via ``hierarchy_sink``.

    Args:
        payloads: List of hierarchy_payload dicts from prior scans.

    Returns:
        NodeIndex if hierarchy data was found, else None.
    """
    merged: dict[str, list[object]] = {"hierarchy": []}
    for payload in payloads:
        hierarchy = payload.get("hierarchy")
        if isinstance(hierarchy, list):
            merged["hierarchy"].extend(hierarchy)
    if not merged["hierarchy"]:
        return None
    return NodeIndex(merged)


def _apply_remediation(
    target: Path,
    *,
    apply: bool = False,
    max_passes: int = 5,
    opa_bundle: str | None = None,
    ansible_version: str | None = None,
    collection_specs: list[str] | None = None,
    session_id: str | None = None,
    ai_provider: object | None = None,
    max_ai_attempts: int = 2,
    ci_mode: bool = False,
) -> None:
    """Scan files and run the remediation convergence loop.

    Args:
        target: Resolved path to file or directory.
        apply: If True, write fixes in place.
        max_passes: Maximum convergence passes.
        opa_bundle: Optional path to OPA bundle.
        ansible_version: ansible-core version for introspection.
        collection_specs: Optional collection specifiers for venv.
        session_id: Optional session ID for venv reuse.
        ai_provider: Optional AIProvider for Tier 2 escalation.
        max_ai_attempts: Max retry attempts for AI proposals per file batch.
        ci_mode: If True, apply AI proposals without interactive review.
    """
    if target.is_file():
        yaml_files = [str(target)]
    else:
        yaml_files = [
            str(p)
            for p in target.rglob("*")
            if p.suffix in (".yml", ".yaml") and not any(part.startswith(".") for part in p.parts)
        ]

    if not yaml_files:
        sys.stderr.write("  No YAML files found.\n")
        return

    primary_addr = os.environ.get("APME_PRIMARY_ADDRESS")

    if primary_addr:

        def scan_fn(paths: list[str]) -> list[ViolationDict]:
            return _scan_files_grpc(
                paths,
                primary_addr,
                ansible_version=ansible_version,
                collection_specs=collection_specs,
            )

        active_session_id = None
    else:
        repo_root = str(Path(__file__).resolve().parent.parent)

        session = resolve_session(
            ansible_version or ANSIBLE_DEFAULT_VERSION,
            collection_specs=collection_specs,
            session_id=session_id,
        )
        active_session_id = session.session_id if session else None

        hierarchy_payloads: list[Mapping[str, object]] = []
        _capture_hierarchy = [True]

        def scan_fn(paths: list[str]) -> list[ViolationDict]:
            sink = hierarchy_payloads if _capture_hierarchy[0] else None
            return _scan_files_local(
                paths,
                repo_root,
                opa_bundle,
                ansible_version=ansible_version,
                collection_specs=collection_specs,
                session_id=active_session_id,
                hierarchy_sink=sink,
            )

    node_index: NodeIndex | None = None

    registry = build_default_registry()

    if not primary_addr:
        initial_violations = scan_fn(yaml_files)
        _capture_hierarchy[0] = False
        node_index = _build_node_index_from_payloads(hierarchy_payloads)
        if node_index is not None:
            sys.stderr.write(f"  Indexed {len(node_index)} hierarchy nodes\n")
    else:
        initial_violations = None

    engine = RemediationEngine(
        registry=registry,
        scan_fn=scan_fn,
        max_passes=max_passes,
        max_ai_attempts=max_ai_attempts,
        verbose=True,
        node_index=node_index,
        ai_provider=ai_provider,  # type: ignore[arg-type]
    )

    sys.stderr.write(f"  {len(yaml_files)} YAML file(s), {len(registry)} transforms registered\n")
    sys.stderr.write(f"  Transforms: {', '.join(registry.rule_ids)}\n")
    if ai_provider is not None:
        sys.stderr.write("  AI escalation: enabled\n")
    sys.stderr.write("  Remediating...\n")

    report = engine.remediate(
        yaml_files,
        apply=apply,
        initial_violations=initial_violations,
    )

    if apply and report.applied_patches:
        patched_paths = {p.path for p in report.applied_patches}
        reformat = [format_file(Path(p)) for p in patched_paths]
        for r in reformat:
            if r.changed:
                r.path.write_text(r.formatted, encoding="utf-8")

    sys.stderr.write(f"  Tier 1 (deterministic):  {report.fixed} fixed\n")
    sys.stderr.write(f"  Tier 2 (AI-proposable):  {len(report.remaining_ai)} remaining\n")
    sys.stderr.write(f"  Tier 3 (manual review):  {len(report.remaining_manual)} remaining\n")
    sys.stderr.write(f"  Passes:                  {report.passes}\n")
    if report.ai_proposed:
        total_patches = sum(len(p.patches) for p in report.ai_proposed)
        total_skipped = sum(len(p.skipped) for p in report.ai_proposed)
        sys.stderr.write(f"  AI proposals:            {len(report.ai_proposed)} file(s), {total_patches} patch(es)\n")
        if total_skipped:
            sys.stderr.write(f"  AI skipped:              {total_skipped} violation(s) (see below for guidance)\n")
    if report.oscillation_detected:
        sys.stderr.write("  WARNING: oscillation detected, stopped early\n")

    # Interactive review of AI proposals
    if report.ai_proposed and not ci_mode:
        _review_ai_proposals(report.ai_proposed, apply=apply)
    elif report.ai_proposed and ci_mode:
        if apply:
            for proposal in report.ai_proposed:
                Path(proposal.file).write_text(proposal.fixed_yaml, encoding="utf-8")
            sys.stderr.write(f"  CI mode: applied {total_patches} AI patch(es) automatically\n")
        else:
            sys.stderr.write(f"  CI mode: {total_patches} AI patch(es) ready (use --apply to write)\n")

    # Print AI skipped violations with guidance
    _print_ai_skipped(report.ai_proposed)

    if active_session_id is not None:
        release_session(active_session_id)

    if not apply and report.applied_patches:
        sys.stderr.write(f"\n{len(report.applied_patches)} file(s) would be patched (use --apply to write):\n")
        for p in report.applied_patches:
            sys.stdout.write(p.diff)


def _review_ai_proposals(
    proposals: list[AIProposal],
    *,
    apply: bool = False,
) -> None:
    """Interactive per-hunk review loop (like git add -p).

    Iterates per file, then per patch within each file.
    Only accepted patches are applied.

    Args:
        proposals: List of AIProposal objects (one per file).
        apply: If True, accepted patches are written to disk.
    """
    total_patches = sum(len(p.patches) for p in proposals)
    accepted_total = 0
    skip_all = False

    for proposal in proposals:
        if skip_all:
            break

        sys.stderr.write(
            f"\n{'=' * 60}\n"
            f"File: {proposal.file}\n"
            f"Patches: {len(proposal.patches)} | "
            f"Min confidence: {proposal.confidence:.0%}\n"
            f"{'=' * 60}\n"
        )

        if proposal.hybrid_transforms_applied:
            sys.stderr.write(
                f"  ({proposal.hybrid_transforms_applied} deterministic cleanup(s) applied to AI output)\n"
            )

        accepted_patches: list[AIPatch] = []

        for pidx, patch in enumerate(proposal.patches, 1):
            sys.stderr.write(
                f"\n--- Patch {pidx}/{len(proposal.patches)} "
                f"[{patch.rule_id}] "
                f"lines {patch.line_start}-{patch.line_end} "
                f"({patch.confidence:.0%})\n"
                f"    {patch.explanation}\n"
            )

            if patch.diff_hunk:
                sys.stdout.write(patch.diff_hunk + "\n")
            else:
                sys.stderr.write("  (no diff available)\n")

            answer = _prompt_ynsa()
            if answer == "y":
                accepted_patches.append(patch)
                accepted_total += 1
            elif answer == "n":
                sys.stderr.write("  Skipped\n")
            elif answer == "a":
                accepted_patches.extend(proposal.patches[pidx - 1 :])
                accepted_total += len(proposal.patches) - pidx + 1
                sys.stderr.write(f"  Accepted remaining {len(proposal.patches) - pidx + 1} patch(es) for this file\n")
                break
            elif answer == "s":
                skip_all = True
                break
            elif answer == "q":
                sys.stderr.write("\nAborted.\n")
                sys.stderr.write(f"\n{accepted_total} of {total_patches} AI patch(es) accepted\n")
                return

        if accepted_patches and apply:
            patched = apply_patches(proposal.original_yaml, accepted_patches)
            Path(proposal.file).write_text(patched, encoding="utf-8")
            sys.stderr.write(f"  Applied {len(accepted_patches)} patch(es) to {proposal.file}\n")
        elif accepted_patches:
            sys.stderr.write(f"  Accepted {len(accepted_patches)} patch(es) (use --apply to write)\n")

    sys.stderr.write(f"\n{accepted_total} of {total_patches} AI patch(es) accepted\n")


def _prompt_ynsa() -> str:
    """Prompt user for y/n/a/s/q response.

    Returns:
        One of 'y', 'n', 'a', 's', 'q'.
    """
    while True:
        try:
            answer = (
                input("\nAccept? [y]es / [n]o / [a]ccept rest of file / [s]kip remaining / [q]uit: ").strip().lower()
            )
        except (EOFError, KeyboardInterrupt):
            return "q"

        if answer in ("y", "yes"):
            return "y"
        if answer in ("n", "no"):
            return "n"
        if answer in ("a", "accept"):
            return "a"
        if answer in ("s", "skip"):
            return "s"
        if answer in ("q", "quit"):
            return "q"
        sys.stderr.write("  Please enter y, n, a, s, or q\n")


def _print_ai_skipped(proposals: list[AIProposal]) -> None:
    """Print violations the AI could not fix with reasons and suggestions.

    Args:
        proposals: AI proposals (may contain skipped entries).
    """
    all_skipped = [(p.file, s) for p in proposals for s in p.skipped]
    if not all_skipped:
        return

    sys.stderr.write(f"\n{yellow('AI could not auto-fix')} ({len(all_skipped)} violation(s)):\n")

    current_file = ""
    for file_path, skip in all_skipped:
        if file_path != current_file:
            current_file = file_path
            sys.stderr.write(f"\n  {bold(Path(file_path).name)}\n")

        sys.stderr.write(f"    {dim(skip.rule_id)} line {skip.line}: {skip.reason}\n")
        if skip.suggestion:
            sys.stderr.write(f"      {cyan('suggestion:')} {skip.suggestion}\n")


def _resolve_ai_provider(args: argparse.Namespace) -> object | None:
    """Set up the AI provider if --ai is enabled.

    Runs preflight and exits on failure.

    Args:
        args: CLI namespace with ai, model, abbenay_addr, abbenay_token.

    Returns:
        AbbenayProvider instance if --ai, else None.
    """
    if not getattr(args, "ai", False):
        return None

    import asyncio  # noqa: PLC0415

    from apme_engine.remediation.abbenay_provider import (  # noqa: PLC0415
        AbbenayProvider,
        discover_abbenay,
    )

    addr = getattr(args, "abbenay_addr", None)
    if addr is None:
        addr = discover_abbenay()

    if addr is None:
        sys.stderr.write(
            "ERROR: --ai requires a running Abbenay daemon.\n"
            "Start with: abbenay daemon start\n"
            "Or pass --abbenay-addr explicitly.\n"
        )
        sys.exit(1)

    model = getattr(args, "model", None)
    if not model:
        sys.stderr.write("ERROR: --ai requires a model.\nPass --model 'provider/model' or set APME_AI_MODEL.\n")
        sys.exit(1)

    try:
        provider = AbbenayProvider(
            addr,
            token=getattr(args, "abbenay_token", None),
            model=model,
        )
    except ImportError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        sys.exit(1)

    sys.stderr.write(f"  Abbenay daemon: {addr}\n")

    healthy = asyncio.run(provider.preflight())
    if not healthy:
        sys.stderr.write("ERROR: Abbenay daemon health check failed.\nEnsure the daemon is running and accessible.\n")
        sys.exit(1)

    sys.stderr.write("  Abbenay preflight: OK\n")
    return provider


def _run_fix(args: argparse.Namespace) -> None:
    """Format -> idempotency check -> scan -> remediate (convergence loop).

    Args:
        args: Parsed CLI namespace with target, exclude, apply, check, max_passes, opa_bundle, ai flags.

    """
    target = Path(args.target).resolve()
    exclude = getattr(args, "exclude", None) or []
    apply_changes = getattr(args, "apply", False)
    check_only = getattr(args, "check", False)
    max_passes = getattr(args, "max_passes", 5)

    if not target.exists():
        sys.stderr.write(f"Path not found: {target}\n")
        sys.exit(1)

    # Phase 1: Format
    sys.stderr.write("Phase 1: Formatting...\n")
    results = [format_file(target)] if target.is_file() else format_directory(target, exclude_patterns=exclude)

    changed = [r for r in results if r.changed]

    if check_only:
        if changed:
            sys.stderr.write(f"  {len(changed)} file(s) would be reformatted\n")
            sys.exit(1)
        sys.stderr.write("  All files already formatted\n")
        sys.exit(0)

    if changed and apply_changes:
        for r in changed:
            r.path.write_text(r.formatted, encoding="utf-8")
        sys.stderr.write(f"  {len(changed)} file(s) reformatted\n")
    elif changed:
        for r in changed:
            sys.stdout.write(r.diff)
        sys.stderr.write(f"  {len(changed)} file(s) would be reformatted (use --apply to write)\n")
        return
    else:
        sys.stderr.write("  All files already formatted\n")

    # Phase 2: Idempotency gate
    sys.stderr.write("Phase 2: Idempotency check...\n")
    recheck = [format_file(target)] if target.is_file() else format_directory(target, exclude_patterns=exclude)

    still_changed = [r for r in recheck if r.changed]
    if still_changed:
        sys.stderr.write(f"  FAILED: {len(still_changed)} file(s) still have changes after formatting.\n")
        sys.stderr.write("  This indicates a formatter bug. Aborting.\n")
        for r in still_changed:
            sys.stderr.write(f"    {r.path}\n")
        sys.exit(1)
    sys.stderr.write("  Passed (zero diffs on second run)\n")

    # Phase 2.5: AI preflight (if --ai)
    ai_provider = _resolve_ai_provider(args)

    # Phase 3: Scan + Remediate
    sys.stderr.write("Phase 3: Scanning & remediating...\n")
    _apply_remediation(
        target,
        apply=apply_changes,
        max_passes=max_passes,
        opa_bundle=getattr(args, "opa_bundle", None),
        ansible_version=getattr(args, "ansible_version", None),
        collection_specs=getattr(args, "collections", None),
        session_id=getattr(args, "session", None),
        ai_provider=ai_provider,
        max_ai_attempts=getattr(args, "max_ai_attempts", 2),
        ci_mode=getattr(args, "ci", False),
    )


def _run_session(args: argparse.Namespace) -> None:
    """Manage named venv sessions (list, info, delete, reap).

    Args:
        args: Parsed CLI namespace with session_command and optional session_id/ttl.
    """
    from datetime import datetime, timezone

    from apme_engine.collection_cache.venv_session import VenvSessionManager

    cmd = args.session_command
    ttl = getattr(args, "ttl", 3600)
    mgr = VenvSessionManager(ttl_seconds=ttl)

    if cmd == "list":
        sessions = mgr.list_sessions()
        if not sessions:
            print("No active sessions.")
            return
        print(f"{'SESSION ID':<20} {'ANSIBLE':<10} {'COLLECTIONS':<30} {'AGE':<12} {'TYPE':<10}")
        print("-" * 82)
        now = time.time()
        for s in sessions:
            age_s = int(now - s.created_at)
            if age_s < 60:
                age = f"{age_s}s"
            elif age_s < 3600:
                age = f"{age_s // 60}m"
            else:
                age = f"{age_s // 3600}h {(age_s % 3600) // 60}m"
            colls = ", ".join(s.collection_specs) if s.collection_specs else "-"
            kind = "ephemeral" if s.ephemeral else "named"
            print(f"{s.session_id:<20} {s.ansible_version:<10} {colls:<30} {age:<12} {kind:<10}")

    elif cmd == "info":
        session = mgr.get(args.session_id)
        if session is None:
            sys.stderr.write(f"Session not found: {args.session_id}\n")
            sys.exit(1)
        created = datetime.fromtimestamp(session.created_at, tz=timezone.utc).isoformat()
        last_used = datetime.fromtimestamp(session.last_used_at, tz=timezone.utc).isoformat()
        print(f"Session ID:     {session.session_id}")
        print(f"Ansible:        {session.ansible_version}")
        print(f"Collections:    {', '.join(session.collection_specs) or '-'}")
        print(f"Venv root:      {session.venv_root}")
        print(f"Created:        {created}")
        print(f"Last used:      {last_used}")
        print(f"Type:           {'ephemeral' if session.ephemeral else 'named'}")

    elif cmd == "delete":
        if mgr.delete(args.session_id):
            print(f"Deleted session: {args.session_id}")
        else:
            sys.stderr.write(f"Session not found: {args.session_id}\n")
            sys.exit(1)

    elif cmd == "reap":
        count = mgr.reap_expired()
        print(f"Reaped {count} expired session(s).")


def _run_health_check(args: argparse.Namespace) -> None:
    """Check health of all services (Primary, Native, OPA, Ansible, Cache maintainer) via gRPC.

    Args:
        args: Parsed CLI namespace with primary_addr, native_addr, opa_addr, ansible_addr, cache_addr, timeout, json.

    """
    primary_addr = getattr(args, "primary_addr", None) or os.environ.get("APME_PRIMARY_ADDRESS")
    if not primary_addr:
        sys.stderr.write("Set --primary-addr or APME_PRIMARY_ADDRESS to check remote services.\n")
        sys.exit(1)

    results = run_health_checks(
        primary_addr=primary_addr,
        native_addr=getattr(args, "native_addr", None) or os.environ.get("NATIVE_GRPC_ADDRESS"),
        opa_addr=getattr(args, "opa_addr", None) or os.environ.get("OPA_GRPC_ADDRESS"),
        ansible_addr=getattr(args, "ansible_addr", None) or os.environ.get("ANSIBLE_GRPC_ADDRESS"),
        cache_addr=getattr(args, "cache_addr", None) or os.environ.get("APME_CACHE_GRPC_ADDRESS"),
        timeout=getattr(args, "timeout", 5.0),
    )

    if getattr(args, "json", False):
        out: dict[str, dict[str, str | float | bool | None]] = {
            name: {k: v for k, v in r.items() if v is not None} for name, r in results.items()
        }
        print(json.dumps(out, indent=2))
        sys.exit(0 if all(r["ok"] for r in results.values()) else 1)

    all_ok = True
    for name, r in results.items():
        ok = r["ok"]
        if not ok:
            all_ok = False
        status_str = "ok" if ok else "fail"
        latency = r.get("latency_ms")
        extra = f" ({latency}ms)" if latency is not None else ""
        if not ok and r.get("error"):
            extra = f" - {r['error']}"
        print(f"  {name}: {status_str}{extra}")
    print("overall:", "ok" if all_ok else "fail")
    sys.exit(0 if all_ok else 1)


def main() -> None:
    """Parse CLI, dispatch to scan/cache/format/fix/health-check, and exit."""
    parser = argparse.ArgumentParser(
        description="Run APME scan: engine + OPA and native validators; or manage collection cache.",
    )
    global_opts = argparse.ArgumentParser(add_help=False)
    global_opts.add_argument(
        "--na",
        "--no-ansi",
        action="store_true",
        default=False,
        dest="no_ansi",
        help="Disable ANSI color output",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", parents=[global_opts], help="Run scan on a playbook, role, or project")
    scan_parser.add_argument("target", nargs="?", default=".", help="Path to playbook, role, or project")
    scan_parser.add_argument(
        "--primary-addr",
        default=None,
        help=(
            "Primary daemon gRPC address (e.g. localhost:50051). If set, scan runs on daemon; "
            "else in-process. Env: APME_PRIMARY_ADDRESS"
        ),
    )
    scan_parser.add_argument(
        "--opa-bundle",
        default=None,
        help="Path to OPA bundle directory (default: use built-in validator bundle)",
    )
    scan_parser.add_argument("--json", action="store_true", help="Output violations as JSON")
    scan_parser.add_argument("--no-opa", action="store_true", help="Skip OPA validator")
    scan_parser.add_argument("--no-native", action="store_true", help="Skip native (Python) validator")
    scan_parser.add_argument("--no-ansible", action="store_true", help="Skip Ansible validator (plugin introspection)")
    scan_parser.add_argument(
        "--ansible-version",
        default=None,
        help="ansible-core version to use for validation (e.g. 2.18, 2.20). Default: 2.20",
    )
    scan_parser.add_argument(
        "--collections",
        nargs="*",
        default=None,
        help="Collection specs to make available (e.g. community.general:9.0.0 amazon.aws)",
    )
    scan_parser.add_argument(
        "--session",
        default=None,
        help="Named session ID for venv reuse across invocations (omit for ephemeral)",
    )
    scan_parser.add_argument(
        "--session-ttl",
        type=int,
        default=3600,
        help="TTL in seconds for named sessions before auto-expiry (default: 3600)",
    )
    scan_parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Diagnostics verbosity: -v for summary + top 10, -vv for full per-rule breakdown",
    )
    scan_parser.add_argument(
        "--auto-fix",
        action="store_true",
        help="Apply auto-fixable (Tier 1) remediations after scan",
    )

    cache_parser = subparsers.add_parser(
        "cache", parents=[global_opts], help="Manage collection cache (Galaxy + GitHub)"
    )
    cache_parser.add_argument(
        "--cache-root",
        default=None,
        help="Collection cache root (default: APME_COLLECTION_CACHE or ~/.apme-data/collection-cache)",
    )
    cache_sub = cache_parser.add_subparsers(dest="cache_command", required=True)

    pull_galaxy = cache_sub.add_parser(
        "pull-galaxy", help="Install a Galaxy collection (e.g. namespace.collection or ns.coll:1.2.3)"
    )
    pull_galaxy.add_argument("spec", help="Collection spec: namespace.collection or namespace.collection:version")
    pull_galaxy.add_argument("--galaxy-server", default=None, help="Galaxy server URL")

    pull_req = cache_sub.add_parser("pull-requirements", help="Install collections from a requirements.yml")
    pull_req.add_argument("requirements_path", help="Path to requirements.yml")
    pull_req.add_argument("--galaxy-server", default=None, help="Galaxy server URL")

    clone_org = cache_sub.add_parser("clone-org", help="Clone GitHub org repos that are Ansible collections")
    clone_org.add_argument("org", help="GitHub organization name")
    clone_org.add_argument(
        "--repos", nargs="*", default=None, help="Optional list of repo names to clone (default: all from org)"
    )
    clone_org.add_argument("--depth", type=int, default=1, help="Git clone depth (default: 1)")
    clone_org.add_argument("--token", default=None, help="GitHub token for API (for listing org repos)")

    # ── format ──
    format_parser = subparsers.add_parser(
        "format",
        parents=[global_opts],
        help="Normalize YAML formatting (indentation, key order, jinja spacing, tabs)",
    )
    format_parser.add_argument("target", nargs="?", default=".", help="Path to file or directory")
    format_parser.add_argument("--apply", action="store_true", help="Write formatted files in place")
    format_parser.add_argument("--check", action="store_true", help="Exit 1 if files would change (CI mode)")
    format_parser.add_argument("--exclude", nargs="*", default=None, help="Glob patterns to skip")

    # ── fix ──
    fix_parser = subparsers.add_parser(
        "fix",
        parents=[global_opts],
        help="Format then modernize: format → idempotency check → re-scan → modernize",
    )
    fix_parser.add_argument("target", nargs="?", default=".", help="Path to file or directory")
    fix_parser.add_argument("--apply", action="store_true", help="Write changes in place")
    fix_parser.add_argument("--check", action="store_true", help="Exit 1 if changes would be made (CI mode)")
    fix_parser.add_argument("--exclude", nargs="*", default=None, help="Glob patterns to skip")
    fix_parser.add_argument("--max-passes", type=int, default=5, help="Max convergence passes (default: 5)")
    fix_parser.add_argument(
        "--ai",
        action="store_true",
        help="Enable AI escalation for Tier 2 violations (requires Abbenay daemon)",
    )
    fix_parser.add_argument(
        "--model",
        default=os.environ.get("APME_AI_MODEL"),
        help="AI model identifier (e.g. 'openrouter/anthropic/claude-opus-4.6'). Falls back to APME_AI_MODEL env var.",
    )
    fix_parser.add_argument(
        "--abbenay-addr",
        default=None,
        help="Abbenay daemon address (default: auto-discover from runtime socket)",
    )
    fix_parser.add_argument(
        "--abbenay-token",
        default=os.environ.get("APME_ABBENAY_TOKEN"),
        help="Consumer auth token for Abbenay inline policy. Falls back to APME_ABBENAY_TOKEN env var.",
    )
    fix_parser.add_argument(
        "--max-ai-attempts",
        type=int,
        default=2,
        help="Max retry attempts for AI proposals per file batch (default: 2)",
    )
    fix_parser.add_argument(
        "--ci",
        action="store_true",
        help="CI mode: apply AI proposals without interactive review",
    )
    fix_parser.add_argument("--opa-bundle", default=None, help="Path to OPA bundle directory")
    fix_parser.add_argument(
        "--ansible-version",
        default=None,
        help="ansible-core version for plugin introspection (e.g. 2.18, 2.20). Default: 2.20",
    )
    fix_parser.add_argument(
        "--collections",
        nargs="*",
        default=None,
        help="Collection specs to make available (e.g. community.general:9.0.0 amazon.aws)",
    )
    fix_parser.add_argument(
        "--session",
        default=None,
        help="Named session ID for venv reuse across invocations (omit for ephemeral)",
    )
    fix_parser.add_argument(
        "--session-ttl",
        type=int,
        default=3600,
        help="TTL in seconds for named sessions before auto-expiry (default: 3600)",
    )

    # ── session ──
    session_parser = subparsers.add_parser(
        "session",
        parents=[global_opts],
        help="Manage named venv sessions (list, info, delete, reap)",
    )
    session_sub = session_parser.add_subparsers(dest="session_command", required=True)

    session_sub.add_parser("list", help="List all active sessions")
    session_info = session_sub.add_parser("info", help="Show details for a session")
    session_info.add_argument("session_id", help="Session ID to inspect")
    session_del = session_sub.add_parser("delete", help="Delete a session and its venv")
    session_del.add_argument("session_id", help="Session ID to delete")
    session_reap = session_sub.add_parser("reap", help="Delete all expired sessions")
    session_reap.add_argument(
        "--ttl",
        type=int,
        default=3600,
        help="TTL in seconds; sessions unused longer than this are reaped (default: 3600)",
    )

    # ── health-check ──
    health_parser = subparsers.add_parser(
        "health-check",
        parents=[global_opts],
        help="Check health of all services (Primary, Native, OPA, Ansible, Cache maintainer) via gRPC",
    )
    health_parser.add_argument(
        "--primary-addr",
        default=None,
        help="Primary daemon gRPC address (e.g. localhost:50051). Env: APME_PRIMARY_ADDRESS",
    )
    health_parser.add_argument(
        "--native-addr", default=None, help="Native validator gRPC address (default: derived or NATIVE_GRPC_ADDRESS)"
    )
    health_parser.add_argument(
        "--opa-addr", default=None, help="OPA validator gRPC address (default: derived or OPA_GRPC_ADDRESS)"
    )
    health_parser.add_argument(
        "--ansible-addr", default=None, help="Ansible validator gRPC address (default: derived or ANSIBLE_GRPC_ADDRESS)"
    )
    health_parser.add_argument(
        "--cache-addr", default=None, help="Cache maintainer gRPC address (default: derived or APME_CACHE_GRPC_ADDRESS)"
    )
    health_parser.add_argument("--timeout", type=float, default=5.0, help="Timeout per check in seconds (default: 5)")
    health_parser.add_argument("--json", action="store_true", help="Output results as JSON")

    args = parser.parse_args()

    if args.no_ansi:
        from apme_engine.ansi import force_no_color  # noqa: PLC0415

        force_no_color()

    # Set session TTL if provided
    ttl = getattr(args, "session_ttl", 3600)
    if ttl != 3600:
        from apme_engine.validators.ansible._venv import get_session_manager  # noqa: PLC0415

        get_session_manager(ttl_seconds=ttl)

    if args.command == "scan":
        _run_scan(args)
    elif args.command == "format":
        _run_format(args)
    elif args.command == "fix":
        _run_fix(args)
    elif args.command == "session":
        _run_session(args)
    elif args.command == "health-check":
        _run_health_check(args)
    else:
        _run_cache(args)


if __name__ == "__main__":
    main()
