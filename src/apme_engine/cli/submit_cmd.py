"""``apme submit`` — thin shim over Gateway POST /operation/submit."""

from __future__ import annotations

import argparse
import json
import sys

import httpx

from apme_engine.cli.gateway_client import GatewayClient
from apme_engine.submit_token import generate_submit_token, is_valid_submit_token


def _replay_hint(token: str) -> str:
    """Build the retry instruction shown when a submit's outcome is unknown.

    Args:
        token: Idempotency key used for this submit.

    Returns:
        One-line hint pointing at ``--idempotency-key``.
    """
    return f"Re-run with --idempotency-key {token} to replay the same submit instead of pushing again."


# Statuses where the Gateway may already have pushed a commit or opened a PR
# without returning a result: a client-side timeout or a transport error, an
# SCM provider timeout (504), a provider error after a partial write (502), or
# a publish it could not record (500). The key is the only way back to the same
# operation, so it is reported instead of promising a clean retry.
_AMBIGUOUS_SUBMIT_STATUSES = frozenset({500, 502, 504})


def run_submit(args: argparse.Namespace) -> None:
    """Push remediated patches to a branch and optionally open a PR.

    The idempotency key is resolved *before* the request so it can be reported
    on failure: after a timeout the Gateway may already have pushed the
    branch, and only reusing the same key turns the retry into a replay
    (same operation returned) instead of a second push.

    Args:
        args: Parsed CLI arguments with ``project_id``, ``branch``,
            ``no_pr``, ``activity_id``, ``idempotency_key``, and
            ``gateway_url``.
    """
    create_pr = not args.no_pr
    client = GatewayClient(base_url=args.gateway_url)
    # Resolve the key exactly like the Gateway validates it: an explicit empty
    # or over-limit value is rejected instead of silently replaced by a fresh
    # key (which would push a second branch), and a key is only generated when
    # the caller passed none. ``--no-idempotency-key`` opts out deliberately so
    # a tokenless follow-up can finish a branch-only publish.
    tokenless = bool(getattr(args, "no_idempotency_key", False))
    raw_key = getattr(args, "idempotency_key", None)
    idempotency_key: str | None
    if tokenless:
        if raw_key is not None:
            print(
                "Error: --no-idempotency-key cannot be combined with --idempotency-key.",
                file=sys.stderr,
            )
            sys.exit(1)
        idempotency_key = None
    elif raw_key is None:
        idempotency_key = generate_submit_token()
    else:
        idempotency_key = raw_key.strip()
        if not is_valid_submit_token(idempotency_key):
            print(
                f"Error: --idempotency-key {raw_key!r} is not usable. The Gateway requires "
                "32-128 hex characters (dashes allowed), e.g. `python -c 'import uuid; print(uuid.uuid4().hex)'`.",
                file=sys.stderr,
            )
            sys.exit(1)
    try:
        result = client.submit_operation(
            args.project_id,
            branch_name=getattr(args, "branch", None),
            create_pr=create_pr,
            activity_id=getattr(args, "activity_id", None),
            idempotency_key=idempotency_key,
            tokenless=tokenless,
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
        if idempotency_key is not None and (
            exc.response.status_code in {409, 503} or exc.response.status_code in _AMBIGUOUS_SUBMIT_STATUSES
        ):
            print(_replay_hint(idempotency_key), file=sys.stderr)
        sys.exit(1)
    except httpx.TimeoutException:
        print(
            "Error: Gateway submit timed out. The operation may still have completed; "
            "check the activity or target branch before retrying.",
            file=sys.stderr,
        )
        if idempotency_key is not None:
            print(_replay_hint(idempotency_key), file=sys.stderr)
        sys.exit(1)
    except httpx.RequestError:
        print(
            f"Error: could not connect to Gateway at {client.base_url} — is it running?",
            file=sys.stderr,
        )
        # A transport error can surface after the request reached the Gateway,
        # so the key is the only way back to the same operation.
        if idempotency_key is not None:
            print(_replay_hint(idempotency_key), file=sys.stderr)
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
