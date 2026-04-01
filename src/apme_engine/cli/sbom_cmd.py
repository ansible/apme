"""``apme sbom`` — retrieve SBOM from Gateway REST API."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

from apme_engine.cli.gateway_client import GatewayClient


def run_sbom(args: argparse.Namespace) -> None:
    """Retrieve and display SBOM for a project.

    Args:
        args: Parsed CLI arguments with ``project_id``, ``format``,
            ``output``, and ``gateway_url`` attributes.
    """
    client = GatewayClient(base_url=args.gateway_url)
    try:
        bom = client.get_sbom(args.project_id, format=args.format)
    except httpx.HTTPStatusError as exc:
        print(
            f"Error: {exc.response.status_code} — {exc.response.text}",
            file=sys.stderr,
        )
        sys.exit(1)
    except httpx.RequestError:
        print(
            f"Error: could not connect to Gateway at {client.base_url} — is it running?",
            file=sys.stderr,
        )
        sys.exit(1)

    payload = json.dumps(bom, indent=2)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
        print(f"SBOM written to {args.output}", file=sys.stderr)
    else:
        print(payload)
