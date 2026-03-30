"""Async client for the Ansible Galaxy V3 REST API.

.. deprecated:: 0.2.0
    This module is superseded by :mod:`galaxy_proxy.collection_downloader`
    (ADR-045).  Galaxy authentication and tarball downloading are now
    delegated to ``ansible-galaxy collection download``.  This module is
    retained for backward compatibility and will be removed in a future
    release.

Supports multiple upstream Galaxy servers (public Galaxy, Automation Hub,
private instances) with per-server auth tokens.  Servers are tried in order;
the first successful response wins.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

DEFAULT_GALAXY_URL = "https://galaxy.ansible.com"

COLLECTIONS_PATH = "/api/v3/plugin/ansible/content/published/collections/index"

logger = logging.getLogger(__name__)


@dataclass
class GalaxyServer:
    """A single upstream Galaxy / Automation Hub endpoint.

    Attributes:
        url: Base URL of the Galaxy or Automation Hub API.
        token: Optional API token for Authorization, if required.
        name: Optional short name for logging and display.
    """

    url: str
    token: str | None = None
    name: str | None = None

    def label(self) -> str:
        """Return a human-readable label (name if set, otherwise URL).

        Returns:
            The configured name, or the server URL if name is unset.
        """
        return self.name or self.url


@dataclass
class CollectionVersion:
    """Metadata for a single published collection version.

    Attributes:
        namespace: Collection namespace.
        name: Collection name.
        version: Semantic version string.
        download_url: Absolute URL to the collection artifact tarball.
        dependencies: Other collections and version constraints required by this version.
        requires_ansible: Declared Ansible version requirement, if any.
        license: SPDX or other license strings from metadata.
        authors: Author strings from metadata.
        description: Human-readable description from metadata.
    """

    namespace: str
    name: str
    version: str
    download_url: str
    dependencies: dict[str, str] = field(default_factory=dict)
    requires_ansible: str | None = None
    license: list[str] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)
    description: str = ""


class GalaxyClient:
    """Async client for fetching collections from one or more Galaxy servers.

    When multiple servers are configured, each operation tries them in order
    and returns the first successful result (like ``ansible.cfg``'s
    ``galaxy_server_list``).
    """

    def __init__(
        self,
        galaxy_url: str = DEFAULT_GALAXY_URL,
        token: str | None = None,
        timeout: float = 30.0,
        *,
        servers: list[GalaxyServer] | None = None,
    ) -> None:
        """Initialise with one or more upstream Galaxy servers.

        Args:
            galaxy_url: Default Galaxy base URL when ``servers`` is not provided.
            token: Default token for the single implicit server from ``galaxy_url``.
            timeout: HTTP timeout in seconds for all clients.
            servers: Explicit list of upstream servers; overrides ``galaxy_url``/``token``.
        """
        self._timeout = timeout
        if servers:
            self._servers = list(servers)
        else:
            self._servers = [GalaxyServer(url=galaxy_url, token=token)]
        self._clients: list[httpx.AsyncClient] = []
        for srv in self._servers:
            headers: dict[str, str] = {"Accept": "application/json"}
            if srv.token:
                headers["Authorization"] = f"Token {srv.token}"
            self._clients.append(
                httpx.AsyncClient(
                    base_url=srv.url.rstrip("/"),
                    headers=headers,
                    timeout=timeout,
                    follow_redirects=True,
                )
            )
        self._download_client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
        )

    @property
    def servers(self) -> list[GalaxyServer]:
        """Return a copy of the configured server list."""
        return list(self._servers)

    async def close(self) -> None:
        """Close all underlying HTTP clients."""
        for c in self._clients:
            await c.aclose()
        await self._download_client.aclose()

    async def __aenter__(self) -> GalaxyClient:
        """Enter async context manager.

        Returns:
            This client instance.
        """
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Exit async context manager and close clients.

        Args:
            *exc: Exception info from the context manager protocol (type, value, traceback).
        """
        await self.close()

    async def list_versions(self, namespace: str, name: str) -> list[str]:
        """Fetch all published version strings for a collection.

        Tries each configured server in order; returns versions from the
        first server that responds successfully.

        Args:
            namespace: Collection namespace.
            name: Collection name.

        Returns:
            List of version strings from the first successful upstream.

        Raises:
            httpx.HTTPStatusError: When the last attempted server returns an error status.
            httpx.RequestError: When the last attempted server's request fails.
            RuntimeError: When no Galaxy servers are configured.
        """  # noqa: DOC503
        last_exc: Exception | None = None
        for srv, client in zip(self._servers, self._clients, strict=True):
            try:
                versions = await self._list_versions_from(client, namespace, name)
                logger.debug(
                    "list_versions %s.%s: %d version(s) from %s",
                    namespace,
                    name,
                    len(versions),
                    srv.label(),
                )
                return versions
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                logger.debug(
                    "list_versions %s.%s: %s failed: %s",
                    namespace,
                    name,
                    srv.label(),
                    exc,
                )
                last_exc = exc
        raise last_exc or RuntimeError("No Galaxy servers configured")

    async def get_version_detail(
        self,
        namespace: str,
        name: str,
        version: str,
    ) -> CollectionVersion:
        """Fetch full metadata for a specific collection version.

        Tries each server in order.

        Args:
            namespace: Collection namespace.
            name: Collection name.
            version: Collection version string.

        Returns:
            Parsed ``CollectionVersion`` from the first successful upstream.

        Raises:
            httpx.HTTPStatusError: When the last attempted server returns an error status.
            httpx.RequestError: When the last attempted server's request fails.
            RuntimeError: When no Galaxy servers are configured.
        """  # noqa: DOC503
        last_exc: Exception | None = None
        for srv, client in zip(self._servers, self._clients, strict=True):
            try:
                detail = await self._get_detail_from(client, namespace, name, version)
                logger.debug(
                    "get_version_detail %s.%s:%s from %s",
                    namespace,
                    name,
                    version,
                    srv.label(),
                )
                return detail
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                logger.debug(
                    "get_version_detail %s.%s:%s: %s failed: %s",
                    namespace,
                    name,
                    version,
                    srv.label(),
                    exc,
                )
                last_exc = exc
        raise last_exc or RuntimeError("No Galaxy servers configured")

    async def download_tarball(self, download_url: str) -> bytes:
        """Download a collection tarball by its absolute URL.

        Uses a dedicated client without a base_url so it can follow the
        download URL returned by any upstream server.

        Args:
            download_url: Full URL to the tarball resource.

        Returns:
            Raw tarball bytes.
        """
        resp = await self._download_client.get(download_url)
        resp.raise_for_status()
        return resp.content  # type: ignore[no-any-return]

    async def get_version_and_download(
        self,
        namespace: str,
        name: str,
        version: str,
    ) -> tuple[CollectionVersion, bytes]:
        """Fetch version metadata and download the tarball in sequence.

        Args:
            namespace: Collection namespace.
            name: Collection name.
            version: Collection version string.

        Returns:
            Tuple of version metadata and tarball bytes.
        """
        detail = await self.get_version_detail(namespace, name, version)
        tarball = await self.download_tarball(detail.download_url)
        return detail, tarball

    # ── internal per-client helpers ──────────────────────────────────

    @staticmethod
    async def _list_versions_from(
        client: httpx.AsyncClient,
        namespace: str,
        name: str,
    ) -> list[str]:
        versions: list[str] = []
        url = f"{COLLECTIONS_PATH}/{namespace}/{name}/versions/"
        params: dict[str, str | int] = {"limit": 100, "offset": 0}
        while True:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json()
            for entry in payload.get("data", []):
                versions.append(entry["version"])
            if not payload.get("links", {}).get("next"):
                break
            params["offset"] = int(params["offset"]) + int(params["limit"])
        return versions

    @staticmethod
    async def _get_detail_from(
        client: httpx.AsyncClient,
        namespace: str,
        name: str,
        version: str,
    ) -> CollectionVersion:
        url = f"{COLLECTIONS_PATH}/{namespace}/{name}/versions/{version}/"
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
        meta = data.get("metadata", {})
        return CollectionVersion(
            namespace=namespace,
            name=name,
            version=version,
            download_url=data["download_url"],
            dependencies=meta.get("dependencies", {}),
            requires_ansible=data.get("requires_ansible"),
            license=meta.get("license", []),
            authors=meta.get("authors", []),
            description=meta.get("description", ""),
        )
