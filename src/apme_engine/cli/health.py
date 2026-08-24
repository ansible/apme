"""Health-check subcommand: probe Engine aggregate health."""

from __future__ import annotations

import argparse
import json
import sys

import grpc

from apme.v1 import engine_pb2_grpc
from apme.v1.common_pb2 import HealthRequest
from apme_engine.cli.discovery import resolve_engine


def run_health_check(args: argparse.Namespace) -> None:
    """Execute the health-check subcommand.

    Args:
        args: Parsed CLI arguments.
    """
    channel, addr = resolve_engine(args)
    stub = engine_pb2_grpc.EngineStub(channel)  # type: ignore[no-untyped-call]

    timeout = getattr(args, "timeout", 5.0)
    try:
        resp = stub.Health(HealthRequest(), timeout=timeout)
    except grpc.RpcError as e:
        if args.json:
            print(json.dumps({"engine": {"status": f"error: {e}", "address": addr}}))
        else:
            sys.stderr.write(f"Engine ({addr}): error - {e}\n")
        sys.exit(1)
    finally:
        channel.close()

    results: dict[str, dict[str, str]] = {
        "engine": {"status": resp.status, "address": addr},
    }
    for svc in resp.downstream:
        results[svc.name] = {"status": svc.status, "address": svc.address}

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for name, info in results.items():
            status = info["status"]
            address = info.get("address", "")
            ok = status == "ok"
            symbol = "\u2714" if ok else "\u2718"
            sys.stdout.write(f"  {symbol} {name:15s} {status:10s} {address}\n")

    all_ok = all(info["status"] == "ok" for info in results.values())
    if not all_ok:
        sys.exit(1)
