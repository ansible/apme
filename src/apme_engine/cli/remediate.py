"""Remediate subcommand: full remediation pipeline with Tier 1 auto-fix and optional AI proposals (ADR-028, ADR-039).

Creates a fix session, streams progress events, handles interactive proposal
review (or --auto-approve), and writes patched files on completion.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import grpc

from apme.v1 import common_pb2, primary_pb2_grpc
from apme.v1.primary_pb2 import (
    AiEscalateRequest,
    AiEscalateTarget,
    ApprovalRequest,
    CloseRequest,
    ExtendRequest,
    FixOptions,
    FixReport,
    Proposal,
    ScanChunk,
    SessionCommand,
)
from apme_engine.cli._exit_codes import EXIT_ERROR, EXIT_VIOLATIONS
from apme_engine.cli._galaxy_config import discover_galaxy_servers
from apme_engine.cli._project_root import derive_session_id, discover_project_root
from apme_engine.cli._rules_yml import load_rule_configs_from_project
from apme_engine.cli._suppressions import apply_suppressions, load_suppressions
from apme_engine.cli.ansi import dim, red, yellow
from apme_engine.cli.discovery import resolve_primary
from apme_engine.daemon.chunked_fs import yield_scan_chunks
from apme_engine.daemon.violation_convert import violation_proto_to_dict
from apme_engine.engine.models import ViolationDict


def run_remediate(args: argparse.Namespace) -> None:
    """Execute the remediate subcommand.

    Args:
        args: Parsed CLI arguments.
    """
    from apme_engine.cli.check import _apply_dep_scan_flags

    skip_collection, skip_python = _apply_dep_scan_flags(args)
    target = Path(args.target).resolve()
    if not target.exists():
        sys.stderr.write(f"Target not found: {args.target}\n")
        sys.exit(EXIT_ERROR)

    explicit_session = getattr(args, "session", None)
    project_root = discover_project_root(target)
    session_id = explicit_session or derive_session_id(project_root)

    galaxy_servers = discover_galaxy_servers(project_root) or None
    rule_cfgs = load_rule_configs_from_project(project_root)

    try:
        base_chunks = yield_scan_chunks(
            str(target),
            project_root_name="project",
            ansible_core_version=getattr(args, "ansible_version", None),
            collection_specs=getattr(args, "collections", None),
            session_id=session_id,
            galaxy_servers=galaxy_servers,
            rule_configs=rule_cfgs or None,
            skip_collection_health=skip_collection,
            skip_dep_audit=skip_python,
        )
    except FileNotFoundError as e:
        sys.stderr.write(f"{e}\n")
        sys.exit(EXIT_ERROR)

    fix_opts = FixOptions(
        max_passes=getattr(args, "max_passes", 5),
        ansible_core_version=getattr(args, "ansible_version", None) or "",
        collection_specs=getattr(args, "collections", None) or [],
        enable_ai=getattr(args, "ai", False),
        ai_model=getattr(args, "model", None) or os.environ.get("APME_AI_MODEL", ""),
        session_id=session_id,
        galaxy_servers=galaxy_servers or [],
        interactive=getattr(args, "interactive", False),
    )

    cmd_queue: queue.Queue[SessionCommand | None] = queue.Queue()

    def _upload_producer() -> None:
        """Stream upload chunks into the command queue in a background thread."""
        first = True
        for chunk in base_chunks:
            if first:
                cmd_chunk = ScanChunk(
                    scan_id=chunk.scan_id,
                    project_root=chunk.project_root,
                    options=chunk.options if chunk.HasField("options") else None,
                    files=list(chunk.files),
                    last=chunk.last,
                    fix_options=fix_opts,
                )
                first = False
            else:
                cmd_chunk = chunk
            cmd_queue.put(SessionCommand(upload=cmd_chunk))

    upload_thread = threading.Thread(target=_upload_producer, daemon=True)
    upload_thread.start()

    def command_iter() -> Iterator[SessionCommand]:
        """Yield commands from the queue (uploads + interactive commands).

        Yields:
            SessionCommand: Next command until a None sentinel stops iteration.
        """
        while True:
            cmd = cmd_queue.get()
            if cmd is None:
                return
            yield cmd

    channel, _ = resolve_primary(args)
    stub = primary_pb2_grpc.PrimaryStub(channel)  # type: ignore[no-untyped-call]

    use_json = getattr(args, "json", False)
    tier1_report: FixReport | None = None
    result_violations: list[ViolationDict] = []
    result_patches: list[object] = []
    got_result = False

    try:
        stream_timeout = getattr(args, "timeout", None)
        responses = stub.FixSession(command_iter(), timeout=stream_timeout)

        for event in responses:
            oneof = event.WhichOneof("event")

            if oneof == "created":
                pass  # session established

            elif oneof == "error":
                err = event.error
                sys.stderr.write(f"  Operation failed [{err.code}]: {err.message}\n")
                sys.exit(EXIT_ERROR)

            elif oneof == "progress":
                p = event.progress
                verbosity = getattr(args, "verbose", 0) or 0
                min_level = {0: 2, 1: 2}.get(verbosity, 1)
                if p.level < min_level:
                    continue
                phase = f"[{p.phase}] " if p.phase else ""
                _LEVEL_FMT = {1: dim, 3: yellow, 4: red}
                fmt = _LEVEL_FMT.get(p.level, str)
                sys.stderr.write(f"  {phase}{fmt(p.message)}\n")

            elif oneof == "tier1_complete":
                summary = event.tier1_complete
                tier1_report = summary.report if summary.HasField("report") else FixReport()
                if not use_json:
                    _render_tier1(summary)

            elif oneof == "proposals":
                proposals = list(event.proposals.proposals)
                if not proposals:
                    continue

                if getattr(args, "auto_approve", False):
                    approved = [p.id for p in proposals]
                elif use_json:
                    approved = []
                else:
                    approved = _interactive_review(proposals)

                cmd_queue.put(
                    SessionCommand(
                        approve=ApprovalRequest(approved_ids=approved),
                    )
                )

            elif oneof == "ai_triage":
                # CLI has no Include/Skip UI (SPA owns that). Escalate every
                # candidate path so --ai --interactive matches pre-triage behavior.
                paths = sorted({c.path for c in event.ai_triage.candidates if c.path})
                if not use_json:
                    sys.stderr.write(
                        f"  AI escalation: including {len(paths)} location(s)\n",
                    )
                targets = [AiEscalateTarget(path=p, rule_ids=[]) for p in paths]
                cmd_queue.put(
                    SessionCommand(ai_escalate=AiEscalateRequest(targets=targets)),
                )

            elif oneof == "approval_ack":
                ack = event.approval_ack
                sys.stderr.write(f"  Applied {ack.applied_count} proposal(s)\n")

            elif oneof == "result":
                result = event.result
                result_violations = [violation_proto_to_dict(v) for v in result.remaining_violations]
                result_patches = list(result.patches)
                got_result = True
                _write_patches(target, result.patches)
                cmd_queue.put(SessionCommand(close=CloseRequest()))

            elif oneof == "expiring":
                sys.stderr.write(
                    f"  Session expires in {event.expiring.ttl_seconds}s\n",
                )
                cmd_queue.put(SessionCommand(extend=ExtendRequest()))

            elif oneof == "data":
                payload = event.data
                if not use_json:
                    sys.stderr.write(f"  [{payload.kind}]\n")

            elif oneof == "closed":
                break

    except grpc.RpcError as e:
        sys.stderr.write(f"Engine error: {e.details()}\n")
        sys.exit(EXIT_ERROR)
    finally:
        cmd_queue.put(None)
        channel.close()

    if not got_result:
        sys.stderr.write("Error: no session result received from engine\n")
        sys.exit(EXIT_ERROR)

    show_suppressed = getattr(args, "show_suppressed", False)
    suppressed_count = 0
    if not show_suppressed:
        suppressions = load_suppressions(project_root)
        enforced_rules = {cfg.rule_id for cfg in (rule_cfgs or []) if cfg.enforced}
        suppression_result = apply_suppressions(result_violations, suppressions, enforced_rules)
        suppressed_count = len(suppression_result.suppressed)
        result_violations = suppression_result.active

    if use_json:
        _emit_json(result_violations, result_patches, tier1_report)
    elif result_violations:
        ai_count = sum(1 for v in result_violations if v.get("remediation_class") == "ai-candidate")
        manual_count = len(result_violations) - ai_count
        if ai_count:
            sys.stderr.write(f"\n{ai_count} violation(s) may be fixable with --ai (Tier 2)\n")
        if manual_count:
            sys.stderr.write(f"{manual_count} violation(s) require manual review (Tier 3)\n")

    if suppressed_count and not show_suppressed and not use_json:
        sys.stderr.write(dim(f"  ({suppressed_count} suppressed violation(s) hidden — use --show-suppressed)\n"))

    if result_violations:
        sys.exit(EXIT_VIOLATIONS)


def _emit_json(
    violations: list[ViolationDict],
    patches: list[object],
    report: FixReport | None,
) -> None:
    """Write structured JSON to stdout.

    Args:
        violations: Remaining violations as dicts.
        patches: Applied patches (proto objects).
        report: Tier 1 remediation report.
    """
    from apme_engine.cli.output import deduplicate_violations, sort_violations
    from apme_engine.remediation.partition import count_by_remediation_class, count_by_resolution

    violations = deduplicate_violations(sort_violations(violations))
    rem_counts = count_by_remediation_class(violations)
    res_counts = count_by_resolution(violations)
    diffs = [
        {"path": p.path, "diff": p.diff}  # type: ignore[attr-defined]
        for p in patches
        if getattr(p, "diff", "")
    ]
    fixable = int(report.fixed) if report else 0
    out: dict[str, object] = {
        "violations": violations,
        "count": len(violations),
        "remediation_summary": {
            "auto_fixable": fixable,
            "ai_candidate": rem_counts.get("ai-candidate", 0),
            "manual_review": rem_counts.get("manual-review", 0),
        },
        "resolution_summary": dict(res_counts),
        "diffs": diffs,
        "files_updated": sum(1 for _ in patches),
    }
    print(json.dumps(out, indent=2))


def _render_tier1(summary: object) -> None:
    format_diffs = list(summary.format_diffs)  # type: ignore[attr-defined]
    applied = list(summary.applied_patches)  # type: ignore[attr-defined]
    report = summary.report  # type: ignore[attr-defined]

    if format_diffs:
        sys.stderr.write(f"Formatted {len(format_diffs)} file(s)\n")
    if not summary.idempotency_ok:  # type: ignore[attr-defined]
        sys.stderr.write("WARNING: Formatter is not idempotent on this input.\n")
    if report:
        sys.stderr.write(
            f"Remediation: {report.passes} pass(es), "
            f"{report.fixed} fixed, "
            f"{report.remaining_ai} AI-candidate, "
            f"{report.remaining_manual} manual-review",
        )
        if report.oscillation_detected:
            sys.stderr.write(" (oscillation detected)")
        sys.stderr.write("\n")

    if applied:
        sys.stderr.write(f"Applied {len(applied)} Tier 1 patch(es)\n")


def _interactive_review(proposals: list[Proposal]) -> list[str]:
    """Interactive y/n/a/s/q review loop for proposals.

    Args:
        proposals: List of Proposal proto objects to review.

    Returns:
        List of approved proposal IDs.
    """
    approved: list[str] = []
    total = len(proposals)
    skip_all = False

    for i, prop in enumerate(proposals, 1):
        if skip_all:
            break

        sys.stderr.write(
            f"\n--- Proposal {i}/{total} [{prop.rule_id}] {prop.file} lines {prop.line_start}-{prop.line_end} "
        )
        if prop.confidence:
            sys.stderr.write(f"({prop.confidence:.0%})")
        sys.stderr.write("\n")

        if prop.explanation:
            sys.stderr.write(f"    {prop.explanation}\n")
        if prop.diff_hunk:
            sys.stdout.write(prop.diff_hunk + "\n")

        answer = _prompt_ynasq()
        if answer == "y":
            approved.append(prop.id)
        elif answer == "n":
            sys.stderr.write("  Skipped\n")
        elif answer == "a":
            approved.extend(p.id for p in proposals[i - 1 :])
            sys.stderr.write(f"  Accepted remaining {total - i + 1} proposal(s)\n")
            break
        elif answer == "s":
            skip_all = True
        elif answer == "q":
            sys.stderr.write("\nAborted.\n")
            break

    sys.stderr.write(f"\n{len(approved)} of {total} proposal(s) accepted\n")
    return approved


def _prompt_ynasq() -> str:
    while True:
        try:
            answer = (
                input(
                    "\nAccept? [y]es / [n]o / [a]ccept all / [s]kip rest / [q]uit: ",
                )
                .strip()
                .lower()
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


def _write_patches(target: Path, patches: list[object]) -> None:
    count = 0
    for p in patches:
        out_path = target / p.path if target.is_dir() else target  # type: ignore[attr-defined]
        _safe_write(out_path, p.original, p.patched)  # type: ignore[attr-defined]
        rules = ", ".join(p.applied_rules) if p.applied_rules else "changes"  # type: ignore[attr-defined]
        sys.stderr.write(f"  Fixed: {p.path} [{rules}]\n")  # type: ignore[attr-defined]
        count += 1
    sys.stderr.write(f"\n{count} file(s) updated.\n")


def _render_remaining(result: object) -> None:
    remaining = list(result.remaining_violations)  # type: ignore[attr-defined]
    if not remaining:
        return
    ai_count = sum(
        getattr(v, "remediation_class", 0) == common_pb2.REMEDIATION_CLASS_AI_CANDIDATE  # type: ignore[attr-defined]
        for v in remaining
    )
    manual_count = len(remaining) - ai_count
    if ai_count:
        sys.stderr.write(f"\n{ai_count} violation(s) may be fixable with --ai (Tier 2)\n")
    if manual_count:
        sys.stderr.write(f"{manual_count} violation(s) require manual review (Tier 3)\n")


def _safe_write(path: Path, expected_original: bytes, new_content: bytes) -> None:
    current = path.read_bytes()
    if current != expected_original:
        sys.stderr.write(
            f"WARNING: {path} was modified since scan — skipping to avoid data loss.\n",
        )
        return
    path.write_bytes(new_content)
