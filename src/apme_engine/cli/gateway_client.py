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
