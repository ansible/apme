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

from galaxy_proxy import MAX_VERSION_PAGES

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
            RuntimeError: When no Galaxy servers are configured, when every
                server's version listing was truncated at the page bound, or
                when every server failed (the last failure is chained via
                ``__cause__`` so callers never see a bare
                ``ValueError``/``KeyError`` from malformed payloads).
        """  # noqa: DOC503
        last_exc: Exception | None = None
        truncated = False
        for srv, client in zip(self._servers, self._clients, strict=True):
            try:
                versions = await self._list_versions_from(client, namespace, name)
            except (httpx.HTTPStatusError, httpx.RequestError, ValueError, KeyError) as exc:
                logger.debug(
                    "list_versions %s.%s: %s failed: %s",
                    namespace,
                    name,
                    srv.label(),
                    exc,
                )
                last_exc = exc
                continue
            if versions is None:
                # Truncated listing: not a complete answer, fail over.
                logger.debug(
                    "list_versions %s.%s: %s truncated version listing, trying next server",
                    namespace,
                    name,
                    srv.label(),
                )
                truncated = True
                continue
            logger.debug(
                "list_versions %s.%s: %d version(s) from %s",
                namespace,
                name,
                len(versions),
                srv.label(),
            )
            return versions
        if last_exc is not None:
            msg = f"Galaxy version listing failed for {namespace}.{name}: {last_exc}"
            raise RuntimeError(msg) from last_exc
        if truncated:
            msg = f"Galaxy version listing truncated at {MAX_VERSION_PAGES} pages for {namespace}.{name}"
            raise RuntimeError(msg)
        raise RuntimeError("No Galaxy servers configured")

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
            RuntimeError: When no Galaxy servers are configured, or when
                every server failed (the last failure is chained via
                ``__cause__`` so callers never see a bare
                ``httpx.HTTPStatusError``/``httpx.RequestError`` or
                ``ValueError``/``KeyError`` from malformed payloads).
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
            except (httpx.HTTPStatusError, httpx.RequestError, ValueError, KeyError) as exc:
                logger.debug(
                    "get_version_detail %s.%s:%s: %s failed: %s",
                    namespace,
                    name,
                    version,
                    srv.label(),
                    exc,
                )
                last_exc = exc
        if last_exc is not None:
            msg = f"Galaxy version detail failed for {namespace}.{name}:{version}: {last_exc}"
            raise RuntimeError(msg) from last_exc
        raise RuntimeError("No Galaxy servers configured")

    async def download_tarball(self, download_url: str) -> bytes:
        """Download a collection tarball by its absolute URL.

        Uses a dedicated client without a base_url so it can follow the
        download URL returned by any upstream server.

        Args:
            download_url: Full URL to the tarball resource.

        Returns:
            Raw tarball bytes.

        Raises:
            RuntimeError: When the download fails (the ``httpx`` failure
                is chained via ``__cause__`` so callers never see a bare
                transport error), symmetric with :meth:`list_versions`.
        """  # noqa: DOC503
        try:
            resp = await self._download_client.get(download_url)
            resp.raise_for_status()
            return resp.content  # type: ignore[no-any-return]
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            msg = f"Galaxy tarball download failed for {download_url}: {exc}"
            raise RuntimeError(msg) from exc

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

        Raises:
            RuntimeError: When metadata lookup or tarball download fails
                (failures from the underlying calls are already
                ``RuntimeError`` and propagate unchanged; any other
                transport or malformed-payload failure is wrapped with the
                original chained via ``__cause__``), symmetric with
                :meth:`list_versions`.
        """  # noqa: DOC503
        try:
            detail = await self.get_version_detail(namespace, name, version)
            tarball = await self.download_tarball(detail.download_url)
            return detail, tarball
        except (httpx.HTTPStatusError, httpx.RequestError, ValueError, KeyError) as exc:
            msg = f"Galaxy download failed for {namespace}.{name}:{version}: {exc}"
            raise RuntimeError(msg) from exc

    # ── internal per-client helpers ──────────────────────────────────

    @staticmethod
    async def _list_versions_from(
        client: httpx.AsyncClient,
        namespace: str,
        name: str,
    ) -> list[str] | None:
        """List versions, or ``None`` when the listing is truncated.

        A server that keeps returning ``links.next`` past
        ``MAX_VERSION_PAGES`` yields a partial list that must not be
        mistaken for a complete answer, so truncation is a failure signal
        the caller fails over on.

        Args:
            client: Authenticated httpx client for one Galaxy server.
            namespace: Collection namespace.
            name: Collection name.

        Returns:
            Version strings, or ``None`` when truncated at the page bound
            or when a page body is not a JSON object (both are failure
            signals the caller fails over on).
        """
        versions: list[str] = []
        url = f"{COLLECTIONS_PATH}/{namespace}/{name}/versions/"
        params: dict[str, str | int] = {"limit": 100, "offset": 0}
        for _page in range(MAX_VERSION_PAGES):
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json()
            if not isinstance(payload, dict):
                # A non-dict JSON body (e.g. a list or string) has no
                # ``.get`` — treat it as a failure so the caller fails
                # over to the next server instead of raising AttributeError.
                logger.debug(
                    "Galaxy version listing for %s.%s returned non-dict payload (%s); treating as failure",
                    namespace,
                    name,
                    type(payload).__name__,
                )
                return None
            entries = payload.get("data")
            if not isinstance(entries, list):
                logger.debug(
                    "Galaxy version listing for %s.%s returned non-list data; treating as failure",
                    namespace,
                    name,
                )
                return None
            for entry in entries:
                if not isinstance(entry, dict):
                    logger.debug(
                        "Galaxy version listing for %s.%s returned non-object entry; treating as failure",
                        namespace,
                        name,
                    )
                    return None
                versions.append(entry["version"])
            if "links" in payload:
                links = payload["links"]
                if not isinstance(links, dict):
                    logger.debug(
                        "Galaxy version listing for %s.%s returned non-object links; treating as failure",
                        namespace,
                        name,
                    )
                    return None
                if not links.get("next"):
                    break
            else:
                break
            params["offset"] = int(params["offset"]) + int(params["limit"])
        else:
            logger.warning(
                "Galaxy version pagination exceeded %d pages for %s.%s; treating as failure",
                MAX_VERSION_PAGES,
                namespace,
                name,
            )
            return None
        return versions

    @staticmethod
    async def _get_detail_from(
        client: httpx.AsyncClient,
        namespace: str,
        name: str,
        version: str,
    ) -> CollectionVersion:
        """Fetch and parse version metadata from a single Galaxy server.

        Args:
            client: Authenticated httpx client for one Galaxy server.
            namespace: Collection namespace.
            name: Collection name.
            version: Collection version string.

        Returns:
            Parsed ``CollectionVersion``.

        Raises:
            ValueError: When the response body is not a JSON object (a
                non-dict payload has no ``.get`` — surfacing
                ``AttributeError`` would escape the caller's failover
                handler, so this is a ``ValueError`` the caller fails
                over on).
            KeyError: When the payload lacks required fields.
        """  # noqa: DOC503
        url = f"{COLLECTIONS_PATH}/{namespace}/{name}/versions/{version}/"
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            msg = (
                f"Galaxy version detail for {namespace}.{name}:{version} "
                f"returned non-dict payload ({type(data).__name__})"
            )
            raise ValueError(msg)
        raw_meta = data.get("metadata")
        if raw_meta is not None and not isinstance(raw_meta, dict):
            msg = (
                f"Galaxy version detail for {namespace}.{name}:{version} "
                f"returned non-object metadata ({type(raw_meta).__name__})"
            )
            raise ValueError(msg)
        meta = raw_meta or {}
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
