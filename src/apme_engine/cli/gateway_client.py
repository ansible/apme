"""Thin REST client for the APME Gateway.

First CLI→Gateway REST client, establishing the pattern from ADR-024:
read-heavy operations on persisted data go through the Gateway REST API.
"""

from __future__ import annotations

import os

import httpx

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
    ) -> dict[str, object]:
        """Submit remediated patches via Gateway ``POST /api/v1/projects/{project_id}/operation/submit``.

        Thin shim — the Gateway owns SCM branch/PR creation (ADR-056).

        Args:
            project_id: Project UUID.
            branch_name: Explicit branch name (None for auto-generated).
            create_pr: Whether to open a PR after pushing.
            activity_id: Optional historical activity ID (None for live op).

        Returns:
            Parsed submit response (branch_name, commit_sha, pr_url).
        """
        payload: dict[str, object] = {"create_pr": create_pr}
        if branch_name:
            payload["branch_name"] = branch_name
        if activity_id:
            payload["activity_id"] = activity_id
        resp = httpx.post(
            f"{self.base_url}/api/v1/projects/{project_id}/operation/submit",
            json=payload,
            timeout=120,
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
