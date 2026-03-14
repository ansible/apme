"""CLI: run engine + validators and print violations; collection cache commands; YAML formatting."""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import cast

import grpc

from apme.v1 import primary_pb2_grpc
from apme.v1.primary_pb2 import ScanDiagnostics
from apme_engine.collection_cache import (
    get_cache_root,
    pull_galaxy_collection,
    pull_galaxy_requirements,
    pull_github_org,
    pull_github_repos,
)
from apme_engine.daemon.chunked_fs import build_scan_request
from apme_engine.daemon.health_check import run_health_checks
from apme_engine.daemon.violation_convert import violation_proto_to_dict
from apme_engine.engine.models import ViolationDict, YAMLDict
from apme_engine.formatter import format_directory, format_file
from apme_engine.remediation.engine import RemediationEngine
from apme_engine.remediation.transforms import build_default_registry
from apme_engine.runner import run_scan
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
        if isinstance(line, (int, float)):
            resolved = line
        elif isinstance(line, (list, tuple)) and line:
            first = line[0]
            resolved = first if isinstance(first, (int, float)) else 0
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
        if isinstance(line, (list, tuple)):
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

    validators: list[tuple[str, OpaValidator | NativeValidator]] = []
    if not args.no_opa:
        validators.append(("OPA", OpaValidator(args.opa_bundle)))
    if not args.no_native:
        validators.append(("Native", NativeValidator()))

    violations: list[ViolationDict] = []
    for name, v in validators:
        result = v.run(context)
        sys.stderr.write(f"{name}: {len(result)} violation(s)\n")
        violations.extend(result)  # type: ignore[arg-type]
    violations = _deduplicate_violations(_sort_violations(violations))

    if not validators:
        if args.json:
            print(json.dumps({"hierarchy_payload": context.hierarchy_payload}))
        else:
            print("Hierarchy payload built (use --json to dump). All validators skipped.")
        return

    if args.json:
        print(json.dumps({"violations": violations, "count": len(violations)}, indent=2))
        return

    payload: YAMLDict = context.hierarchy_payload or {}
    print(f"Scan: {payload.get('scan_id', '')} | Violations: {len(violations)}")
    for violation in violations:
        line = violation.get("line")
        line_str = str(line) if line is not None else "?"
        rule_id = violation.get("rule_id", "")
        fpath = violation.get("file", "")
        msg = violation.get("message", "")
        print(f"  [{rule_id}] {fpath}:{line_str} - {msg}")
    if not violations:
        print("No violations.")


def _run_scan_grpc(args: argparse.Namespace, primary_addr: str) -> None:
    """Send chunked fs to Primary daemon and print violations.

    Args:
        args: Parsed CLI namespace with target, json, verbose, ansible_version, collections.
        primary_addr: gRPC address of the Primary daemon.

    """
    verbosity = getattr(args, "verbose", 0) or 0

    try:
        req = build_scan_request(
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
        resp = stub.Scan(req, timeout=120)
    except grpc.RpcError as e:
        sys.stderr.write(f"Primary daemon error: {e.details()}\n")
        sys.exit(1)
    finally:
        channel.close()

    violations: list[ViolationDict] = [violation_proto_to_dict(v) for v in resp.violations]
    violations = _deduplicate_violations(_sort_violations(violations))

    has_diag = resp.HasField("diagnostics")

    if args.json:
        out = {"violations": violations, "count": len(violations), "scan_id": resp.scan_id}
        if verbosity and has_diag:
            out["diagnostics"] = _diag_to_dict(resp.diagnostics)
        print(json.dumps(out, indent=2))
        return

    print(f"Scan: {resp.scan_id} | Violations: {len(violations)}")

    if verbosity >= 2 and has_diag:
        _print_diagnostics_vv(resp.diagnostics)
    elif verbosity >= 1 and has_diag:
        _print_diagnostics_v(resp.diagnostics)

    for v in violations:
        line = v.get("line")
        line_str = str(line) if line is not None else "?"
        print(f"  [{v.get('rule_id', '')}] {v.get('file', '')}:{line_str} - {v.get('message', '')}")
    if not violations:
        print("No violations.")


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


def _scan_files_local(file_paths: list[str], repo_root: str, opa_bundle: str | None) -> list[ViolationDict]:
    """In-process scan: engine + OPA + native validators. Returns violation dicts.

    Args:
        file_paths: Paths to YAML files to scan.
        repo_root: Project root path for engine context.
        opa_bundle: Optional path to OPA bundle; None uses built-in.

    Returns:
        Deduplicated, sorted list of violation dicts.

    """
    from apme_engine.runner import run_scan as _run_scan

    yaml_files = [f for f in file_paths if f.endswith((".yml", ".yaml"))]
    if not yaml_files:
        return []

    all_violations: list[ViolationDict] = []
    for fpath in yaml_files:
        try:
            context = _run_scan(fpath, repo_root, include_scandata=True)
        except Exception:
            continue

        if not context.hierarchy_payload:
            continue

        validators: list[tuple[str, OpaValidator | NativeValidator]] = [
            ("OPA", OpaValidator(opa_bundle)),
            ("Native", NativeValidator()),
        ]
        for _name, v in validators:
            all_violations.extend(v.run(context))  # type: ignore[arg-type]

    return _deduplicate_violations(_sort_violations(all_violations))


def _run_fix(args: argparse.Namespace) -> None:
    """Format → idempotency check → scan → remediate → ansible-lint (convergence loop).

    Args:
        args: Parsed CLI namespace with target, exclude, apply, check, max_passes, opa_bundle,
            lint_profile, lint_config, no_lint.

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

    # Phase 3: Scan + Remediate
    sys.stderr.write("Phase 3: Scanning...\n")

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

    repo_root = str(Path(__file__).resolve().parent.parent)
    opa_bundle = getattr(args, "opa_bundle", None)

    def scan_fn(paths: list[str]) -> list[ViolationDict]:
        return _scan_files_local(paths, repo_root, opa_bundle)

    registry = build_default_registry()
    engine = RemediationEngine(
        registry=registry,
        scan_fn=scan_fn,
        max_passes=max_passes,
        verbose=True,
    )

    sys.stderr.write(f"  {len(yaml_files)} YAML file(s), {len(registry)} transforms registered\n")
    sys.stderr.write(f"  Transforms: {', '.join(registry.rule_ids)}\n")

    sys.stderr.write("Phase 4: Remediating...\n")
    report = engine.remediate(yaml_files, apply=apply_changes)

    # Phase 5: ansible-lint
    lint_result = None
    if not getattr(args, "no_lint", False):
        from apme_engine.lint_runner import run_ansible_lint

        sys.stderr.write("Phase 5: ansible-lint...\n")
        lint_profile = getattr(args, "lint_profile", "production")
        lint_config = getattr(args, "lint_config", None)
        try:
            lint_result = run_ansible_lint(
                target,
                fix=apply_changes,
                profile=lint_profile,
                config_file=lint_config,
            )
            if lint_result.stdout:
                sys.stdout.write(lint_result.stdout)
            if lint_result.stderr:
                sys.stderr.write(lint_result.stderr)

            if lint_result.returncode == 0:
                sys.stderr.write("  ansible-lint: clean\n")
            elif apply_changes:
                sys.stderr.write(f"  ansible-lint: applied fixes (exit code {lint_result.returncode})\n")
            else:
                sys.stderr.write(f"  ansible-lint: {lint_result.returncode} (violations found)\n")
        except FileNotFoundError as e:
            sys.stderr.write(f"  WARNING: {e}\n")
    else:
        sys.stderr.write("Phase 5: ansible-lint (skipped)\n")

    # Phase 6: Report
    sys.stderr.write("Phase 6: Summary\n")
    sys.stderr.write(f"  Tier 1 (deterministic):  {report.fixed} fixed\n")
    sys.stderr.write(f"  Tier 2 (AI-proposable):  {len(report.remaining_ai)} remaining\n")
    sys.stderr.write(f"  Tier 3 (manual review):  {len(report.remaining_manual)} remaining\n")
    sys.stderr.write(f"  Passes:                  {report.passes}\n")
    if report.oscillation_detected:
        sys.stderr.write("  WARNING: oscillation detected, stopped early\n")
    if lint_result is not None:
        status = "clean" if lint_result.returncode == 0 else f"exit {lint_result.returncode}"
        mode = "fix" if lint_result.fixed else "report"
        sys.stderr.write(f"  ansible-lint ({mode}):    {status}\n")

    if not apply_changes and report.applied_patches:
        sys.stderr.write(f"\n{len(report.applied_patches)} file(s) would be patched (use --apply to write):\n")
        for p in report.applied_patches:
            sys.stdout.write(p.diff)


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
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Run scan on a playbook, role, or project")
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
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Diagnostics verbosity: -v for summary + top 10, -vv for full per-rule breakdown",
    )

    cache_parser = subparsers.add_parser("cache", help="Manage collection cache (Galaxy + GitHub)")
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
        help="Normalize YAML formatting (indentation, key order, jinja spacing, tabs)",
    )
    format_parser.add_argument("target", nargs="?", default=".", help="Path to file or directory")
    format_parser.add_argument("--apply", action="store_true", help="Write formatted files in place")
    format_parser.add_argument("--check", action="store_true", help="Exit 1 if files would change (CI mode)")
    format_parser.add_argument("--exclude", nargs="*", default=None, help="Glob patterns to skip")

    # ── fix ──
    fix_parser = subparsers.add_parser(
        "fix",
        help="Format then modernize: format → idempotency check → re-scan → modernize",
    )
    fix_parser.add_argument("target", nargs="?", default=".", help="Path to file or directory")
    fix_parser.add_argument("--apply", action="store_true", help="Write changes in place")
    fix_parser.add_argument("--check", action="store_true", help="Exit 1 if changes would be made (CI mode)")
    fix_parser.add_argument("--exclude", nargs="*", default=None, help="Glob patterns to skip")
    fix_parser.add_argument("--max-passes", type=int, default=5, help="Max convergence passes (default: 5)")
    fix_parser.add_argument("--no-ai", action="store_true", help="Skip AI escalation (deterministic fixes only)")
    fix_parser.add_argument("--opa-bundle", default=None, help="Path to OPA bundle directory")
    fix_parser.add_argument("--lint-profile", default="production", help="ansible-lint profile (default: production)")
    fix_parser.add_argument("--lint-config", default=None, help="Path to ansible-lint config file (passed as -c)")
    fix_parser.add_argument("--no-lint", action="store_true", help="Skip the ansible-lint phase")

    # ── health-check ──
    health_parser = subparsers.add_parser(
        "health-check", help="Check health of all services (Primary, Native, OPA, Ansible, Cache maintainer) via gRPC"
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

    if args.command == "scan":
        _run_scan(args)
    elif args.command == "format":
        _run_format(args)
    elif args.command == "fix":
        _run_fix(args)
    elif args.command == "health-check":
        _run_health_check(args)
    else:
        _run_cache(args)


if __name__ == "__main__":
    main()
