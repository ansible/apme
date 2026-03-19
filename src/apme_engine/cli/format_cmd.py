"""Format subcommand: stream files to Primary.FormatStream, apply/show diffs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import grpc

from apme.v1 import primary_pb2_grpc
from apme_engine.cli.discovery import resolve_primary
from apme_engine.daemon.chunked_fs import yield_scan_chunks


def run_format(args: argparse.Namespace) -> None:
    target = Path(args.target).resolve()
    if not target.exists():
        sys.stderr.write(f"Target not found: {args.target}\n")
        sys.exit(1)

    try:
        chunks = yield_scan_chunks(str(target), project_root_name="project")
    except FileNotFoundError as e:
        sys.stderr.write(f"{e}\n")
        sys.exit(1)

    channel, _ = resolve_primary(args)
    stub = primary_pb2_grpc.PrimaryStub(channel)  # type: ignore[no-untyped-call]
    try:
        resp = stub.FormatStream(chunks, timeout=120)
    except grpc.RpcError as e:
        sys.stderr.write(f"Engine error: {e.details()}\n")
        sys.exit(1)
    finally:
        channel.close()

    diffs = list(resp.diffs)

    if not diffs:
        sys.stderr.write("All files already formatted.\n")
        return

    # --check mode: exit 1 if anything would change
    if args.check:
        for d in diffs:
            sys.stderr.write(f"Would reformat: {d.path}\n")
        sys.stderr.write(f"\n{len(diffs)} file(s) would be reformatted.\n")
        sys.exit(1)

    if args.apply:
        for d in diffs:
            out_path = target / d.path if target.is_dir() else target
            _safe_write(out_path, d.original, d.formatted)
            sys.stderr.write(f"Formatted: {d.path}\n")
        sys.stderr.write(f"\n{len(diffs)} file(s) reformatted.\n")
    else:
        for d in diffs:
            sys.stdout.write(d.diff)
        sys.stderr.write(f"\n{len(diffs)} file(s) would be reformatted. Use --apply to write.\n")


def _safe_write(path: Path, expected_original: bytes, new_content: bytes) -> None:
    """Write new_content to path, verifying current content matches expected_original.

    Args:
        path: Target file path.
        expected_original: Content the file should currently contain.
        new_content: Replacement content to write.
    """
    current = path.read_bytes()
    if current != expected_original:
        sys.stderr.write(
            f"WARNING: {path} was modified since scan — skipping to avoid data loss.\n"
        )
        return
    path.write_bytes(new_content)
