"""Remediate subcommand: full remediation pipeline with Tier 1 auto-fix and optional AI proposals (ADR-028, ADR-039).

Creates a fix session, streams progress events, handles interactive proposal
review (or --auto-approve), and writes patched files on completion.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import random
import sys
import threading
import time
from collections.abc import Iterable, Iterator
from pathlib import Path

import grpc

from apme.v1 import common_pb2, engine_pb2_grpc
from apme.v1.engine_pb2 import (
    AiEscalateRequest,
    AiEscalateTarget,
    ApprovalRequest,
    CloseRequest,
    ExtendRequest,
    FilePatch,
    FixOptions,
    FixReport,
    Proposal,
    ScanChunk,
    SessionCommand,
    SessionResult,
    Tier1Summary,
)
from apme_engine.cli._exit_codes import EXIT_ERROR, EXIT_VIOLATIONS
from apme_engine.cli._galaxy_config import discover_galaxy_servers
from apme_engine.cli._project_root import derive_session_id, discover_project_root
from apme_engine.cli._rules_yml import load_rule_configs_from_project
from apme_engine.cli._suppressions import apply_suppressions, load_suppressions
from apme_engine.cli.ansi import dim, red, yellow
from apme_engine.cli.discovery import resolve_engine
from apme_engine.daemon.chunked_fs import yield_scan_chunks
from apme_engine.daemon.violation_convert import violation_proto_to_dict
from apme_engine.engine.models import ViolationDict


def run_remediate(args: argparse.Namespace) -> None:
    """Execute the remediate subcommand.

    A transient transport failure before any result is retried once with
    jittered backoff; every attempt builds and tears down its own channel,
    producer thread, and command queue so no state leaks across retries.
    A background upload failure fails the run even when a server result
    arrived first (a partial upload must never silently pass). Patches are
    written to disk only after the producer is verified clean, so a failed
    upload never mutates the tree. A transport drop after a result arrived
    keeps the buffered result instead of discarding success. The retry
    log carries the failed attempt's scan_id and attempt number so the
    two server-side scan rows for one invocation stay attributable.

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

    def _make_chunks() -> Iterator[ScanChunk]:
        """Build a fresh upload-chunk stream (re-runnable for reconnect retry).

        Each call mints a fresh scan_id inside ``yield_scan_chunks``: a
        retry is a brand-new server session (``SessionStore.create`` mints
        a fresh session with no dedup on ``scan_id``; ``scan_id`` is only
        a reporting label), so sharing an ID across attempts buys nothing
        and risks reporting-row collision. The attempt-teardown
        ``channel.close()`` cancels the first session's stream, and
        interactive approvals are re-prompted on the retry.

        Upload failures propagate to the caller — the background producer
        records them and the main thread reports them, so this generator
        never exits the process itself.

        Yields:
            ScanChunk: Scan upload chunks for the FixSession stream.
        """
        yield from yield_scan_chunks(
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

    # ADR-068: remediate omits the client-side gRPC deadline and relies
    # on server-side budget + stall enforcement, so a None --timeout
    # (server adaptive budget) is intentional, not a hang.
    stream_timeout = getattr(args, "timeout", None)

    use_json = getattr(args, "json", False)
    tier1_report: FixReport | None = None
    result_violations: list[ViolationDict] = []
    result_patches: list[FilePatch] = []
    result_files_written = 0
    got_result = False
    write_failed = False

    def _run_uploads(
        cmd_queue: queue.Queue[SessionCommand | None],
        producer_errors: list[Exception],
        attempt_scan_ids: list[str],
        stop_event: threading.Event,
    ) -> None:
        """Stream upload chunks into the command queue in a background thread.

        Producer failures are recorded for the main thread — never
        ``sys.exit`` here, which would only kill this daemon thread and
        hang the consumer on an empty queue. Abrupt thread death
        (``SystemExit``/``KeyboardInterrupt``/``CancelledError``) still
        enqueues the ``None`` sentinel for liveness, then re-raises to
        preserve semantics. The ``None`` sentinel is only
        enqueued on the error path so the consumer terminates; on success
        the stream stays open for proposals/approvals and the outer
        attempt-teardown ``finally`` delivers the single terminating
        ``None``. On the error path the teardown ``finally`` enqueues a
        second ``None`` that is left over in the discarded per-attempt
        queue — harmless because each attempt owns a fresh queue.

        Args:
            cmd_queue: Per-attempt command queue feeding the stream.
            producer_errors: Per-attempt holder for background failures.
            attempt_scan_ids: Per-attempt holder for the first upload
                chunk's scan_id, recorded before enqueue so the main
                thread can attribute a retry to the failed attempt's
                server-side scan row.
            stop_event: When set, the producer exits without enqueueing
                further chunks (used during per-attempt teardown).

        Raises:
            BaseException: Re-raised after enqueueing the ``None`` sentinel
                when the producer dies abruptly (``SystemExit``,
                ``KeyboardInterrupt``, ``CancelledError``) so the consumer
                never blocks forever.
        """
        try:
            first = True
            for chunk in _make_chunks():
                if stop_event.is_set():
                    return
                if not attempt_scan_ids and chunk.scan_id:
                    attempt_scan_ids.append(chunk.scan_id)
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
        except Exception as exc:  # noqa: BLE001 — recorded, reported by main thread
            producer_errors.append(exc)
            cmd_queue.put(None)
        except BaseException:
            # SystemExit/KeyboardInterrupt/CancelledError would otherwise
            # kill this thread with no sentinel and hang _drain_commands
            # forever — enqueue it for liveness, then re-raise.
            cmd_queue.put(None)
            raise

    def _drain_commands(cmd_queue: queue.Queue[SessionCommand | None]) -> Iterator[SessionCommand]:
        """Yield commands from the queue (uploads + interactive commands).

        Args:
            cmd_queue: Per-attempt command queue feeding the stream.

        Yields:
            SessionCommand: Next command until a None sentinel stops iteration.
        """
        while True:
            cmd = cmd_queue.get()
            if cmd is None:
                return
            yield cmd

    for attempt in range(2):
        retry = False
        # Reset per-attempt state so a stale attempt-1 report does not pair
        # with attempt-2 violations/patches after a reconnect retry.
        tier1_report = None
        result_violations = []
        result_patches = []
        result_files_written = 0
        cmd_queue: queue.Queue[SessionCommand | None] = queue.Queue()
        producer_errors: list[Exception] = []
        attempt_scan_ids: list[str] = []

        stop_event = threading.Event()
        upload_thread = threading.Thread(
            target=_run_uploads,
            args=(cmd_queue, producer_errors, attempt_scan_ids, stop_event),
            daemon=True,
        )
        upload_thread.start()

        channel, _ = resolve_engine(args)
        stub = engine_pb2_grpc.EngineStub(channel)  # type: ignore[no-untyped-call]
        try:
            responses = stub.FixSession(_drain_commands(cmd_queue), timeout=stream_timeout)

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
                    # Patches stay buffered: _write_patches runs only after
                    # the producer is verified clean (below), so a partial
                    # upload never mutates disk.
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
            # Only UNAVAILABLE is retried. With stream_timeout=None a
            # DEADLINE_EXCEEDED is the server adaptive budget expiring —
            # retrying would re-run the whole scan and double load.
            transient = e.code() == grpc.StatusCode.UNAVAILABLE
            # Uploads re-stream deterministically from disk and patches are
            # only written once a result arrives and the producer is proven
            # clean, so retrying before any result is safe. A retry starts
            # a fresh server session with a fresh scan_id; the
            # attempt-teardown channel.close() cancels the first session's
            # stream, and interactive approvals are re-prompted on the new
            # session.
            if got_result:
                # Transport drop after a result arrived: keep the buffered
                # result and fall through to the producer check + deferred
                # write below instead of discarding success.
                sys.stderr.write(
                    dim(
                        f"  Connection {e.code().name} after result; using received result\n",
                    )
                )
            elif transient and attempt == 0:
                prior_scan = attempt_scan_ids[0] if attempt_scan_ids else "unknown"
                sys.stderr.write(
                    dim(
                        f"  Connection {e.code().name} before result "
                        f"(attempt {attempt + 1}, scan_id={prior_scan}); retrying session once...\n"
                    )
                )
                retry = True
            else:
                sys.stderr.write(f"Engine error: {e.details()}\n")
                sys.exit(EXIT_ERROR)
        finally:
            # Tear down this attempt before any retry rebuilds it.
            stop_event.set()
            cmd_queue.put(None)
            upload_thread.join(timeout=30)
            if upload_thread.is_alive():
                sys.stderr.write("Error: upload worker did not stop in time\n")
                sys.exit(EXIT_ERROR)
            channel.close()

        if producer_errors:
            sys.stderr.write(f"{producer_errors[0]}\n")
            sys.exit(EXIT_ERROR)
        if got_result:
            # Deferred until the producer is proven clean above: a partial
            # or synthetic result must never mutate disk on a failed run.
            result_files_written, write_failed = _write_patches(target, result_patches)
            break
        if retry:
            time.sleep(1.0 + random.uniform(0, 1.0))
            continue
        break

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
        _emit_json(result_violations, result_patches, tier1_report, result_files_written)
    if write_failed:
        sys.exit(EXIT_ERROR)
    if not use_json and result_violations:
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
    patches: list[FilePatch],
    report: FixReport | None,
    files_updated: int | None = None,
) -> None:
    """Write structured JSON to stdout.

    Args:
        violations: Remaining violations as dicts.
        patches: Applied patches (FilePatch protos).
        report: Tier 1 remediation report.
        files_updated: Files actually written to disk. Defaults to the
            patch count when not provided (callers that did not go through
            ``_write_patches``).
    """
    from apme_engine.cli.output import deduplicate_violations, sort_violations
    from apme_engine.remediation.partition import count_by_remediation_class, count_by_resolution

    violations = deduplicate_violations(sort_violations(violations))
    rem_counts = count_by_remediation_class(violations)
    res_counts = count_by_resolution(violations)
    diffs = [{"path": p.path, "diff": p.diff} for p in patches if p.diff]
    fixable = int(report.fixed) if report else 0
    written = files_updated if files_updated is not None else sum(1 for _ in patches)
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
        "files_updated": written,
    }
    print(json.dumps(out, indent=2))


def _render_tier1(summary: Tier1Summary) -> None:
    format_diffs = list(summary.format_diffs)
    applied = list(summary.applied_patches)
    report = summary.report if summary.HasField("report") else None  # type: ignore[attr-defined]

    if format_diffs:
        sys.stderr.write(f"Formatted {len(format_diffs)} file(s)\n")
    if not summary.idempotency_ok:
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


def _write_patches(target: Path, patches: Iterable[FilePatch]) -> tuple[int, bool]:
    """Write patched files to disk, skipping failures.

    Args:
        target: Scan target directory or single file.
        patches: Patches to apply.

    Returns:
        Tuple of (files actually written, whether any patch failed to apply).
    """
    count = 0
    had_failures = False
    for p in patches:
        out_path = target / p.path if target.is_dir() else target
        try:
            if _safe_write(out_path, p.original, p.patched):
                rules = ", ".join(p.applied_rules) if p.applied_rules else "changes"
                sys.stderr.write(f"  Fixed: {p.path} [{rules}]\n")
                count += 1
            else:
                had_failures = True
        except OSError as exc:
            sys.stderr.write(f"WARNING: skipping {p.path}: {exc}\n")
            had_failures = True
    sys.stderr.write(f"\n{count} file(s) updated.\n")
    return count, had_failures


def _render_remaining(result: SessionResult) -> None:
    remaining = list(result.remaining_violations)
    if not remaining:
        return
    ai_count = sum(
        # Ignore tracks the stale checked-in stub (missing enum constant);
        # same pattern as violation_convert.py. Proper fix is upstream #506.
        v.remediation_class == common_pb2.REMEDIATION_CLASS_AI_CANDIDATE  # type: ignore[attr-defined]
        for v in remaining
    )
    manual_count = len(remaining) - ai_count
    if ai_count:
        sys.stderr.write(f"\n{ai_count} violation(s) may be fixable with --ai (Tier 2)\n")
    if manual_count:
        sys.stderr.write(f"{manual_count} violation(s) require manual review (Tier 3)\n")


def _safe_write(path: Path, expected_original: bytes, new_content: bytes) -> bool:
    current = path.read_bytes()
    if current != expected_original:
        sys.stderr.write(
            f"WARNING: {path} was modified since scan — skipping to avoid data loss.\n",
        )
        return False
    path.write_bytes(new_content)
    return True
