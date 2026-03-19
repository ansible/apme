"""Fix subcommand: bidirectional FixSession stream (ADR-028).

Creates a fix session, streams progress events, handles interactive proposal
review (or --auto-approve), and writes patched files on completion.
"""

from __future__ import annotations

import argparse
import queue
import sys
from collections.abc import Iterator
from pathlib import Path

import grpc

from apme.v1 import primary_pb2_grpc
from apme.v1.primary_pb2 import (
    ApprovalRequest,
    CloseRequest,
    ExtendRequest,
    FixOptions,
    ScanChunk,
    SessionCommand,
)
from apme_engine.cli.discovery import resolve_primary
from apme_engine.daemon.chunked_fs import yield_scan_chunks


def run_fix(args: argparse.Namespace) -> None:
    target = Path(args.target).resolve()
    if not target.exists():
        sys.stderr.write(f"Target not found: {args.target}\n")
        sys.exit(1)

    try:
        base_chunks = yield_scan_chunks(
            str(target),
            project_root_name="project",
            ansible_core_version=getattr(args, "ansible_version", None),
            collection_specs=getattr(args, "collections", None),
        )
    except FileNotFoundError as e:
        sys.stderr.write(f"{e}\n")
        sys.exit(1)

    fix_opts = FixOptions(
        max_passes=getattr(args, "max_passes", 5),
        ansible_core_version=getattr(args, "ansible_version", None) or "",
        collection_specs=getattr(args, "collections", None) or [],
        enable_ai=getattr(args, "ai", False),
    )

    cmd_queue: queue.Queue[SessionCommand | None] = queue.Queue()

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

    def command_iter() -> Iterator[SessionCommand]:
        while True:
            cmd = cmd_queue.get()
            if cmd is None:
                return
            yield cmd

    channel, _ = resolve_primary(args)
    stub = primary_pb2_grpc.PrimaryStub(channel)  # type: ignore[no-untyped-call]

    exit_code = 0

    try:
        responses = stub.FixSession(command_iter(), timeout=600)

        for event in responses:
            oneof = event.WhichOneof("event")

            if oneof == "created":
                pass  # session established

            elif oneof == "progress":
                p = event.progress
                if p.level <= 1 and getattr(args, "verbose", 0) < 1:
                    continue
                sys.stderr.write(f"  {p.message}\n")

            elif oneof == "tier1_complete":
                summary = event.tier1_complete
                _render_tier1(summary)

                if args.check:
                    total = len(summary.applied_patches) + len(summary.format_diffs)
                    if total:
                        sys.stderr.write(f"{total} file(s) would be changed.\n")
                        exit_code = 1
                    else:
                        sys.stderr.write("No changes needed.\n")
                    cmd_queue.put(SessionCommand(close=CloseRequest()))
                    continue

            elif oneof == "proposals":
                proposals = list(event.proposals.proposals)
                if not proposals:
                    continue

                if getattr(args, "auto_approve", False):
                    approved = [p.id for p in proposals]
                else:
                    approved = _interactive_review(proposals)

                cmd_queue.put(SessionCommand(
                    approve=ApprovalRequest(approved_ids=approved),
                ))

            elif oneof == "approval_ack":
                ack = event.approval_ack
                sys.stderr.write(f"  Applied {ack.applied_count} proposal(s)\n")

            elif oneof == "result":
                result = event.result
                if getattr(args, "apply", False):
                    _write_patches(target, result.patches)
                else:
                    _show_diffs(result.patches)

                _render_remaining(result)
                cmd_queue.put(SessionCommand(close=CloseRequest()))

            elif oneof == "expiring":
                sys.stderr.write(
                    f"  Session expires in {event.expiring.ttl_seconds}s\n",
                )
                cmd_queue.put(SessionCommand(extend=ExtendRequest()))

            elif oneof == "data":
                payload = event.data
                if getattr(args, "json", False):
                    import json

                    from google.protobuf.json_format import MessageToDict

                    json.dump(
                        {"kind": payload.kind, "data": MessageToDict(payload.data)},
                        sys.stdout,
                    )
                    sys.stdout.write("\n")
                else:
                    sys.stderr.write(f"  [{payload.kind}]\n")

            elif oneof == "closed":
                break

    except grpc.RpcError as e:
        sys.stderr.write(f"Engine error: {e.details()}\n")
        sys.exit(1)
    finally:
        cmd_queue.put(None)
        channel.close()

    if exit_code:
        sys.exit(exit_code)


def _render_tier1(summary: object) -> None:
    format_diffs = list(summary.format_diffs)  # type: ignore[attr-defined]
    patches = list(summary.applied_patches)  # type: ignore[attr-defined]
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


def _interactive_review(proposals: list[object]) -> list[str]:
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
            f"\n--- Proposal {i}/{total} "
            f"[{prop.rule_id}] "  # type: ignore[attr-defined]
            f"{prop.file} "  # type: ignore[attr-defined]
            f"lines {prop.line_start}-{prop.line_end} "  # type: ignore[attr-defined]
        )
        if prop.confidence:  # type: ignore[attr-defined]
            sys.stderr.write(f"({prop.confidence:.0%})")  # type: ignore[attr-defined]
        sys.stderr.write("\n")

        if prop.explanation:  # type: ignore[attr-defined]
            sys.stderr.write(f"    {prop.explanation}\n")  # type: ignore[attr-defined]
        if prop.diff_hunk:  # type: ignore[attr-defined]
            sys.stdout.write(prop.diff_hunk + "\n")  # type: ignore[attr-defined]

        answer = _prompt_ynasq()
        if answer == "y":
            approved.append(prop.id)  # type: ignore[attr-defined]
        elif answer == "n":
            sys.stderr.write("  Skipped\n")
        elif answer == "a":
            approved.extend(p.id for p in proposals[i - 1:])  # type: ignore[attr-defined]
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
            answer = input(
                "\nAccept? [y]es / [n]o / [a]ccept all / [s]kip rest / [q]uit: ",
            ).strip().lower()
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


def _show_diffs(patches: list[object]) -> None:
    for p in patches:
        if p.diff:  # type: ignore[attr-defined]
            sys.stdout.write(p.diff)  # type: ignore[attr-defined]
    count = len(patches)
    if count:
        sys.stderr.write(
            f"\n{count} file(s) would be changed. Use --apply to write.\n",
        )
    else:
        sys.stderr.write("No changes needed.\n")


def _render_remaining(result: object) -> None:
    remaining = list(result.remaining_violations)  # type: ignore[attr-defined]
    if not remaining:
        return
    ai_count = sum(
        1 for v in remaining
        if getattr(v, "remediation_class", "") == "ai_candidate"
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
