"""SBOM subcommand: generate CycloneDX 1.5 SBOM via gRPC GenerateSbom RPC."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import IO

import grpc

from apme.v1 import primary_pb2, primary_pb2_grpc
from apme_engine.cli.check import _resolve_session_id
from apme_engine.cli.discovery import resolve_primary
from apme_engine.daemon.chunked_fs import yield_scan_chunks


def _render_summary(
    response: primary_pb2.SbomResponse,
    verbose: int = 0,
    file: IO[str] | None = None,
) -> None:
    """Render SBOM summary to the given file handle.

    Args:
        response: SbomResponse from the gRPC call.
        verbose: Verbosity level (0=counts-only, >=1=grouped table).
        file: Output stream (default: sys.stderr).
    """
    out = file or sys.stderr

    if verbose < 1:
        out.write(
            f"SBOM: {response.collection_count} collections, "
            f"{response.package_count} packages, "
            f"{response.role_count} roles "
            f"({response.total_count} total)\n"
        )
        return

    # Verbose: grouped table
    groups: dict[str, list[primary_pb2.SbomComponentDetail]] = {}
    for comp in response.components:
        groups.setdefault(comp.type, []).append(comp)

    type_order = ["collection", "package", "role"]
    count_map = {
        "collection": response.collection_count,
        "package": response.package_count,
        "role": response.role_count,
    }

    for ctype in type_order:
        comps = groups.get(ctype, [])
        label = ctype.capitalize() + "s"
        count = count_map.get(ctype, len(comps))
        out.write(f"{label} ({count}):\n")
        out.write(f"  {'Name':<34}{'Version':<10}{'License':<20}\n")
        for c in comps:
            name = c.name
            if c.name_inferred:
                name += " [inferred]"
            version = "-" if c.version_missing else (c.version or "-")
            license_str = c.license or "-"
            out.write(f"  {name:<34}{version:<10}{license_str:<20}\n")


def run_sbom(args: argparse.Namespace) -> None:
    """Execute the sbom subcommand.

    Args:
        args: Parsed CLI arguments.
    """
    session_id = _resolve_session_id(args)
    verbose = getattr(args, "verbose", 0) or 0

    channel: grpc.Channel | None = None

    try:
        channel, _ = resolve_primary(args)
        stub = primary_pb2_grpc.PrimaryStub(channel)  # type: ignore[no-untyped-call]

        if args.summary:
            # Summary-only: no file upload, just requery existing session
            chunk = primary_pb2.ScanChunk(
                scan_id="",
                project_root="",
                options=primary_pb2.ScanOptions(
                    session_id=session_id,
                    summary_only=True,
                ),
                last=True,
            )
            response = stub.GenerateSbom(iter([chunk]), timeout=120)
            # Summary-only always renders verbose table
            _render_summary(response, verbose=max(verbose, 1), file=sys.stdout)
            return

        # Full SBOM path
        chunks = list(
            yield_scan_chunks(
                args.target,
                project_root_name="project",
                session_id=session_id,
            )
        )

        # Set refresh flag on first chunk if requested
        if getattr(args, "refresh", False) and chunks:
            chunks[0].options.refresh = True

        response = stub.GenerateSbom(iter(chunks), timeout=120)

        # Render summary
        summary_file = sys.stdout if args.output else sys.stderr
        _render_summary(response, verbose=verbose, file=summary_file)

        # Write JSON output
        if args.output:
            Path(args.output).write_bytes(response.sbom_json)
        else:
            sys.stdout.buffer.write(response.sbom_json)
            sys.stdout.buffer.write(b"\n")

    except grpc.RpcError as e:
        sys.stderr.write(f"Engine error: {e.details()}\n")
        sys.exit(1)
    except FileNotFoundError as e:
        sys.stderr.write(f"{e}\n")
        sys.exit(1)
    finally:
        if channel is not None:
            channel.close()
