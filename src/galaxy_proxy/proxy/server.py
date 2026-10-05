"""PEP 503 Simple Repository API server for Ansible collections.

Serves Python wheels converted from Galaxy collection tarballs.  Tarballs
are obtained via ``ansible-galaxy collection download`` (ADR-045), not a
custom httpx client.
"""

from __future__ import annotations

import asyncio
import configparser
import hmac
import ipaddress
import logging
import os
import re
import socket
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel

from galaxy_proxy import MAX_VERSION_PAGES
from galaxy_proxy.collection_downloader import (
    GalaxyServerConfig,
    download_collections,
)
from galaxy_proxy.converter import tarball_to_wheel
from galaxy_proxy.metadata import sha256_file_hex
from galaxy_proxy.naming import (
    is_collection_package,
    normalize_pep503,
    python_to_fqcn,
    wheel_filename,
)
from galaxy_proxy.proxy.cache import ProxyCache
from galaxy_proxy.proxy.passthrough import PyPIPassthrough

logger = logging.getLogger(__name__)

_ADMIN_TOKEN_ENV = "APME_PROXY_ADMIN_TOKEN"
_ALLOW_UNAUTH_ADMIN_ENV = "APME_PROXY_ALLOW_UNAUTH_ADMIN"
# Must match _PROXY_ADMIN_TOKEN_HEADER in
# apme_gateway/_galaxy_proxy_sync.py — the two services deploy
# independently, so a one-side rename 403s config pushes.
# Admin auth is fail-closed: when no token is configured, admin routes
# reject every request unless the operator explicitly opts out with
# APME_PROXY_ALLOW_UNAUTH_ADMIN=1 (single-host local daemon only).
# Rollout order matters: enable the token on the gateway first, then on the
# proxy. Gateway-first is hitless because an old proxy ignores the extra
# header (open only when it also opts out); proxy-first 403s pushes from
# old gateways while the envs disagree — Gateway logs the failed push and
# reports the proxy component degraded via /health.
_ADMIN_TOKEN_HEADER = "x-apme-proxy-token"

_UNAUTH_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _unauth_admin_allowed() -> bool:
    """Return whether unauthenticated admin access is explicitly allowed.

    Opt-out for the single-host local daemon only — never set this when the
    proxy listens on a routable address. Deployments must configure
    ``APME_PROXY_ADMIN_TOKEN`` instead.

    Returns:
        True when ``APME_PROXY_ALLOW_UNAUTH_ADMIN`` is explicitly truthy.
    """
    return os.environ.get(_ALLOW_UNAUTH_ADMIN_ENV, "").strip().lower() in _UNAUTH_TRUTHY


def _admin_token_configured() -> str | None:
    """Return the configured admin token, or signal unset/invalid.

    Returns:
        Stripped ASCII ``APME_PROXY_ADMIN_TOKEN`` value, ``""`` when unset,
        or ``None`` when set but contains non-ASCII characters (misconfigured).
    """
    token = os.environ.get(_ADMIN_TOKEN_ENV, "").strip()
    if not token:
        return ""
    try:
        token.encode("ascii")
    except UnicodeEncodeError:
        logger.error(
            "%s contains non-ASCII characters; admin routes reject all requests",
            _ADMIN_TOKEN_ENV,
        )
        return None
    return token


def _require_admin_token(request: Request) -> None:
    """Enforce shared-admin-token auth on admin endpoints.

    Fail-closed: when ``APME_PROXY_ADMIN_TOKEN`` is unset the admin surface
    rejects every request unless the operator explicitly opted out with
    ``APME_PROXY_ALLOW_UNAUTH_ADMIN=1`` (single-host local daemon only).
    Otherwise the request must present the token in the
    ``x-apme-proxy-token`` header.

    Args:
        request: Incoming HTTP request.

    Raises:
        HTTPException: 403 when no token is configured (and no opt-out),
            when the configured token is invalid, or when the presented
            token does not match.
    """
    expected = _admin_token_configured()
    if expected == "":
        if _unauth_admin_allowed():
            return
        raise HTTPException(
            status_code=403,
            detail="Admin token is not configured; set APME_PROXY_ADMIN_TOKEN "
            "or explicitly allow unauthenticated admin with "
            "APME_PROXY_ALLOW_UNAUTH_ADMIN=1",
        )
    if expected is None:
        raise HTTPException(status_code=403, detail="Invalid admin token")
    provided = request.headers.get(_ADMIN_TOKEN_HEADER, "")
    try:
        provided.encode("ascii")
    except UnicodeEncodeError:
        raise HTTPException(status_code=403, detail="Invalid admin token") from None
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Invalid admin token")


def _safe_server_label(raw_url: str) -> str:
    """Return an upstream URL label without query, fragment, or credentials.

    Args:
        raw_url: Upstream server URL.

    Returns:
        Sanitized host and port label.
    """
    parsed = urlsplit(raw_url)
    if not parsed.hostname:
        return "unknown"
    try:
        return f"{parsed.hostname}:{parsed.port}" if parsed.port else parsed.hostname
    except ValueError:
        return "unknown"


def _validate_galaxy_server_url(raw_url: str) -> None:
    """Reject Galaxy server URLs that cannot safely receive stored tokens.

    A pushed server URL later receives the victim's stored Galaxy token in
    the Authorization header on every version-list and tarball download, so
    an attacker who can POST ``/admin/galaxy-config`` must not be able to
    point it at an arbitrary host. This rejects non-HTTPS schemes (tokens
    would travel in cleartext), embedded userinfo (credentials the proxy
    would forward), and literal IPs that target this host or the local link
    (loopback, link-local including the cloud-metadata address, multicast,
    unspecified, reserved). Hostnames are accepted — they cannot be judged
    without DNS — and private-range IPs stay allowed for enterprise hubs.

    Args:
        raw_url: Galaxy server URL from the admin config push.

    Raises:
        HTTPException: 422 describing why the URL is rejected.
    """
    url = (raw_url or "").strip()
    parsed = urlsplit(url)
    if parsed.scheme != "https":
        raise HTTPException(
            status_code=422,
            detail=f"Galaxy server URL must use https: {raw_url!r}",
        )
    if parsed.username or parsed.password:
        raise HTTPException(
            status_code=422,
            detail=f"Galaxy server URL must not embed userinfo credentials: {raw_url!r}",
        )
    host = parsed.hostname or ""
    if not host:
        raise HTTPException(
            status_code=422,
            detail=f"Galaxy server URL has no host: {raw_url!r}",
        )
    bare_host = host.rstrip(".")
    if bare_host == "localhost" or bare_host.endswith(".localhost"):
        raise HTTPException(
            status_code=422,
            detail=f"Galaxy server URL must not target a local/link-local address: {raw_url!r}",
        )
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Encoded literal IPs (all-decimal integers like 2130706433,
        # hex/octal dotted quads) raise ValueError here yet the OS
        # resolver still maps them to loopback/link-local addresses —
        # reject anything inet_aton accepts so such URLs cannot bypass
        # the block below and later receive stored Galaxy tokens.
        try:
            socket.inet_aton(host)
        except OSError:
            return  # Hostname: cannot judge without DNS; allowed.
        raise HTTPException(
            status_code=422,
            detail=f"Galaxy server URL must not target a local/link-local address: {raw_url!r}",
        ) from None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        # IPv4-mapped IPv6 literals (e.g. ::ffff:127.0.0.1) report
        # is_loopback/is_link_local False on some releases yet connect to
        # the embedded IPv4 target — judge the mapped address instead.
        ip = ip.ipv4_mapped
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        raise HTTPException(
            status_code=422,
            detail=f"Galaxy server URL must not target a local/link-local address: {raw_url!r}",
        )


_GALAXY_API_URL = "https://galaxy.ansible.com"
_GALAXY_VERSIONS_PATH = "/api/v3/plugin/ansible/content/published/collections/index"


class _GalaxyServerPayload(BaseModel):  # type: ignore[misc]
    """Single Galaxy server entry in the admin config push.

    Attributes:
        name: Server identifier (e.g. ``certified``).
        url: Galaxy API URL.
        token: Authentication token (optional).
        auth_url: SSO auth URL for token refresh (optional).
    """

    name: str
    url: str
    token: str = ""
    auth_url: str = ""


class _GalaxyConfigPayload(BaseModel):  # type: ignore[misc]
    """Payload for ``POST /admin/galaxy-config``.

    Attributes:
        servers: List of Galaxy server configurations to register.
    """

    servers: list[_GalaxyServerPayload]


def create_app(
    pypi_url: str = "https://pypi.org",
    cache_dir: Path | None = None,
    metadata_ttl: float = 600.0,
    enable_passthrough: bool = True,
    *,
    ansible_cfg_path: Path | None = None,
    galaxy_servers: list[GalaxyServerConfig] | None = None,
    ansible_galaxy_bin: str | None = None,
) -> FastAPI:
    """Create and configure the proxy FastAPI application.

    Galaxy authentication and server discovery are delegated entirely to
    ``ansible-galaxy`` (ADR-045).  The proxy's role is tarball-to-wheel
    conversion and PEP 503 serving.

    When ``galaxy_servers`` is provided, the proxy writes a temporary
    ``ansible.cfg`` for each download invocation.  When ``ansible_cfg_path``
    is provided, the user's existing config is used directly.  If neither
    is set, ``ansible-galaxy`` uses its default config discovery.

    Args:
        pypi_url: Base URL for PyPI passthrough (non-collection packages).
        cache_dir: Optional cache root; defaults to XDG cache layout.
        metadata_ttl: Seconds before cached metadata is considered stale
            (passed through to :class:`ProxyCache`).
        enable_passthrough: Whether to forward non-collection packages to PyPI.
        ansible_cfg_path: Path to an existing ``ansible.cfg`` for Galaxy auth.
        galaxy_servers: Ordered list of Galaxy server configs (ansible.cfg-style).
        ansible_galaxy_bin: Override path to the ``ansible-galaxy`` binary.

    Returns:
        Configured FastAPI application instance.

    Raises:
        ValueError: When both ``ansible_cfg_path`` and ``galaxy_servers``
            are provided.
    """
    if ansible_cfg_path and galaxy_servers:
        msg = "ansible_cfg_path and galaxy_servers are mutually exclusive"
        raise ValueError(msg)

    cache = ProxyCache(cache_dir=cache_dir, metadata_ttl=metadata_ttl)
    passthrough = PyPIPassthrough(pypi_url=pypi_url) if enable_passthrough else None
    _download_locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: ARG001
        yield
        if passthrough:
            await passthrough.close()

    app = FastAPI(title="Ansible Collection Proxy", version="0.2.0", lifespan=lifespan)

    @app.middleware("http")  # type: ignore[untyped-decorator]
    async def log_response(request: Request, call_next: Any) -> Response:
        """Log the status, duration, and response size for each HTTP request.

        Args:
            request: Incoming HTTP request.
            call_next: Downstream ASGI request handler.

        Returns:
            Downstream HTTP response.

        Raises:
            Exception: Re-raises an unhandled downstream request exception.
        """
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = (time.perf_counter() - started) * 1000
            logger.error(
                "server_response method=%s path=%s status=500 duration_ms=%.1f size_bytes=unknown error_type=%s",
                request.method,
                request.url.path,
                duration_ms,
                type(exc).__name__,
            )
            raise
        else:
            duration_ms = (time.perf_counter() - started) * 1000
            size = response.headers.get("content-length", "unknown")
            logger.info(
                "server_response method=%s path=%s status=%d duration_ms=%.1f size_bytes=%s",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
                size,
            )
            return response

    app.state.galaxy_servers = list(galaxy_servers) if galaxy_servers else []
    app.state.ansible_cfg_path = ansible_cfg_path
    app.state.ansible_galaxy_bin = ansible_galaxy_bin

    if _admin_token_configured() == "" and not _unauth_admin_allowed():
        logger.warning(
            "APME_PROXY_ADMIN_TOKEN is not configured — /admin/galaxy-config and "
            "/convert-tarballs reject all requests. Set APME_PROXY_ADMIN_TOKEN "
            "to require token auth on the admin surface, or explicitly allow "
            "unauthenticated admin on a single-host daemon with "
            "APME_PROXY_ALLOW_UNAUTH_ADMIN=1."
        )
    elif _admin_token_configured() == "":
        logger.warning(
            "APME_PROXY_ALLOW_UNAUTH_ADMIN is set — /admin/galaxy-config and "
            "/convert-tarballs are unprotected. Only use this opt-out on a "
            "single-host daemon; set APME_PROXY_ADMIN_TOKEN everywhere else."
        )

    def _get_galaxy_config() -> tuple[Path | None, list[GalaxyServerConfig] | None, str | None]:
        """Read current Galaxy config from app state.

        Returns:
            tuple: (ansible_cfg_path, galaxy_servers, ansible_galaxy_bin).
        """
        servers = app.state.galaxy_servers
        cfg_path = app.state.ansible_cfg_path
        if cfg_path is None:
            env_cfg = os.environ.get("ANSIBLE_CONFIG", "").strip()
            if env_cfg:
                candidate = Path(env_cfg).expanduser()
                if candidate.is_file():
                    cfg_path = candidate
        galaxy_bin = app.state.ansible_galaxy_bin
        return cfg_path, servers or None, galaxy_bin

    @app.get("/health")  # type: ignore[untyped-decorator]
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/admin/galaxy-config")  # type: ignore[untyped-decorator]
    async def update_galaxy_config(request: Request, body: _GalaxyConfigPayload) -> dict[str, Any]:
        """Accept Galaxy server configs pushed from the Gateway (ADR-045).

        The Gateway calls this after startup and after any CRUD change to
        the Galaxy server settings.  The proxy stores the configs in memory
        and uses them for all subsequent ``ansible-galaxy`` downloads.

        Args:
            request: Incoming HTTP request (carries the admin token header).
            body: Galaxy server configurations to register.

        Returns:
            dict: Confirmation with count and names of accepted servers.

        Raises:
            HTTPException: 403 if the admin token is invalid or unconfigured;
                422 if any server name is empty, invalid, or duplicated, or
                any server URL is unsafe (non-https, embedded userinfo, or
                local/link-local target).
        """
        _require_admin_token(request)
        seen: set[str] = set()
        for s in body.servers:
            name = s.name.strip()
            if not name:
                raise HTTPException(status_code=422, detail="Server name must not be empty")
            if not re.match(r"^[A-Za-z0-9_\-]+$", name):
                raise HTTPException(status_code=422, detail=f"Invalid server name: {s.name!r}")
            if name.upper() in seen:
                raise HTTPException(status_code=422, detail=f"Duplicate server name: {s.name!r}")
            seen.add(name.upper())
            # Fail closed before storing: a stored URL later receives the
            # victim's Galaxy token on every download, so an unsafe URL must
            # never reach app state (tokens are never attached to hosts that
            # fail validation because they are never stored).
            _validate_galaxy_server_url(s.url)

        app.state.galaxy_servers = [
            GalaxyServerConfig(
                name=s.name.strip(),
                url=s.url,
                token=s.token or None,
                auth_url=s.auth_url or None,
            )
            for s in body.servers
        ]
        app.state.ansible_cfg_path = None
        names = [s.name.strip() for s in body.servers]
        logger.info("Galaxy config updated: %d server(s): %s", len(names), ", ".join(names))
        return {"accepted": len(names), "servers": names}

    @app.get("/simple/", response_class=HTMLResponse)  # type: ignore[untyped-decorator]
    async def root_index() -> str:
        """Root index page.

        Returns:
            Minimal HTML document string.
        """
        return (
            "<!DOCTYPE html>\n"
            "<html><body>\n"
            "<h1>Ansible Collection Proxy</h1>\n"
            "<p>Use pip install --extra-index-url to install collections.</p>\n"
            "</body></html>\n"
        )

    @app.get("/simple/{package_name}/", response_class=HTMLResponse)  # type: ignore[untyped-decorator]
    async def project_page(package_name: str) -> HTMLResponse:
        """PEP 503 project page listing available versions.

        For collections, lists all Galaxy versions (cached with TTL) so
        pip can resolve any version constraint.  Cached wheels include
        SHA256 hashes; uncached versions get plain links — ``serve_wheel``
        downloads on demand when pip requests them.

        Args:
            package_name: Requested package name from the URL path.

        Returns:
            HTML Simple API response (collection listing or passthrough).

        Raises:
            HTTPException: When passthrough is disabled for a non-collection
                package.
        """
        normalized = normalize_pep503(package_name)

        if not is_collection_package(normalized):
            logger.info("package_requested package=%s type=pypi endpoint=project", normalized)
            if passthrough is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Package {package_name!r} is not an Ansible collection and passthrough is disabled",
                )
            html, status = await passthrough.fetch_project_page(normalized)
            return HTMLResponse(content=html, status_code=status)

        logger.info("collection_requested package=%s type=collection endpoint=project", normalized)

        try:
            namespace, name = python_to_fqcn(normalized)
        except ValueError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"Package {package_name!r} is not a valid Ansible collection name",
            ) from exc

        cached_wheel_set = set(_list_cached_wheels(cache, namespace, name))

        versions: list[str] | None = None
        meta = cache.get_metadata(namespace, name)
        if meta is not None:
            versions = meta.versions
            logger.info("metadata_cache_hit collection=%s.%s versions=%d", namespace, name, len(versions))
        else:
            logger.info("metadata_cache_miss collection=%s.%s", namespace, name)

        if versions is None:
            cfg_path, servers_cfg, _ = _get_galaxy_config()
            if servers_cfg is None and cfg_path is not None:
                servers_cfg = _load_servers_from_ansible_cfg(cfg_path) or None
            galaxy_versions = await _fetch_galaxy_versions(
                namespace,
                name,
                servers=servers_cfg,
            )
            if galaxy_versions is not None:
                if galaxy_versions:
                    cache.put_metadata(namespace, name, galaxy_versions)
                versions = galaxy_versions
            else:
                # Truncation or total server failure — do not cache an empty
                # listing that would masquerade as a valid zero-version catalog.
                versions = []

        if not versions and not cached_wheel_set:
            lock_key = f"{namespace}.{name}:latest"
            lock = _download_locks.get(lock_key)
            if lock is None:
                lock = _download_locks.setdefault(lock_key, asyncio.Lock())
            async with lock:
                cached_wheel_set = set(_list_cached_wheels(cache, namespace, name))
                if not cached_wheel_set:
                    try:
                        cfg_path, servers_cfg, galaxy_bin = _get_galaxy_config()
                        whl_name, whl_data = await _download_and_convert(
                            namespace,
                            name,
                            "",
                            ansible_cfg_path=cfg_path,
                            galaxy_servers=servers_cfg,
                            ansible_galaxy_bin=galaxy_bin,
                        )
                        cache.put_wheel(whl_name, whl_data)
                        logger.info("On-demand download for %s.%s: %s", namespace, name, whl_name)
                        cached_wheel_set = {whl_name}
                    except Exception:
                        logger.warning(
                            "On-demand download failed for %s.%s — returning empty listing",
                            namespace,
                            name,
                            exc_info=True,
                        )

        links: list[str] = []
        seen_versions: set[str] = set()

        for whl_name in sorted(cached_wheel_set):
            cached_wheel = cache.wheel_path(whl_name)
            whl_hash = sha256_file_hex(cached_wheel) if cached_wheel else ""
            href = f"/wheels/{whl_name}"
            if whl_hash:
                href += f"#sha256={whl_hash}"
            links.append(f'<a href="{href}">{whl_name}</a>')
            parts = whl_name.split("-")
            if len(parts) >= 2:
                seen_versions.add(parts[1])

        if versions:
            for ver in versions:
                if ver in seen_versions:
                    continue
                whl_name = wheel_filename(namespace, name, ver)
                links.append(f'<a href="/wheels/{whl_name}">{whl_name}</a>')

        html = "<!DOCTYPE html>\n<html><body>\n" + "\n".join(links) + "\n</body></html>\n"
        return HTMLResponse(content=html)

    @app.get("/wheels/{filename}")  # type: ignore[untyped-decorator]
    async def serve_wheel(filename: str) -> Response:
        """Serve a wheel file, downloading and converting on cache miss.

        On cache miss, uses ``ansible-galaxy collection download`` to fetch
        the tarball, converts it to a wheel, and caches the result.

        Args:
            filename: Requested wheel filename from the URL path.

        Returns:
            Binary wheel response with appropriate content headers.

        Raises:
            HTTPException: When the filename is invalid, namespace/name cannot
                be parsed, or Galaxy download fails.
        """
        if not filename.endswith(".whl") or "/" in filename or "\\" in filename or ".." in filename:
            raise HTTPException(status_code=404, detail=f"Invalid wheel filename: {filename}")

        started = time.perf_counter()

        def _record_serve(outcome: str, status: str = "ok") -> None:
            try:
                from apme_engine.observability import record_galaxy_wheel_serve

                record_galaxy_wheel_serve(
                    time.perf_counter() - started,
                    outcome=outcome,
                    status=status,
                )
            except Exception:  # noqa: BLE001 — never fail serves for metrics
                logger.debug("Failed to record Galaxy wheel serve metrics", exc_info=True)

        cached = cache.get_wheel(filename)
        if cached:
            logger.info("collection_requested collection=%s endpoint=wheel", filename)
            logger.info("wheel_cache_hit filename=%s size_bytes=%d", filename, len(cached))
            _record_serve("hit")
            return Response(
                content=cached,
                media_type="application/octet-stream",
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )

        dist_name = filename.split("-")[0] if "-" in filename else ""
        pkg_name = normalize_pep503(dist_name.replace("_", "-"))
        if not is_collection_package(pkg_name):
            raise HTTPException(status_code=404, detail=f"Invalid wheel filename: {filename}")

        try:
            ns, coll_name = python_to_fqcn(pkg_name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=f"Cannot parse namespace/name: {filename}") from exc

        parts = filename.replace(".whl", "").split("-")
        if len(parts) < 5:
            raise HTTPException(status_code=404, detail=f"Invalid wheel filename: {filename}")
        version = parts[1]
        logger.info(
            "collection_requested collection=%s.%s version=%s endpoint=wheel",
            ns,
            coll_name,
            version,
        )

        lock_key = f"{ns}.{coll_name}:{version}"
        lock = _download_locks.get(lock_key)
        if lock is None:
            lock = _download_locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            cached = cache.get_wheel(filename)
            if cached:
                logger.info("wheel_cache_hit_after_lock filename=%s size_bytes=%d", filename, len(cached))
                _record_serve("hit")
                return Response(
                    content=cached,
                    media_type="application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename={filename}"},
                )

            logger.info("wheel_cache_miss filename=%s", filename)

            try:
                cfg_path, servers, galaxy_bin = _get_galaxy_config()
                whl_name, whl_data = await _download_and_convert(
                    ns,
                    coll_name,
                    version,
                    ansible_cfg_path=cfg_path,
                    galaxy_servers=servers,
                    ansible_galaxy_bin=galaxy_bin,
                )
            except Exception as exc:
                logger.error(
                    "Failed to download/convert %s.%s %s error_type=%s",
                    ns,
                    coll_name,
                    version,
                    type(exc).__name__,
                )
                _record_serve("miss", status="error")
                raise HTTPException(
                    status_code=502,
                    detail=f"Failed to download/convert {ns}.{coll_name} {version} via ansible-galaxy",
                ) from exc

            cache.put_wheel(whl_name, whl_data)

        _record_serve("miss")
        return Response(
            content=whl_data,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename={whl_name}"},
        )

    @app.post("/convert-tarballs")  # type: ignore[untyped-decorator]
    async def convert_tarballs(request: Request, tarball_dir: str) -> dict[str, list[str]]:
        """Convert all tarballs in a directory to wheels and cache them.

        This endpoint supports the flow where Engine sends collection specs
        and the proxy converts pre-downloaded tarballs to wheels.

        Args:
            request: Incoming HTTP request (carries the admin token header).
            tarball_dir: Path to directory containing ``.tar.gz`` files
                (resolved to absolute internally).

        Returns:
            Dict with ``converted`` (wheel filenames) and ``failed`` (tarball names).

        Raises:
            HTTPException: 403 if the admin token is invalid.
        """  # noqa: DOC502 -- the 403 raise lives in _require_admin_token
        _require_admin_token(request)
        tarball_path = _validate_tarball_dir(tarball_dir)

        converted: list[str] = []
        failed: list[str] = []

        for tb in sorted(tarball_path.glob("*.tar.gz")):
            if tb.is_symlink() or not tb.is_file():
                logger.warning("Skipping non-regular tarball entry: %s", tb)
                failed.append(tb.name)
                continue
            try:
                tarball_data = await asyncio.to_thread(tb.read_bytes)
                whl_name, whl_data = await asyncio.to_thread(tarball_to_wheel, tarball_data)
                cache.put_wheel(whl_name, whl_data)
                converted.append(whl_name)
                logger.info("Converted tarball: %s -> %s", tb.name, whl_name)
            except Exception:
                logger.exception("Failed to convert tarball: %s", tb.name)
                failed.append(tb.name)

        return {"converted": converted, "failed": failed}

    return app


def _validate_tarball_dir(tarball_dir: str) -> Path:
    """Validate and resolve a tarball directory path.

    Resolves the path first (eliminating symlinks and ``..`` components),
    then validates each component of the relative suffix to ensure no
    traversal, and reconstructs the result purely from trusted roots.

    Args:
        tarball_dir: Untrusted path string from the request.

    Returns:
        The resolved, validated ``Path``.

    Raises:
        HTTPException: On disallowed roots or non-directories.
    """
    resolved = Path(os.path.realpath(tarball_dir))

    allowed_roots = (Path(tempfile.gettempdir()).resolve(), Path("/sessions").resolve())
    matched_root: Path | None = None
    for root in allowed_roots:
        if resolved.is_relative_to(root):
            matched_root = root
            break

    if matched_root is None:
        raise HTTPException(
            status_code=400,
            detail="Path must be under a session or temp directory",
        )

    relative_parts = resolved.relative_to(matched_root).parts
    for part in relative_parts:
        if part in (".", "..") or os.sep in part or (os.altsep and os.altsep in part):
            raise HTTPException(
                status_code=400,
                detail="Path contains invalid components",
            )

    safe_path = matched_root.joinpath(*relative_parts) if relative_parts else matched_root
    if not safe_path.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory")

    return safe_path


def _galaxy_version_sort_key(version: str) -> tuple[int, Version | str]:
    """Build a sort key for PEP 440 version ordering.

    Args:
        version: Galaxy collection version string.

    Returns:
        ``(0, Version(...))`` for valid PEP 440 versions, or ``(1, version)``
        so non-PEP-440 strings sort after all valid ones.
    """
    try:
        return (0, Version(version))
    except InvalidVersion:
        return (1, version)


def _load_servers_from_ansible_cfg(cfg_path: Path) -> list[GalaxyServerConfig]:
    """Parse Galaxy servers from an ``ansible.cfg`` file.

    This is used by the proxy's version-discovery path when no Gateway-pushed
    server list is present, such as local daemon mode where the Engine
    temporarily exposes a session-scoped ``ANSIBLE_CONFIG``.

    Args:
        cfg_path: Path to the config file.

    Returns:
        Ordered Galaxy server configs, or an empty list on parse failure.
    """
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(str(cfg_path), encoding="utf-8")
    except (configparser.Error, OSError):
        logger.debug("Failed to parse ansible.cfg for Galaxy servers: %s", cfg_path, exc_info=True)
        return []

    raw_list = parser.get("galaxy", "server_list", fallback="")
    server_names = [name.strip() for name in raw_list.split(",") if name.strip()]
    servers: list[GalaxyServerConfig] = []
    for name in server_names:
        section = f"galaxy_server.{name}"
        if not parser.has_section(section):
            continue
        url = parser.get(section, "url", fallback="").strip()
        if not url:
            continue
        token = parser.get(section, "token", fallback="").strip() or None
        auth_url = parser.get(section, "auth_url", fallback="").strip() or None
        servers.append(
            GalaxyServerConfig(
                name=name,
                url=url,
                token=token,
                auth_url=auth_url,
            )
        )
    return servers


async def _fetch_galaxy_versions(
    namespace: str,
    name: str,
    *,
    servers: list[GalaxyServerConfig] | None = None,
) -> list[str] | None:
    """Fetch all published version strings for a collection from Galaxy.

    When *servers* is provided, each configured server is tried in order
    (matching ``ansible.cfg`` ``server_list`` semantics).  The first
    server to return a successful response wins.  If no configured server
    succeeds — or if no servers are configured — falls back to public
    Galaxy (``galaxy.ansible.com``).

    This enables version discovery from console.redhat.com / Automation
    Hub / private Galaxy instances configured via the Gateway UI.

    Args:
        namespace: Collection namespace.
        name: Collection name.
        servers: Ordered list of Galaxy server configs (optional).

    Returns:
        Sorted list of version strings on success (possibly empty), or
        ``None`` when every server fails (including pagination truncation).
    """
    base_urls: list[tuple[str, str | None]] = []
    for srv in servers or []:
        base_urls.append((srv.url.rstrip("/"), srv.token))
    base_urls.append((_GALAXY_API_URL, None))

    for base_url, token in base_urls:
        logger.info(
            "galaxy_backend_call operation=version_lookup collection=%s.%s server=%s",
            namespace,
            name,
            _safe_server_label(base_url),
        )
        versions = await _fetch_versions_from(namespace, name, base_url, token=token)
        if versions is not None:
            return sorted(set(versions), key=_galaxy_version_sort_key)

    tried = [_safe_server_label(url) for url, _ in base_urls]
    logger.warning(
        "All Galaxy servers failed for %s.%s (tried: %s)",
        namespace,
        name,
        ", ".join(tried),
    )
    return None


def _normalize_galaxy_url(raw_url: str) -> str:
    """Strip ansible.cfg-style ``/api/...`` suffixes from the URL path.

    Configured Galaxy servers often include ``/api/``, ``/api/galaxy/``,
    or ``/api/galaxy/content/...`` in their URL.  ``_GALAXY_VERSIONS_PATH``
    already starts with ``/api/v3/...``, so we must strip any leading
    ``/api`` path segment to avoid ``/api/api/v3/...``.

    Only the *path* component is inspected — hostnames like
    ``https://api.example.com`` are preserved correctly.

    Args:
        raw_url: Server URL as provided by the user / Gateway config.

    Returns:
        Base URL with ``/api...`` path segments removed.
    """
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(raw_url)
    path = parts.path.rstrip("/")
    segments = path.split("/")
    try:
        api_index = segments.index("api")
    except ValueError:
        api_index = -1
    if api_index != -1:
        path = "/".join(segments[:api_index])
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


async def _fetch_versions_from(
    namespace: str,
    name: str,
    base_url: str,
    *,
    token: str | None = None,
) -> list[str] | None:
    """Fetch version strings from a single Galaxy-compatible server.

    Args:
        namespace: Collection namespace.
        name: Collection name.
        base_url: Base URL of the Galaxy API (no trailing slash).
        token: Optional auth token for the server.

    Returns:
        List of version strings on success, or ``None`` on failure — or
        when the listing is truncated at ``MAX_VERSION_PAGES`` — so the
        caller can fall through to the next server. A truncated listing is
        not a complete answer and must never resolve as one.
    """
    versions: list[str] = []
    normalized = _normalize_galaxy_url(base_url)
    url = f"{normalized}{_GALAXY_VERSIONS_PATH}/{namespace}/{name}/versions/"
    params: dict[str, str | int] = {"limit": 100, "offset": 0}
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Token {token}"
    server_host = urlsplit(normalized).hostname or ""
    started = time.perf_counter()
    status = "error"
    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers=headers,
        ) as client:
            for attempt in range(3):
                try:
                    params = {"limit": 100, "offset": 0}
                    versions.clear()
                    for _page in range(MAX_VERSION_PAGES):
                        resp = await client.get(url, params=params)
                        logger.info(
                            "galaxy_backend_response operation=version_lookup collection=%s.%s server=%s status=%d",
                            namespace,
                            name,
                            server_host,
                            resp.status_code,
                        )
                        resp.raise_for_status()
                        payload = resp.json()
                        if not isinstance(payload, dict):
                            # A non-dict JSON body (e.g. a list or string) has no
                            # ``.get`` — treat it as a failure so the caller falls
                            # through to the next server instead of raising
                            # AttributeError.
                            logger.debug(
                                "Version fetch from %s returned non-dict payload (%s) for %s.%s",
                                base_url,
                                type(payload).__name__,
                                namespace,
                                name,
                            )
                            return None
                        entries = payload.get("data")
                        if not isinstance(entries, list):
                            logger.debug(
                                "Version fetch from %s returned non-list data for %s.%s",
                                base_url,
                                namespace,
                                name,
                            )
                            return None
                        for entry in entries:
                            if not isinstance(entry, dict):
                                logger.debug(
                                    "Version fetch from %s returned non-object entry for %s.%s",
                                    base_url,
                                    namespace,
                                    name,
                                )
                                return None
                            versions.append(entry["version"])
                        if "links" in payload:
                            links = payload["links"]
                            if not isinstance(links, dict):
                                logger.debug(
                                    "Version fetch from %s returned non-object links for %s.%s",
                                    base_url,
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
                    status = "ok"
                    return versions
                except httpx.HTTPError:
                    if attempt == 2:
                        return None
                    versions.clear()
                    await asyncio.sleep(0.5 * (attempt + 1))
        return versions
    except (httpx.HTTPError, KeyError, TypeError, AttributeError, ValueError) as exc:
        logger.debug(
            "Version fetch from %s failed for %s.%s: %s",
            _safe_server_label(base_url),
            namespace,
            name,
            type(exc).__name__,
        )
        return None
    finally:
        try:
            from apme_engine.observability import record_galaxy_fetch

            record_galaxy_fetch(
                time.perf_counter() - started,
                operation="version_lookup",
                status=status,
                server=server_host,
            )
        except Exception:  # noqa: BLE001 — never fail lookups for metrics
            logger.debug("Failed to record Galaxy version-lookup metrics", exc_info=True)


def _list_cached_wheels(cache: ProxyCache, namespace: str, name: str) -> list[str]:
    """List cached wheel filenames for a collection.

    Scans the cache's wheels directory for files matching the collection's
    naming pattern.

    Args:
        cache: The proxy cache instance.
        namespace: Collection namespace.
        name: Collection name.

    Returns:
        Sorted list of matching wheel filenames.
    """
    prefix = f"ansible_collection_{namespace}_{name}-"
    wheels: list[str] = []
    if cache.wheels_dir.is_dir():
        for whl in cache.wheels_dir.glob(f"{prefix}*.whl"):
            wheels.append(whl.name)
    return sorted(wheels)


async def _download_and_convert(
    namespace: str,
    name: str,
    version: str,
    *,
    ansible_cfg_path: Path | None = None,
    galaxy_servers: list[GalaxyServerConfig] | None = None,
    ansible_galaxy_bin: str | None = None,
) -> tuple[str, bytes]:
    """Download a single collection tarball and convert to a wheel.

    When *version* is empty, ``ansible-galaxy`` downloads the latest
    available version.

    Args:
        namespace: Collection namespace.
        name: Collection name.
        version: Collection version string (empty for latest).
        ansible_cfg_path: Path to an existing ``ansible.cfg``.
        galaxy_servers: Galaxy server configs for temp ansible.cfg.
        ansible_galaxy_bin: Override path to ``ansible-galaxy``.

    Returns:
        Tuple of ``(wheel_filename, wheel_bytes)``.

    Raises:
        RuntimeError: If download or conversion fails.
    """
    spec = f"{namespace}.{name}:{version}" if version else f"{namespace}.{name}"
    started = time.perf_counter()
    logger.info(
        "galaxy_backend_call operation=download collection=%s version=%s server=configured",
        spec,
        version or "latest",
    )

    with tempfile.TemporaryDirectory(prefix="apme-galaxy-dl-") as tmp:
        download_dir = Path(tmp)

        download_started = time.perf_counter()
        result = await download_collections(
            [spec],
            download_dir,
            ansible_cfg_path=ansible_cfg_path,
            servers=galaxy_servers,
            ansible_galaxy_bin=ansible_galaxy_bin,
        )

        if result.failed_specs:
            msg = f"Failed to download {spec}"
            raise RuntimeError(msg)

        if not result.tarball_paths:
            msg = f"No tarball found after downloading {spec}"
            raise RuntimeError(msg)

        tarball_path = result.tarball_paths[0]
        tarball_data = await asyncio.to_thread(tarball_path.read_bytes)
        download_duration_ms = (time.perf_counter() - download_started) * 1000
        logger.info(
            "galaxy_backend_response operation=download collection=%s version=%s "
            "status=200 duration_ms=%.1f size_bytes=%d",
            spec,
            version or "latest",
            download_duration_ms,
            len(tarball_data),
        )

        conversion_started = time.perf_counter()
        whl_name, whl_data = await asyncio.to_thread(tarball_to_wheel, tarball_data)
        conversion_duration_ms = (time.perf_counter() - conversion_started) * 1000
        total_duration_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "collection_download_complete collection=%s version=%s "
            "download_duration_ms=%.1f conversion_duration_ms=%.1f "
            "total_duration_ms=%.1f tarball_size_bytes=%d wheel_size_bytes=%d",
            spec,
            version or "latest",
            download_duration_ms,
            conversion_duration_ms,
            total_duration_ms,
            len(tarball_data),
            len(whl_data),
        )
        return whl_name, whl_data
