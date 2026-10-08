"""``apme submit`` — thin shim over Gateway POST /operation/submit."""

from __future__ import annotations

import argparse
import json
import sys

import httpx

from apme_engine.cli.gateway_client import GatewayClient


def run_submit(args: argparse.Namespace) -> None:
    """Push remediated patches to a branch and optionally open a PR.

    Args:
        args: Parsed CLI arguments with ``project_id``, ``branch``,
            ``no_pr``, ``activity_id``, and ``gateway_url``.
    """
    create_pr = not args.no_pr
    client = GatewayClient(base_url=args.gateway_url)
    try:
        result = client.submit_operation(
            args.project_id,
            branch_name=getattr(args, "branch", None),
            create_pr=create_pr,
            activity_id=getattr(args, "activity_id", None),
        )
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text
        try:
            parsed = json.loads(detail) if detail else None
        except (ValueError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("detail"), dict):
            coded = parsed["detail"]
            detail = str(coded.get("message") or coded.get("code") or detail)
        print(f"Error: {exc.response.status_code} — {detail}", file=sys.stderr)
        sys.exit(1)
    except httpx.RequestError:
        print(
            f"Error: could not connect to Gateway at {client.base_url} — is it running?",
            file=sys.stderr,
        )
        sys.exit(1)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"Error: invalid Gateway response — {exc}", file=sys.stderr)
        sys.exit(1)
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
    else:
        branch = result.get("branch_name", "")
        sha = result.get("commit_sha", "")
        pr_url = result.get("pr_url") or ""
        print(f"Branch: {branch} ({sha})")
        if pr_url:
            print(f"PR: {pr_url}")
        elif not create_pr:
            print("Pushed without opening a PR (--no-pr).")
