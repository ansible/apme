"""Scan subcommand: stream files to Primary.ScanStream, render violations."""

from __future__ import annotations

import argparse
import json
import sys

import grpc

from apme.v1 import primary_pb2_grpc
from apme_engine.cli._convert import violation_proto_to_dict
from apme_engine.cli._models import ViolationDict
from apme_engine.cli.discovery import resolve_primary
from apme_engine.cli.output import (
    deduplicate_violations,
    diag_to_dict,
    print_diagnostics_v,
    print_diagnostics_vv,
    render_scan_results,
    sort_violations,
)
from apme_engine.daemon.chunked_fs import yield_scan_chunks


def run_scan(args: argparse.Namespace) -> None:
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

    channel, _ = resolve_primary(args)
    stub = primary_pb2_grpc.PrimaryStub(channel)  # type: ignore[no-untyped-call]
    try:
        resp = stub.ScanStream(chunks, timeout=120)
    except grpc.RpcError as e:
        sys.stderr.write(f"Engine error: {e.details()}\n")
        sys.exit(1)
    finally:
        channel.close()

    violations: list[ViolationDict] = [violation_proto_to_dict(v) for v in resp.violations]
    violations = deduplicate_violations(sort_violations(violations))

    has_diag = resp.HasField("diagnostics")

    if args.json:
        summary = resp.summary if resp.HasField("summary") else None
        out: dict[str, object] = {
            "violations": violations,
            "count": len(violations),
            "scan_id": resp.scan_id,
            "remediation_summary": {
                "auto_fixable": summary.auto_fixable if summary else 0,
                "ai_candidate": summary.ai_candidate if summary else 0,
                "manual_review": summary.manual_review if summary else 0,
            },
            "resolution_summary": dict(summary.by_resolution) if summary else {},
        }
        if verbosity and has_diag:
            out["diagnostics"] = diag_to_dict(resp.diagnostics)
        print(json.dumps(out, indent=2))
        return

    scan_time_ms = resp.diagnostics.total_ms if has_diag else None
    summary = resp.summary if resp.HasField("summary") else None
    render_scan_results(violations, scan_id=resp.scan_id, scan_time_ms=scan_time_ms, summary=summary)

    if verbosity >= 2 and has_diag:
        print_diagnostics_vv(resp.diagnostics)
    elif verbosity >= 1 and has_diag:
        print_diagnostics_v(resp.diagnostics)
