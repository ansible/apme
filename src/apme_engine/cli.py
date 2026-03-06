"""CLI: run engine + validators and print violations; collection cache commands; YAML formatting."""

import argparse
import json
import os
import sys
from pathlib import Path

import grpc
from apme.v1 import primary_pb2_grpc
from apme_engine.daemon.chunked_fs import build_scan_request
from apme_engine.daemon.violation_convert import violation_proto_to_dict
from apme_engine.runner import run_scan
from apme_engine.validators.opa import OpaValidator
from apme_engine.validators.native import NativeValidator
from apme_engine.collection_cache import (
    get_cache_root,
    pull_galaxy_collection,
    pull_galaxy_requirements,
    pull_github_org,
    pull_github_repos,
)
from apme_engine.daemon.health_check import run_health_checks
from apme_engine.formatter import format_file, format_directory, check_idempotent
from apme_engine.remediation.engine import RemediationEngine
from apme_engine.remediation.transforms import build_default_registry


def _sort_violations(violations: list[dict]) -> list[dict]:
    """Sort by file, then line for stable output."""
    def key(v):
        f = v.get("file") or ""
        line = v.get("line")
        if isinstance(line, (list, tuple)) and line:
            line = line[0] if line else 0
        return (f, line if isinstance(line, int) else (line or 0))
    return sorted(violations, key=key)


def _deduplicate_violations(violations: list[dict]) -> list[dict]:
    """Remove duplicate violations sharing the same (rule_id, file, line)."""
    seen: set[tuple] = set()
    out: list[dict] = []
    for v in violations:
        line = v.get("line")
        if isinstance(line, (list, tuple)):
            line = tuple(line)
        key = (v.get("rule_id", ""), v.get("file", ""), line)
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out


def _run_scan(args):
    """Run scan: engine + validators on target path, or call Primary daemon over gRPC."""
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

    validators = []
    if not args.no_opa:
        validators.append(("OPA", OpaValidator(args.opa_bundle)))
    if not args.no_native:
        validators.append(("Native", NativeValidator()))

    violations = []
    for name, v in validators:
        result = v.run(context)
        sys.stderr.write(f"{name}: {len(result)} violation(s)\n")
        violations.extend(result)
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

    payload = context.hierarchy_payload
    print(f"Scan: {payload.get('scan_id', '')} | Violations: {len(violations)}")
    for v in violations:
        line = v.get("line")
        line_str = str(line) if line is not None else "?"
        print(f"  [{v.get('rule_id', '')}] {v.get('file', '')}:{line_str} - {v.get('message', '')}")
    if not violations:
        print("No violations.")


def _run_scan_grpc(args, primary_addr: str):
    """Send chunked fs to Primary daemon and print violations."""
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
    stub = primary_pb2_grpc.PrimaryStub(channel)
    try:
        resp = stub.Scan(req, timeout=120)
    except grpc.RpcError as e:
        sys.stderr.write(f"Primary daemon error: {e.details()}\n")
        sys.exit(1)
    finally:
        channel.close()

    violations = [violation_proto_to_dict(v) for v in resp.violations]
    violations = _deduplicate_violations(_sort_violations(violations))

    if args.json:
        print(json.dumps({"violations": violations, "count": len(violations)}, indent=2))
        return

    print(f"Scan: {resp.scan_id} | Violations: {len(violations)}")
    for v in violations:
        line = v.get("line")
        line_str = str(line) if line is not None else "?"
        print(f"  [{v.get('rule_id', '')}] {v.get('file', '')}:{line_str} - {v.get('message', '')}")
    if not violations:
        print("No violations.")


def _run_cache(args):
    """Run a collection cache command (pull-galaxy, pull-requirements, clone-org)."""
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


def _run_format(args):
    """Format YAML files: normalize indentation, key order, jinja spacing, tabs."""
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


def _scan_files_local(file_paths: list[str], repo_root: str, opa_bundle: str | None) -> list[dict]:
    """In-process scan: engine + OPA + native validators. Returns violation dicts."""
    from apme_engine.runner import run_scan as _run_scan

    yaml_files = [f for f in file_paths if f.endswith((".yml", ".yaml"))]
    if not yaml_files:
        return []

    all_violations: list[dict] = []
    for fpath in yaml_files:
        try:
            context = _run_scan(fpath, repo_root, include_scandata=True)
        except Exception:
            continue

        if not context.hierarchy_payload:
            continue

        validators = [
            ("OPA", OpaValidator(opa_bundle)),
            ("Native", NativeValidator()),
        ]
        for _name, v in validators:
            all_violations.extend(v.run(context))

    return _deduplicate_violations(_sort_violations(all_violations))


def _run_fix(args):
    """Format → idempotency check → scan → remediate (convergence loop)."""
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
    if target.is_file():
        results = [format_file(target)]
    else:
        results = format_directory(target, exclude_patterns=exclude)

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
    if target.is_file():
        recheck = [format_file(target)]
    else:
        recheck = format_directory(target, exclude_patterns=exclude)

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
            str(p) for p in target.rglob("*")
            if p.suffix in (".yml", ".yaml") and not any(part.startswith(".") for part in p.parts)
        ]

    if not yaml_files:
        sys.stderr.write("  No YAML files found.\n")
        return

    repo_root = str(Path(__file__).resolve().parent.parent)
    opa_bundle = getattr(args, "opa_bundle", None)

    def scan_fn(paths: list[str]) -> list[dict]:
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

    # Phase 5: Report
    sys.stderr.write("Phase 5: Summary\n")
    sys.stderr.write(f"  Tier 1 (deterministic):  {report.fixed} fixed\n")
    sys.stderr.write(f"  Tier 2 (AI-proposable):  {len(report.remaining_ai)} remaining\n")
    sys.stderr.write(f"  Tier 3 (manual review):  {len(report.remaining_manual)} remaining\n")
    sys.stderr.write(f"  Passes:                  {report.passes}\n")
    if report.oscillation_detected:
        sys.stderr.write("  WARNING: oscillation detected, stopped early\n")

    if not apply_changes and report.applied_patches:
        sys.stderr.write(f"\n{len(report.applied_patches)} file(s) would be patched (use --apply to write):\n")
        for p in report.applied_patches:
            sys.stdout.write(p.diff)


def _run_health_check(args):
    """Check health of all services (Primary, Native, OPA, Ansible, Cache maintainer) via gRPC."""
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
        out = {name: {k: v for k, v in r.items() if v is not None} for name, r in results.items()}
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


def main():
    parser = argparse.ArgumentParser(
        description="Run APME scan: engine + OPA and native validators; or manage collection cache.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Run scan on a playbook, role, or project")
    scan_parser.add_argument("target", nargs="?", default=".", help="Path to playbook, role, or project")
    scan_parser.add_argument(
        "--primary-addr",
        default=None,
        help="Primary daemon gRPC address (e.g. localhost:50051). If set, scan runs on daemon; else in-process. Env: APME_PRIMARY_ADDRESS",
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

    cache_parser = subparsers.add_parser("cache", help="Manage collection cache (Galaxy + GitHub)")
    cache_parser.add_argument(
        "--cache-root",
        default=None,
        help="Collection cache root (default: APME_COLLECTION_CACHE or ~/.apme-data/collection-cache)",
    )
    cache_sub = cache_parser.add_subparsers(dest="cache_command", required=True)

    pull_galaxy = cache_sub.add_parser("pull-galaxy", help="Install a Galaxy collection (e.g. namespace.collection or ns.coll:1.2.3)")
    pull_galaxy.add_argument("spec", help="Collection spec: namespace.collection or namespace.collection:version")
    pull_galaxy.add_argument("--galaxy-server", default=None, help="Galaxy server URL")

    pull_req = cache_sub.add_parser("pull-requirements", help="Install collections from a requirements.yml")
    pull_req.add_argument("requirements_path", help="Path to requirements.yml")
    pull_req.add_argument("--galaxy-server", default=None, help="Galaxy server URL")

    clone_org = cache_sub.add_parser("clone-org", help="Clone GitHub org repos that are Ansible collections")
    clone_org.add_argument("org", help="GitHub organization name")
    clone_org.add_argument("--repos", nargs="*", default=None, help="Optional list of repo names to clone (default: all from org)")
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

    # ── health-check ──
    health_parser = subparsers.add_parser("health-check", help="Check health of all services (Primary, Native, OPA, Ansible, Cache maintainer) via gRPC")
    health_parser.add_argument(
        "--primary-addr",
        default=None,
        help="Primary daemon gRPC address (e.g. localhost:50051). Env: APME_PRIMARY_ADDRESS",
    )
    health_parser.add_argument("--native-addr", default=None, help="Native validator gRPC address (default: derived or NATIVE_GRPC_ADDRESS)")
    health_parser.add_argument("--opa-addr", default=None, help="OPA validator gRPC address (default: derived or OPA_GRPC_ADDRESS)")
    health_parser.add_argument("--ansible-addr", default=None, help="Ansible validator gRPC address (default: derived or ANSIBLE_GRPC_ADDRESS)")
    health_parser.add_argument("--cache-addr", default=None, help="Cache maintainer gRPC address (default: derived or APME_CACHE_GRPC_ADDRESS)")
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
