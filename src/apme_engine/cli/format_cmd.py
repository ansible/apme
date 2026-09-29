"""Format subcommand: stream files to Engine.FormatStream, apply/show diffs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import grpc

from apme.v1 import engine_pb2_grpc
from apme_engine.cli._exit_codes import EXIT_ERROR, EXIT_VIOLATIONS
from apme_engine.cli._project_root import (
    common_scan_base,
    derive_session_id,
    discover_project_root,
    discover_project_root_for_targets,
    normalize_targets,
    path_within_targets,
    resolve_within_base,
)
from apme_engine.cli.discovery import resolve_engine
from apme_engine.cli.output import render_logs
from apme_engine.daemon.chunked_fs import yield_scan_chunks


def run_format(args: argparse.Namespace) -> None:
    """Execute the format subcommand.

    Args:
        args: Parsed CLI arguments.
    """
    targets = normalize_targets(getattr(args, "target", "."))
    for candidate in targets:
        if not Path(candidate).exists():
            sys.stderr.write(f"Target not found: {candidate}\n")
            sys.exit(EXIT_ERROR)
    try:
        base = common_scan_base(targets)
    except FileNotFoundError as e:
        sys.stderr.write(f"{e}\n")
        sys.exit(EXIT_ERROR)

    explicit_session = getattr(args, "session", None)
    try:
        if explicit_session:
            session_id = explicit_session
        elif len(targets) == 1:
            project_root = discover_project_root(targets[0])
            session_id = derive_session_id(project_root)
        else:
            project_root = discover_project_root_for_targets(targets)
            session_id = derive_session_id(project_root)
    except FileNotFoundError as e:
        sys.stderr.write(f"{e}\n")
        sys.exit(EXIT_ERROR)

    try:
        chunks = yield_scan_chunks(
            targets,
            project_root_name="project",
            session_id=session_id,
            exclude_patterns=getattr(args, "exclude", None),
        )
    except FileNotFoundError as e:
        sys.stderr.write(f"{e}\n")
        sys.exit(EXIT_ERROR)

    channel, _ = resolve_engine(args)
    stub = engine_pb2_grpc.EngineStub(channel)  # type: ignore[no-untyped-call]
    try:
        resp = stub.FormatStream(chunks, timeout=120)
    except FileNotFoundError as e:
        # Chunks stream lazily from disk; a deletion race between target
        # pre-validation and the upload walk surfaces here, not at
        # yield_scan_chunks() call time.
        sys.stderr.write(f"{e}\n")
        sys.exit(EXIT_ERROR)
    except grpc.RpcError as e:
        sys.stderr.write(f"Engine error: {e.details()}\n")
        sys.exit(EXIT_ERROR)
    finally:
        channel.close()

    verbosity = getattr(args, "verbose", 0) or 0
    render_logs(resp.logs, verbosity)

    diffs = list(resp.diffs)

    if not diffs:
        sys.stderr.write("All files already formatted.\n")
        return

    # --check mode: exit 1 if anything would change
    if args.check:
        for d in diffs:
            sys.stderr.write(f"Would reformat: {d.path}\n")
        sys.stderr.write(f"\n{len(diffs)} file(s) would be reformatted.\n")
        sys.exit(EXIT_VIOLATIONS)

    if args.apply:
        written = 0
        write_failed = False
        for d in diffs:
            try:
                out_path = resolve_within_base(base, d.path)
            except ValueError as exc:
                sys.stderr.write(f"WARNING: skipping {d.path}: {exc}\n")
                write_failed = True
                continue
            if not path_within_targets(targets, out_path):
                sys.stderr.write(f"WARNING: skipping {d.path}: outside selected targets\n")
                write_failed = True
                continue
            if _safe_write(out_path, d.original, d.formatted):
                sys.stderr.write(f"Formatted: {d.path}\n")
                written += 1
            else:
                write_failed = True
        sys.stderr.write(f"\n{written} file(s) reformatted.\n")
        if write_failed:
            sys.exit(EXIT_ERROR)
    else:
        for d in diffs:
            sys.stdout.write(d.diff)
        sys.stderr.write(f"\n{len(diffs)} file(s) would be reformatted. Use --apply to write.\n")


def _safe_write(path: Path, expected_original: bytes, new_content: bytes) -> bool:
    """Write new_content to path, verifying current content matches expected_original.

    Args:
        path: Target file path.
        expected_original: Content the file should currently contain.
        new_content: Replacement content to write.

    Returns:
        True if the file was written, False if skipped.
    """
    try:
        current = path.read_bytes()
    except OSError as exc:
        sys.stderr.write(f"WARNING: {path} could not be read — skipping ({exc}).\n")
        return False
    if current != expected_original:
        sys.stderr.write(f"WARNING: {path} was modified since scan — skipping to avoid data loss.\n")
        return False
    try:
        path.write_bytes(new_content)
    except OSError as exc:
        sys.stderr.write(f"WARNING: {path} could not be written — skipping ({exc}).\n")
        return False
    return True
