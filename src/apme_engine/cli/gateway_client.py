"""Thin REST client for the APME Gateway.

First CLI→Gateway REST client, establishing the pattern from ADR-024:
read-heavy operations on persisted data go through the Gateway REST API.
"""

from __future__ import annotations

import os
import uuid

import httpx

from apme_engine.submit_token import is_valid_submit_token

_DEFAULT_GATEWAY_URL = "http://localhost:8080"


class GatewayClient:
    """Minimal wrapper around httpx for Gateway REST calls."""

    def __init__(self, base_url: str | None = None) -> None:
        """Initialise the client.

        Args:
            base_url: Gateway base URL.  Falls back to ``$APME_GATEWAY_URL``
                then ``http://localhost:8080``.
        """
        self.base_url = base_url or os.environ.get("APME_GATEWAY_URL") or _DEFAULT_GATEWAY_URL

    def get_sbom(
        self,
        project_id: str,
        format: str = "cyclonedx",
    ) -> dict[str, object]:
        """Fetch an SBOM for *project_id* from the Gateway.

        Args:
            project_id: Project UUID or display name.
            format: SBOM output format (default ``cyclonedx``).

        Returns:
            Parsed CycloneDX JSON payload.
        """
        resp = httpx.get(
            f"{self.base_url}/api/v1/projects/{project_id}/sbom",
            params={"format": format},
            timeout=30,
        )
        resp.raise_for_status()
        return dict(resp.json())

    def submit_operation(
        self,
        project_id: str,
        *,
        branch_name: str | None = None,
        create_pr: bool = True,
        activity_id: str | None = None,
        idempotency_key: str | None = None,
        tokenless: bool = False,
    ) -> dict[str, object]:
        """Submit remediated patches via Gateway ``POST /api/v1/projects/{project_id}/operation/submit``.

        Thin shim — the Gateway owns SCM branch/PR creation (ADR-056).

        The ``Idempotency-Key`` header is what makes the call replayable: the
        Gateway persists the token with the resulting operation and returns
        the same operation for a repeat with the same key. A caller that
        wants retry-after-timeout semantics must pass the *same*
        ``idempotency_key`` again, so the key is a caller-visible value rather
        than an implementation detail.

        Args:
            project_id: Project UUID.
            branch_name: Explicit branch name (None for auto-generated).
            create_pr: Whether to open a PR after pushing.
            activity_id: Optional historical activity ID (None for live op).
            idempotency_key: Replay token (>= 32 hex chars). Defaults to a
                fresh ``uuid4().hex``.
            tokenless: Send no ``Idempotency-Key`` header at all. The Gateway
                accepts a tokenless submit for a branch-only publish on an
                unclaimed scan; it cannot be replayed, so a retry after an
                ambiguous failure may push twice. Honoured over
                ``idempotency_key``.

        Returns:
            Parsed submit response (branch_name, commit_sha, pr_url).

        Raises:
            ValueError: If ``idempotency_key`` is too weak for the Gateway to
                accept (under 32 hex chars, or over 128 chars), or if it is
                supplied together with ``tokenless``.
        """
        payload: dict[str, object] = {"create_pr": create_pr}
        if branch_name:
            payload["branch_name"] = branch_name
        if activity_id:
            payload["activity_id"] = activity_id
        headers: dict[str, str] = {}
        if tokenless:
            if idempotency_key is not None:
                msg = "tokenless submit cannot carry an idempotency_key"
                raise ValueError(msg)
        else:
            token = (idempotency_key or uuid.uuid4().hex).strip()
            if not is_valid_submit_token(token):
                msg = (
                    f"Invalid idempotency key {token!r}: the Gateway requires 32-128 "
                    "hex characters (dashes allowed), e.g. `python -c 'import uuid; print(uuid.uuid4().hex)'`"
                )
                raise ValueError(msg)
            headers["Idempotency-Key"] = token
        resp = httpx.post(
            f"{self.base_url}/api/v1/projects/{project_id}/operation/submit",
            json=payload,
            headers=headers,
            # Submit can wait for a 30s lock and up to three 120s SCM calls.
            timeout=420,
        )
        resp.raise_for_status()
        data = resp.json()
        return dict(data) if isinstance(data, dict) else {"result": data}

    def import_scan(
        self,
        payload: dict[str, object],
    ) -> dict[str, object]:
        """Store external check JSON via ``POST /api/v1/scans/import``.

        Args:
            payload: Import payload (project_id, project_path, violations).

        Returns:
            Parsed import response (scan_id, session_id).
        """
        resp = httpx.post(
            f"{self.base_url}/api/v1/scans/import",
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return dict(data) if isinstance(data, dict) else {"result": data}
