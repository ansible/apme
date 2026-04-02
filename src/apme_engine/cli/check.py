"""Check subcommand: runs full remediation pipeline via FixSession in check mode (ADR-039)."""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
from collections.abc import Iterator

import grpc

from apme.v1 import primary_pb2_grpc
from apme.v1.primary_pb2 import (
    ApprovalRequest,
    CloseRequest,
    ExtendRequest,
    FixReport,
    SessionCommand,
)
from apme_engine.cli._convert import violation_proto_to_dict
from apme_engine.cli._galaxy_config import discover_galaxy_servers
from apme_engine.cli._models import ViolationDict
from apme_engine.cli._project_root import derive_session_id, discover_project_root
from apme_engine.cli._rules_yml import load_rule_configs_from_project
from apme_engine.cli.ansi import dim, red, yellow
from apme_engine.cli.discovery import resolve_primary
from apme_engine.cli.output import (
    deduplicate_violations,
    render_check_results,
    sort_violations,
)
from apme_engine.daemon.chunked_fs import yield_scan_chunks
from apme_engine.remediation.partition import count_by_remediation_class, count_by_resolution

_SAFE_SESSION_RE = __import__("re").compile(r"^[A-Za-z0-9_\-]+$")


class _ScanSummaryCompat:
    """Wraps FixReport tier-1 fields for format_remediation_summary / render_check_results.

    Args:
        report: Completed FixReport or None for empty summary.
    """

    __slots__ = ("ai_candidate", "auto_fixable", "by_resolution", "manual_review")

    def __init__(self, report: FixReport | None) -> None:
        if report is None:
            self.auto_fixable = 0
            self.ai_candidate = 0
            self.manual_review = 0
            self.by_resolution: dict[str, int] = {}
            return
        self.auto_fixable = int(report.fixed)
        self.ai_candidate = int(report.remaining_ai)
        self.manual_review = int(report.remaining_manual)
        self.by_resolution = {}


def _resolve_session_id(args: argparse.Namespace) -> str:
    """Resolve the session ID from CLI args or project root discovery.

    Args:
        args: Parsed CLI arguments with optional ``session`` and ``target``.

    Returns:
        Session ID string.

    Raises:
        SystemExit: If explicit --session value contains invalid characters.
    """
    explicit: str | None = getattr(args, "session", None)
    if explicit:
        if not _SAFE_SESSION_RE.match(explicit):
            sys.stderr.write(
                f"Error: --session value {explicit!r} is invalid. "
                "Must contain only letters, digits, hyphens, and underscores.\n"
            )
            raise SystemExit(2)
        return explicit
    target: str = getattr(args, "target", ".")
    project_root = discover_project_root(target)
    return derive_session_id(project_root)


def run_check(args: argparse.Namespace) -> None:
    """Execute the check subcommand.

    Args:
        args: Parsed CLI arguments.
    """
    verbosity = getattr(args, "verbose", 0) or 0
    session_id = _resolve_session_id(args)

    target: str = getattr(args, "target", ".")
    project_root = discover_project_root(target)
    galaxy_servers = discover_galaxy_servers(project_root) or None
    rule_cfgs = load_rule_configs_from_project(project_root)

    try:
        chunks = yield_scan_chunks(
            args.target,
            project_root_name="project",
            ansible_core_version=getattr(args, "ansible_version", None),
            collection_specs=getattr(args, "collections", None),
            session_id=session_id,
            galaxy_servers=galaxy_servers,
            rule_configs=rule_cfgs or None,
        )
    except FileNotFoundError as e:
        sys.stderr.write(f"{e}\n")
        sys.exit(1)

    min_level = {0: 3, 1: 2}.get(verbosity, 1)

    cmd_queue: queue.Queue[SessionCommand | None] = queue.Queue()
    scan_id_holder: list[str] = [""]

    def _upload_producer() -> None:
        first = True
        for chunk in chunks:
            if first:
                scan_id_holder[0] = chunk.scan_id or ""
                first = False
            cmd_queue.put(SessionCommand(upload=chunk))

    upload_thread = threading.Thread(target=_upload_producer, daemon=True)
    upload_thread.start()

    def command_iter() -> Iterator[SessionCommand]:
        """Yield commands from the queue until a None sentinel stops iteration.

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

    tier1_report: FixReport | None = None
    violations: list[ViolationDict] = []
    patches: list[object] = []
    got_result = False

    try:
        check_timeout = float(getattr(args, "timeout", None) or 120)
        responses = stub.FixSession(command_iter(), timeout=check_timeout)

        for event in responses:
            oneof = event.WhichOneof("event")

            if oneof == "created":
                continue

            if oneof == "progress":
                p = event.progress
                if p.level >= min_level:
                    phase = f"[{p.phase}] " if p.phase else ""
                    _LEVEL_FMT = {1: dim, 3: yellow, 4: red}
                    fmt = _LEVEL_FMT.get(p.level, str)
                    sys.stderr.write(f"  {phase}{fmt(p.message)}\n")
                continue

            if oneof == "tier1_complete":
                t1 = event.tier1_complete
                tier1_report = t1.report if t1.HasField("report") else FixReport()
                continue

            if oneof == "proposals":
                cmd_queue.put(SessionCommand(approve=ApprovalRequest(approved_ids=[])))
                continue

            if oneof == "result":
                res = event.result
                violations = [violation_proto_to_dict(v) for v in res.remaining_violations]
                patches = list(res.patches)
                got_result = True
                cmd_queue.put(SessionCommand(close=CloseRequest()))
                continue

            if oneof == "expiring":
                sys.stderr.write(
                    f"  Session expires in {event.expiring.ttl_seconds}s\n",
                )
                cmd_queue.put(SessionCommand(extend=ExtendRequest()))
                continue

            if oneof == "closed":
                break

    except grpc.RpcError as e:
        sys.stderr.write(f"Engine error: {e.details()}\n")
        sys.exit(1)
    finally:
        cmd_queue.put(None)
        channel.close()

    if not got_result:
        sys.stderr.write("Error: no session result received from engine\n")
        sys.exit(1)

    violations = deduplicate_violations(sort_violations(violations))
    scan_id = scan_id_holder[0]

    if args.json:
        rem_counts = count_by_remediation_class(violations)
        res_counts = count_by_resolution(violations)
        diffs = [
            {"path": p.path, "diff": p.diff}  # type: ignore[attr-defined]
            for p in patches
            if getattr(p, "diff", "")
        ]
        fixable_from_report = int(tier1_report.fixed) if tier1_report else 0
        out: dict[str, object] = {
            "violations": violations,
            "count": len(violations),
            "scan_id": scan_id,
            "remediation_summary": {
                "auto_fixable": fixable_from_report,
                "ai_candidate": rem_counts.get("ai-candidate", 0),
                "manual_review": rem_counts.get("manual-review", 0),
            },
            "resolution_summary": dict(res_counts),
            "diffs": diffs,
        }
        print(json.dumps(out, indent=2))
        return

    show_diff = getattr(args, "diff", False)
    if show_diff and patches:
        for p in patches:
            diff_text = getattr(p, "diff", "")
            if diff_text:
                sys.stdout.write(diff_text)
        diff_count = sum(1 for p in patches if getattr(p, "diff", ""))
        sys.stderr.write(f"\n{diff_count} file(s) would be changed by remediate.\n\n")

    display_summary = _ScanSummaryCompat(tier1_report)
    render_check_results(violations, scan_id=scan_id, scan_time_ms=None, summary=display_summary)
