"""Cache subcommand: manage collection cache via Primary proxy RPCs."""

from __future__ import annotations

import argparse
import sys

import grpc

from apme.v1 import cache_pb2, primary_pb2_grpc
from apme_engine.cli.discovery import resolve_primary


def run_cache(args: argparse.Namespace) -> None:
    channel, _ = resolve_primary(args)
    stub = primary_pb2_grpc.PrimaryStub(channel)  # type: ignore[no-untyped-call]

    try:
        if args.cache_command == "pull-galaxy":
            _pull_galaxy(stub, args)
        elif args.cache_command == "pull-requirements":
            _pull_requirements(stub, args)
        elif args.cache_command == "clone-org":
            _clone_org(stub, args)
    except grpc.RpcError as e:
        sys.stderr.write(f"Cache operation failed: {e.details()}\n")
        sys.exit(1)
    finally:
        channel.close()


def _pull_galaxy(stub: primary_pb2_grpc.PrimaryStub, args: argparse.Namespace) -> None:
    resp = stub.PullGalaxy(
        cache_pb2.PullGalaxyRequest(
            spec=args.spec,
            galaxy_server=getattr(args, "galaxy_server", None) or "",
        ),
        timeout=120,
    )
    if resp.success:
        sys.stderr.write(f"Installed: {args.spec} -> {resp.path}\n")
    else:
        sys.stderr.write(f"Failed: {resp.error_message}\n")
        sys.exit(1)


def _pull_requirements(stub: primary_pb2_grpc.PrimaryStub, args: argparse.Namespace) -> None:
    resp = stub.PullRequirements(
        cache_pb2.PullRequirementsRequest(
            requirements_path=args.requirements_path,
            galaxy_server=getattr(args, "galaxy_server", None) or "",
        ),
        timeout=120,
    )
    if resp.success:
        for p in resp.paths:
            sys.stderr.write(f"Installed: {p}\n")
    else:
        sys.stderr.write(f"Failed: {resp.error_message}\n")
        sys.exit(1)


def _clone_org(stub: primary_pb2_grpc.PrimaryStub, args: argparse.Namespace) -> None:
    resp = stub.CloneOrg(
        cache_pb2.CloneOrgRequest(
            org=args.org,
            repos=getattr(args, "repos", None) or [],
            depth=getattr(args, "depth", 1),
            token=getattr(args, "token", None) or "",
        ),
        timeout=300,
    )
    if resp.success:
        for p in resp.paths:
            sys.stderr.write(f"Cloned: {p}\n")
    else:
        sys.stderr.write(f"Failed: {resp.error_message}\n")
        sys.exit(1)
