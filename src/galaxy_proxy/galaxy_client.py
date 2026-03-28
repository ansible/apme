"""Async client for the Ansible Galaxy V3 REST API.

Supports multiple upstream Galaxy servers (public Galaxy, Automation Hub,
console.redhat.com) with per-server auth tokens.  Servers are tried in
order; the first successful response wins.

Auth modes:
  - ``token`` (default): ``Authorization: Token <tok>`` — standard Galaxy.
  - ``bearer``: ``Authorization: Bearer <tok>`` — direct bearer.
  - ``sso``: Offline-token exchange via ``auth_url`` (Red Hat SSO / Keycloak).
    The configured ``token`` is treated as a refresh token and exchanged for a
    short-lived access token using ``grant_type=refresh_token``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

DEFAULT_GALAXY_URL = "https://galaxy.ansible.com"

# Relative to the server API root (no leading slash). The base_url on each
# httpx client already points at the API root — e.g.
#   galaxy.ansible.com/api/   or   console.redhat.com/api/automation-hub/
COLLECTIONS_PATH = "v3/plugin/ansible/content/published/collections/index"

REDHAT_SSO_URL = "https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token"
SSO_CLIENT_ID = "cloud-services"
SSO_TOKEN_EXPIRY_MARGIN = 30  # refresh this many seconds before expiry

logger = logging.getLogger(__name__)


def _normalize_api_root(url: str) -> str:
    """Ensure a Galaxy server URL points at the V3 API root.

    Bare Galaxy URLs (e.g. ``https://galaxy.ansible.com``) need ``/api/``
    appended so that relative V3 paths resolve correctly.  Automation Hub
    URLs already include the API prefix (``/api/automation-hub/``) and are
    returned as-is with a guaranteed trailing slash.

    Args:
        url: Galaxy server base URL.

    Returns:
        Normalized URL ending with a trailing slash, pointing at the API root.
    """
    url = url.rstrip("/")
    path = urlparse(url).path
    if not path or path == "/":
        url += "/api/"
    else:
        url += "/"
    return url


@dataclass
class GalaxyServer:
    """A single upstream Galaxy / Automation Hub endpoint.

    Attributes:
        url: Base URL of the Galaxy or Automation Hub API.
        token: API token, offline/refresh token (for SSO), or None.
        name: Optional short name for logging and display.
        auth_url: SSO/OIDC token endpoint for offline-token exchange.
            When set, ``token`` is treated as a refresh token.
        auth_type: ``"token"`` (default), ``"bearer"``, or ``"sso"``.
    """

    url: str
    token: str | None = None
    name: str | None = None
    auth_url: str | None = None
    auth_type: str = "token"

    def label(self) -> str:
        """Return a human-readable label (name if set, otherwise URL).

        Returns:
            The configured name, or the server URL if name is unset.
        """
        return self.name or self.url


@dataclass
class CollectionInfo:
    """Summary info for a collection (from list endpoint).

    Attributes:
        namespace: Collection namespace.
        name: Collection name.
        description: Short description.
        latest_version: The highest semver version string.
        deprecated: Whether the collection is deprecated.
    """

    namespace: str
    name: str
    description: str = ""
    latest_version: str = ""
    deprecated: bool = False


@dataclass
class CollectionVersion:
    """Full metadata for a specific collection version.

    Attributes:
        namespace: Collection namespace.
        name: Collection name.
        version: Semantic version string.
        download_url: Direct URL to the tarball.
        dependencies: Mapping of FQCN -> version specifier.
        description: Collection description.
    """

    namespace: str
    name: str
    version: str
    download_url: str
    dependencies: dict[str, str] = field(default_factory=dict)
    description: str = ""


class _SSOState:
    """Cached access token obtained from an SSO/OIDC token exchange."""

    __slots__ = ("access_token", "expires_at")

    def __init__(self) -> None:
        self.access_token: str | None = None
        self.expires_at: float = 0.0

    def is_valid(self) -> bool:
        return self.access_token is not None and time.monotonic() < self.expires_at


class GalaxyClient:
    """Async client for fetching collections from one or more Galaxy servers.

    By default, queries only ``https://galaxy.ansible.com``.  Supply
    ``servers`` to add Automation Hub or other Galaxy-compatible registries.
    Servers are tried **in order** — the first successful response wins.

    Example:
        >>> async with GalaxyClient(servers=[
        ...     GalaxyServer(url="https://hub.example.com", token="secret"),
        ...     GalaxyServer(url="https://galaxy.ansible.com"),
        ... ]) as client:
        ...     versions = await client.list_versions("ansible", "netcommon")
    """

    def __init__(
        self,
        *,
        galaxy_url: str = DEFAULT_GALAXY_URL,
        token: str | None = None,
        servers: list[GalaxyServer] | None = None,
        timeout: float = 30.0,
    ) -> None:
        """Create a new Galaxy client.

        Args:
            galaxy_url: Fallback Galaxy URL when ``servers`` is empty.
            token: Fallback token for the ``galaxy_url`` server.
            servers: Ordered list of Galaxy servers to query.
            timeout: HTTP timeout for all requests.
        """
        if servers:
            self._servers = list(servers)
        else:
            self._servers = [GalaxyServer(url=galaxy_url, token=token)]

        self._clients: list[httpx.AsyncClient] = []
        self._sso_states: list[_SSOState | None] = []
        self._sso_locks: list[asyncio.Lock | None] = []
        self._sso_client: httpx.AsyncClient | None = None

        for srv in self._servers:
            base = _normalize_api_root(srv.url)
            headers: dict[str, str] = {"Accept": "application/json"}

            if srv.auth_type == "sso":
                self._sso_states.append(_SSOState())
                self._sso_locks.append(asyncio.Lock())
                if self._sso_client is None:
                    self._sso_client = httpx.AsyncClient(timeout=timeout)
            else:
                self._sso_states.append(None)
                self._sso_locks.append(None)
                if srv.token:
                    prefix = "Bearer" if srv.auth_type == "bearer" else "Token"
                    headers["Authorization"] = f"{prefix} {srv.token}"

            self._clients.append(
                httpx.AsyncClient(
                    base_url=base,
                    headers=headers,
                    timeout=timeout,
                    follow_redirects=True,
                )
            )

        self._download_client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
        )

    async def close(self) -> None:
        """Close all underlying HTTP clients."""
        for c in self._clients:
            await c.aclose()
        await self._download_client.aclose()
        if self._sso_client:
            await self._sso_client.aclose()

    async def _ensure_sso_token(self, idx: int) -> None:
        """Exchange a refresh/offline token for a short-lived access token.

        Updates the Authorization header on ``self._clients[idx]`` in-place.

        Args:
            idx: Index into ``self._servers`` / ``self._clients``.

        Raises:
            ValueError: If the server uses SSO auth but has no token configured.
            RuntimeError: If the SSO client is not initialized.
        """
        sso = self._sso_states[idx]
        if sso is None or sso.is_valid():
            return

        lock = self._sso_locks[idx]
        if lock is None:
            return

        async with lock:
            if sso.is_valid():
                return

            srv = self._servers[idx]
            if not srv.token:
                raise ValueError(f"Server {srv.label()} uses auth_type=sso but has no token configured")

            auth_url = srv.auth_url or REDHAT_SSO_URL

            if self._sso_client is None:
                raise RuntimeError(
                    "SSO client is not initialized; cannot refresh SSO token. "
                    "Ensure the GalaxyClient is constructed with SSO support."
                )
            resp = await self._sso_client.post(
                auth_url,
                data={
                    "grant_type": "refresh_token",
                    "client_id": SSO_CLIENT_ID,
                    "refresh_token": srv.token,
                },
            )
            resp.raise_for_status()
            payload = resp.json()
            sso.access_token = payload["access_token"]
            expires_in = int(payload.get("expires_in", 300))
            # Clamp expiry to avoid refresh spinning if expires_in < margin
            sso.expires_at = max(
                time.monotonic() + 5,
                time.monotonic() + expires_in - SSO_TOKEN_EXPIRY_MARGIN,
            )
            self._clients[idx].headers["Authorization"] = f"Bearer {sso.access_token}"
            logger.debug("SSO token refreshed for %s (expires in %ds)", srv.label(), expires_in)

    async def __aenter__(self) -> GalaxyClient:
        """Enter async context manager.

        Returns:
            Self for use in ``async with`` blocks.
        """
        return self

    async def __aexit__(  # noqa: DOC101, DOC103
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        """Exit async context manager, closing clients.

        Args:
            exc_type: Exception type if an exception was raised.
            exc_val: Exception value if an exception was raised.
            exc_tb: Traceback if an exception was raised.
        """
        await self.close()

    async def list_versions(
        self,
        namespace: str,
        name: str,
    ) -> list[str]:
        """List all available versions of a collection.

        Tries each server in order; returns versions from the first server
        that returns a non-empty list.

        Args:
            namespace: Collection namespace.
            name: Collection name.

        Returns:
            List of version strings.

        Raises:
            httpx.HTTPStatusError: When the last attempted server returns an error status.
            httpx.RequestError: When the last attempted server's request fails.
            RuntimeError: When no Galaxy servers are configured.
        """  # noqa: DOC503
        last_exc: Exception | None = None
        for idx, (srv, client) in enumerate(zip(self._servers, self._clients, strict=True)):
            try:
                await self._ensure_sso_token(idx)
                versions = await self._list_versions_from(client, namespace, name)
                if not versions:
                    logger.debug(
                        "list_versions %s.%s: 0 version(s) from %s — trying next",
                        namespace,
                        name,
                        srv.label(),
                    )
                    continue
                logger.debug(
                    "list_versions %s.%s: %d version(s) from %s",
                    namespace,
                    name,
                    len(versions),
                    srv.label(),
                )
                return versions
            except (httpx.HTTPStatusError, httpx.RequestError, ValueError, RuntimeError) as exc:
                logger.debug(
                    "list_versions %s.%s: %s failed: %s",
                    namespace,
                    name,
                    srv.label(),
                    exc,
                )
                last_exc = exc
        if last_exc:
            raise last_exc
        return []

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
        """  # noqa: DOC502, DOC503
        detail, _ = await self._get_version_detail_with_idx(namespace, name, version)
        return detail

    async def _get_version_detail_with_idx(
        self,
        namespace: str,
        name: str,
        version: str,
    ) -> tuple[CollectionVersion, int]:
        """Fetch full metadata for a specific collection version with server index.

        Args:
            namespace: Collection namespace.
            name: Collection name.
            version: Collection version string.

        Returns:
            Tuple of parsed ``CollectionVersion`` and the server index.

        Raises:
            httpx.HTTPStatusError: When the last attempted server returns an error status.
            httpx.RequestError: When the last attempted server's request fails.
            RuntimeError: When no Galaxy servers are configured.
        """  # noqa: DOC503
        last_exc: Exception | None = None
        for idx, (srv, client) in enumerate(zip(self._servers, self._clients, strict=True)):
            try:
                await self._ensure_sso_token(idx)
                detail = await self._get_detail_from(client, namespace, name, version)
                logger.debug(
                    "get_version_detail %s.%s:%s from %s",
                    namespace,
                    name,
                    version,
                    srv.label(),
                )
                return detail, idx
            except (httpx.HTTPStatusError, httpx.RequestError, ValueError, RuntimeError) as exc:
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

    async def download_tarball(self, download_url: str, *, _server_idx: int | None = None) -> bytes:
        """Download a collection tarball by its absolute URL.

        Args:
            download_url: Full URL to the tarball resource.
            _server_idx: Internal — index of the server that provided the URL.
                When set, the server's auth headers are forwarded to the
                download request (required by Automation Hub).

        Returns:
            Raw tarball bytes.
        """
        headers: dict[str, str] = {}
        if _server_idx is not None:
            auth = self._clients[_server_idx].headers.get("Authorization")
            if auth:
                headers["Authorization"] = auth
        resp = await self._download_client.get(download_url, headers=headers)
        resp.raise_for_status()
        return resp.content  # type: ignore[no-any-return]

    async def fetch_collection(
        self,
        namespace: str,
        name: str,
        version: str,
    ) -> tuple[CollectionVersion, bytes]:
        """Fetch version metadata and download the tarball.

        Convenience method that calls ``get_version_detail`` then
        ``download_tarball``.

        Args:
            namespace: Collection namespace.
            name: Collection name.
            version: Collection version string.

        Returns:
            Tuple of version metadata and tarball bytes.
        """
        detail, server_idx = await self._get_version_detail_with_idx(namespace, name, version)
        tarball = await self.download_tarball(detail.download_url, _server_idx=server_idx)
        return detail, tarball

    # ── internal per-client helpers ──────────────────────────────────

    async def _list_versions_from(
        self,
        client: httpx.AsyncClient,
        namespace: str,
        name: str,
    ) -> list[str]:
        """List versions from a single upstream server.

        Args:
            client: The httpx client for this server.
            namespace: Collection namespace.
            name: Collection name.

        Returns:
            List of version strings (sorted descending if API provides order).

        Raises:
            httpx.HTTPStatusError: On non-2xx response.
            httpx.RequestError: On connection error.
        """  # noqa: DOC502
        url = f"{COLLECTIONS_PATH}/{namespace}/{name}/versions/"
        versions: list[str] = []
        params: dict[str, int] | None = {"limit": 100}
        while True:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("data", []):
                v = item.get("version")
                if v:
                    versions.append(v)
            links = data.get("links") or {}
            next_url = links.get("next")
            if not next_url:
                break
            url = next_url
            params = None
        return versions

    async def _get_detail_from(
        self,
        client: httpx.AsyncClient,
        namespace: str,
        name: str,
        version: str,
    ) -> CollectionVersion:
        """Fetch version detail from a single upstream server.

        Args:
            client: The httpx client for this server.
            namespace: Collection namespace.
            name: Collection name.
            version: Collection version string.

        Returns:
            Parsed ``CollectionVersion``.

        Raises:
            httpx.HTTPStatusError: On non-2xx response.
            httpx.RequestError: On connection error.
            ValueError: If the response is missing download_url.
        """  # noqa: DOC502, DOC503
        url = f"{COLLECTIONS_PATH}/{namespace}/{name}/versions/{version}/"
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
        download_url = data.get("download_url")
        if not download_url:
            raise ValueError(f"Collection {namespace}.{name}:{version} response missing download_url")
        return CollectionVersion(
            namespace=namespace,
            name=name,
            version=version,
            download_url=download_url,
            dependencies=data.get("metadata", {}).get("dependencies", {}),
            description=data.get("metadata", {}).get("description", ""),
        )
