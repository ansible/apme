"""PEP 503 Simple Repository API server for Ansible collections.

Serves Python wheels converted from Galaxy collection tarballs.  Tarballs
are obtained via ``ansible-galaxy collection download`` (ADR-045), not a
custom httpx client.
"""

from __future__ import annotations

import asyncio
import configparser
import hashlib
import hmac
import io
import ipaddress
import json
import logging
import os
import re
import socket
import tempfile
import time
import zipfile
from contextlib import asynccontextmanager
from dataclasses import asdict
from email.parser import Parser
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import InvalidWheelFilename, parse_wheel_filename
from packaging.version import Version
from pydantic import BaseModel

from galaxy_proxy.collection_downloader import (
    GalaxyServerConfig,
    download_collections,
    download_error_summary,
    read_server_validate_certs,
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
_ON_DEMAND_WHEEL_TTL_S = 300.0
_ON_DEMAND_WHEEL_LIMIT_BYTES = 64 * 1024 * 1024


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
    the Authorization header on every CLI download, so
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


class _GalaxyServerPayload(BaseModel):  # type: ignore[misc]
    """Single Galaxy server entry in the admin config push.

    Attributes:
        name: Server identifier (e.g. ``certified``).
        url: Galaxy API URL.
        token: Authentication token (optional).
        auth_url: SSO auth URL for token refresh (optional).
        validate_certs: TLS verification override, or None to inherit the CLI default.
    """

    name: str
    url: str
    token: str = ""
    auth_url: str = ""
    validate_certs: bool | None = None


class _GalaxyConfigPayload(BaseModel):  # type: ignore[misc]
    """Payload for ``POST /admin/galaxy-config``.

    Attributes:
        servers: List of Galaxy server configurations to register.
    """

    servers: list[_GalaxyServerPayload]


class _PrepareCollectionsPayload(BaseModel):  # type: ignore[misc]
    """Collection specs to resolve through ``ansible-galaxy``.

    Attributes:
        specs: Collection FQCNs with optional version constraints.
    """

    specs: list[str]


def _source_fingerprint(servers: list[GalaxyServerConfig] | None, cfg_path: Path | None) -> str:
    """Hash source configuration and TLS trust without persisting credentials.

    Args:
        servers: Explicit server definitions, or None for CLI discovery.
        cfg_path: Optional ansible.cfg used by the CLI.

    Returns:
        SHA-256 digest identifying the effective collection source configuration.
    """
    config: dict[str, Any] = {
        "servers": None if servers is None else [asdict(server) for server in servers],
        "environment": {
            key: value
            for key, value in os.environ.items()
            if key.startswith("ANSIBLE_GALAXY_") or key in {"SSL_CERT_FILE", "SSL_CERT_DIR"}
        },
    }
    for label, path in (
        ("ansible_cfg", cfg_path),
        ("ca_bundle", Path(os.environ["SSL_CERT_FILE"]) if os.environ.get("SSL_CERT_FILE") else None),
    ):
        if path is not None:
            try:
                config[label] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                config[label] = {"path": str(path), "unreadable": True}
    if servers is None and cfg_path is None:
        # Include native CLI discovery candidates, even when a higher-priority
        # file makes another candidate unused. Extra invalidation is safe.
        for candidate in (Path.cwd() / "ansible.cfg", Path.home() / ".ansible.cfg", Path("/etc/ansible/ansible.cfg")):
            try:
                config[str(candidate)] = hashlib.sha256(candidate.read_bytes()).hexdigest()
            except OSError:
                config[str(candidate)] = None
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def create_app(
    pypi_url: str = "https://pypi.org",
    cache_dir: Path | None = None,
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

    cache = ProxyCache(cache_dir=cache_dir)
    passthrough = PyPIPassthrough(pypi_url=pypi_url) if enable_passthrough else None
    _download_locks: dict[str, asyncio.Lock] = {}
    _prepare_locks: dict[str, asyncio.Lock] = {}
    _config_lock = asyncio.Lock()
    # Short-lived handoff for a project page whose disk cache write failed.
    # The pip wheel request can still succeed without another upstream fetch.
    _on_demand_wheels: dict[str, tuple[float, bytes]] = {}
    # Exact pins that failed preparation still need a versioned wheel link so
    # pip can invoke the wheel route's exact-version Hub retry.
    _prepare_fallback_wheels: dict[str, float] = {}

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
        path = request.url.path
        simple_package = normalize_pep503(path.removeprefix("/simple/").rstrip("/"))
        collection_path = (
            path.startswith("/wheels/")
            or path == "/simple/"
            or (path.startswith("/simple/") and is_collection_package(simple_package))
        )
        if collection_path or path in {"/admin/prepare-collections", "/convert-tarballs"}:
            await _reconcile_native_config()
        if collection_path and not app.state.galaxy_config_ready:
            return Response("Galaxy configuration has not been restored yet", status_code=503)
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

    app.state.galaxy_servers = None if galaxy_servers is None else list(galaxy_servers)
    app.state.ansible_cfg_path = ansible_cfg_path
    app.state.ansible_galaxy_bin = ansible_galaxy_bin
    app.state.galaxy_config_generation = 0
    initial_cfg = ansible_cfg_path
    if initial_cfg is None and os.environ.get("ANSIBLE_CONFIG", "").strip():
        initial_cfg = Path(os.environ["ANSIBLE_CONFIG"]).expanduser()
    initial_fingerprint = _source_fingerprint(app.state.galaxy_servers, initial_cfg)
    require_gateway = os.environ.get("APME_PROXY_REQUIRE_GATEWAY_CONFIG", "").strip().lower() in {"1", "true", "yes"}
    app.state.galaxy_config_ready = not require_gateway
    if app.state.galaxy_config_ready:
        cache.bind_config(initial_fingerprint)
    app.state.galaxy_config_managed = not app.state.galaxy_config_ready
    app.state.galaxy_source_fingerprint = initial_fingerprint

    async def _bind_cache_config(fingerprint: str, *, managed: bool) -> None:
        """Bind in a worker and hold the config lock until disk work completes.

        Args:
            fingerprint: Effective source digest.
            managed: Whether Gateway owns configuration.

        Raises:
            asyncio.CancelledError: After any in-flight disk binding completes.
        """
        binding = asyncio.create_task(asyncio.to_thread(cache.bind_config, fingerprint, managed=managed))
        try:
            await asyncio.shield(binding)
        except asyncio.CancelledError:
            await binding
            raise

    async def _reconcile_native_config() -> None:
        """Rebind native CLI cache when a local scan activates another source."""
        async with _config_lock:
            if app.state.galaxy_config_managed:
                return
            cfg_path, _, _ = _get_galaxy_config(allow_transition=True)
            fingerprint = await asyncio.to_thread(_source_fingerprint, app.state.galaxy_servers, cfg_path)
            if fingerprint == app.state.galaxy_source_fingerprint and app.state.galaxy_config_ready:
                return
            app.state.galaxy_config_ready = False
            cache.enabled = False
            app.state.galaxy_config_generation += 1
            _on_demand_wheels.clear()
            _prepare_fallback_wheels.clear()
            await _bind_cache_config(fingerprint, managed=False)
            app.state.galaxy_source_fingerprint = fingerprint
            app.state.galaxy_config_ready = True

    def _require_current_config(generation: int) -> None:
        if generation != app.state.galaxy_config_generation:
            raise HTTPException(status_code=409, detail="Galaxy configuration changed; retry the request")

    def _require_config_ready() -> None:
        """Reject collection access while source configuration is in transition.

        Raises:
            HTTPException: When source configuration is unavailable.
        """
        if not app.state.galaxy_config_ready:
            raise HTTPException(status_code=503, detail="Galaxy configuration is being restored")

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

    def _get_galaxy_config(
        *, allow_transition: bool = False
    ) -> tuple[Path | None, list[GalaxyServerConfig] | None, str | None]:
        """Read current Galaxy config from app state.

        ``None`` means no explicit server list (public Galaxy, or servers
        loaded from ``ansible.cfg`` when that file has an opinion).  An
        empty list is explicit configuration with no usable servers and
        must stay empty so callers fail closed.

        Args:
            allow_transition: Internal native reconciliation may recover an interrupted binding.

        Returns:
            tuple: (ansible_cfg_path, galaxy_servers, ansible_galaxy_bin).

        Raises:
            HTTPException: If a configuration transition is in progress.
        """
        if not allow_transition and not app.state.galaxy_config_ready:
            raise HTTPException(status_code=503, detail="Galaxy configuration is being restored")
        servers: list[GalaxyServerConfig] | None = app.state.galaxy_servers
        cfg_path = app.state.ansible_cfg_path
        if cfg_path is None:
            env_cfg = os.environ.get("ANSIBLE_CONFIG", "").strip()
            if env_cfg:
                candidate = Path(env_cfg).expanduser()
                if candidate.is_file():
                    cfg_path = candidate
        if servers is None and cfg_path is not None:
            servers = _load_servers_from_ansible_cfg(cfg_path)
        galaxy_bin = app.state.ansible_galaxy_bin
        return cfg_path, servers, galaxy_bin

    def _download_auth(
        cfg_path: Path | None,
        servers: list[GalaxyServerConfig] | None,
    ) -> tuple[Path | None, list[GalaxyServerConfig] | None]:
        """Choose one Galaxy auth source for ``ansible-galaxy``.

        An explicit server list, including an empty list, is authoritative
        and must not be replaced by ``ansible.cfg`` or the process environment.

        Args:
            cfg_path: Optional path to an existing ``ansible.cfg``.
            servers: Explicit server list, or ``None`` when unset.

        Returns:
            ``(ansible_cfg_path, servers)`` with at most one of them set.
        """
        if servers is not None:
            return None, servers
        return cfg_path, None

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

        desired_servers = [
            GalaxyServerConfig(
                name=s.name.strip(),
                url=s.url,
                token=s.token or None,
                auth_url=s.auth_url or None,
                validate_certs=s.validate_certs,
            )
            for s in body.servers
        ]
        async with _config_lock:
            changed = desired_servers != app.state.galaxy_servers or app.state.ansible_cfg_path is not None
            if changed or not app.state.galaxy_config_ready:
                app.state.galaxy_config_ready = False
                cache.enabled = False
                app.state.galaxy_config_generation += 1
                _on_demand_wheels.clear()
                _prepare_fallback_wheels.clear()
                fingerprint = await asyncio.to_thread(_source_fingerprint, desired_servers, None)
                await _bind_cache_config(fingerprint, managed=True)
                app.state.galaxy_servers = desired_servers
                app.state.ansible_cfg_path = None
                app.state.galaxy_config_managed = True
                app.state.galaxy_source_fingerprint = fingerprint
                app.state.galaxy_config_ready = True
        names = [s.name.strip() for s in body.servers]
        logger.info("Galaxy config updated: %d server(s): %s", len(names), ", ".join(names))
        return {"accepted": len(names), "servers": names}

    @app.post("/admin/prepare-collections")  # type: ignore[untyped-decorator]
    async def prepare_collections(request: Request, body: _PrepareCollectionsPayload) -> dict[str, Any]:
        """Resolve requested pins/latest versions with ``ansible-galaxy``.

        The engine calls this before pip resolves the local PEP 503 page. The
        CLI downloads the requested versions and their collection dependencies;
        converted wheels are then served from the shared proxy cache.

        Args:
            request: Admin request carrying the shared proxy token.
            body: Collection specs submitted by the Engine.

        Returns:
            Prepared wheel filenames and specs that could not be resolved.

        Raises:
            HTTPException: If auth fails, the specs are invalid, or config changes mid-download.
        """
        _require_admin_token(request)
        if not body.specs or len(body.specs) > 100:
            raise HTTPException(status_code=422, detail="specs must contain between 1 and 100 collections")
        if not app.state.galaxy_config_ready:
            raise HTTPException(status_code=503, detail="Galaxy configuration has not been restored yet")

        specs = [spec.strip() for spec in body.specs]
        if any(
            len(spec) > 255 or not re.fullmatch(r"[A-Za-z0-9_]+\.[A-Za-z0-9_]+(?::[A-Za-z0-9._*+!<>=,~-]+)?", spec)
            for spec in specs
        ):
            raise HTTPException(status_code=422, detail="Invalid collection spec")

        generation = app.state.galaxy_config_generation

        def _check_specs_from_disk(
            spec_list: list[str], handoff_snapshot: dict[str, tuple[float, bytes]]
        ) -> dict[str, bool]:
            memo: dict[str, bool] = {}
            return {spec: _cache_satisfies_spec(cache, handoff_snapshot, spec, _memo=memo) for spec in spec_list}

        async def _check_specs(spec_list: list[str]) -> dict[str, bool]:
            # Snapshot mutable app state on the event loop, then do wheel
            # listing, file reads, and ZIP metadata parsing in a worker thread.
            handoff_snapshot = dict(_on_demand_wheels)
            return await asyncio.to_thread(_check_specs_from_disk, spec_list, handoff_snapshot)

        satisfaction = await _check_specs(specs)
        _require_current_config(generation)
        unresolved = [spec for spec in specs if not satisfaction[spec]]
        # Serialize only requests for the same collection. Independent Hub
        # downloads, including requests already satisfied by cache, proceed
        # without waiting behind an unrelated slow download.
        lock_keys = sorted({spec.split(":", 1)[0].lower() for spec in unresolved})
        locks = [_prepare_locks.setdefault(key, asyncio.Lock()) for key in lock_keys]
        acquired: list[asyncio.Lock] = []
        try:
            for lock in locks:
                await lock.acquire()
                acquired.append(lock)
            _require_current_config(generation)
            # Only unresolved entries can have changed while waiting for their
            # collection locks. Recheck those once and reuse the result below.
            refreshed = await _check_specs(unresolved)
            _require_current_config(generation)
            satisfaction.update(refreshed)
            unresolved = [spec for spec in unresolved if not satisfaction[spec]]

            def _list_prepared_wheels() -> list[str]:
                return sorted(
                    {
                        wheel
                        for spec in specs
                        if satisfaction[spec]
                        for wheel in _list_cached_wheels(cache, *spec.split(":", 1)[0].split(".", 1))
                    }
                )

            prepared = await asyncio.to_thread(_list_prepared_wheels)
            _require_current_config(generation)
            failed: list[str] = []

            if unresolved:
                cfg_path, servers, galaxy_bin = _get_galaxy_config()
                cfg_for_download, servers_for_download = _download_auth(cfg_path, servers)
                with tempfile.TemporaryDirectory(prefix="apme-galaxy-prepare-") as tmp:
                    result = await download_collections(
                        unresolved,
                        Path(tmp),
                        ansible_cfg_path=cfg_for_download,
                        servers=servers_for_download,
                        ansible_galaxy_bin=galaxy_bin,
                        include_dependencies=True,
                    )
                    failed.extend(result.failed_specs)
                    for tarball in result.tarball_paths:
                        try:
                            data = await asyncio.to_thread(tarball.read_bytes)
                            whl_name, whl_data = await asyncio.to_thread(tarball_to_wheel, data)
                            # No await may occur between this generation check
                            # and publishing to disk or the in-memory handoff.
                            _require_current_config(generation)
                            if not _cache_downloaded_wheel(cache, whl_name, whl_data):
                                _remember_on_demand_wheel(_on_demand_wheels, whl_name, whl_data)
                            prepared.append(whl_name)
                        except Exception as exc:  # noqa: BLE001 — keep processing partial CLI results
                            logger.warning(
                                "collection_conversion_failed tarball=%s error_type=%s",
                                tarball.name,
                                type(exc).__name__,
                            )
                    _require_current_config(generation)

            if unresolved:
                refreshed = await _check_specs(unresolved)
                _require_current_config(generation)
                satisfaction.update(refreshed)
            for spec in unresolved:
                if not satisfaction[spec] and spec not in failed:
                    failed.append(spec)
            now = time.monotonic()
            for filename, expires_at in list(_prepare_fallback_wheels.items()):
                if expires_at <= now:
                    del _prepare_fallback_wheels[filename]
            for spec in failed:
                fqcn, separator, requested = spec.partition(":")
                if not separator or not requested or requested.startswith(("<", ">", "!", "=", "~", "*")):
                    continue
                namespace, collection = fqcn.split(".", 1)
                filename = wheel_filename(namespace, collection, requested)
                _prepare_fallback_wheels[filename] = now + _ON_DEMAND_WHEEL_TTL_S
            while len(_prepare_fallback_wheels) > 500:
                del _prepare_fallback_wheels[next(iter(_prepare_fallback_wheels))]
        finally:
            for lock in reversed(acquired):
                lock.release()

        return {"prepared": sorted(set(prepared)), "failed_specs": sorted(set(failed))}

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
        """PEP 503 project page listing wheels already prepared by the CLI.

        Engine prepares pinned requirements through the authenticated admin
        endpoint. Direct unpinned requests fall back to the CLI's latest
        resolution. The proxy does not enumerate Galaxy versions.

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
        _require_config_ready()

        try:
            namespace, name = python_to_fqcn(normalized)
        except ValueError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"Package {package_name!r} is not a valid Ansible collection name",
            ) from exc

        generation = app.state.galaxy_config_generation
        cached_wheel_set = set(_list_cached_wheels(cache, namespace, name))
        now = time.monotonic()
        for filename, expires_at in list(_prepare_fallback_wheels.items()):
            if expires_at <= now:
                del _prepare_fallback_wheels[filename]
            elif filename.startswith(f"ansible_collection_{namespace}_{name}-"):
                cached_wheel_set.add(filename)
        for filename, (expires_at, _data) in list(_on_demand_wheels.items()):
            if expires_at <= now:
                del _on_demand_wheels[filename]
            elif filename.startswith(f"ansible_collection_{namespace}_{name}-"):
                cached_wheel_set.add(filename)

        if not cached_wheel_set:
            lock_key = f"{namespace}.{name}:latest"
            lock = _download_locks.get(lock_key)
            if lock is None:
                lock = _download_locks.setdefault(lock_key, asyncio.Lock())
            async with lock:
                _require_current_config(generation)
                cached_wheel_set = set(_list_cached_wheels(cache, namespace, name))
                if not cached_wheel_set:
                    try:
                        cfg_path, servers_cfg, galaxy_bin = _get_galaxy_config()
                        cfg_for_download, servers_for_download = _download_auth(cfg_path, servers_cfg)
                        whl_name, whl_data = await _download_and_convert(
                            namespace,
                            name,
                            "",
                            ansible_cfg_path=cfg_for_download,
                            galaxy_servers=servers_for_download,
                            ansible_galaxy_bin=galaxy_bin,
                        )
                        _require_current_config(generation)
                        if not _cache_downloaded_wheel(cache, whl_name, whl_data):
                            _remember_on_demand_wheel(_on_demand_wheels, whl_name, whl_data)
                        logger.info("On-demand download for %s.%s: %s", namespace, name, whl_name)
                        cached_wheel_set = {whl_name}
                    except HTTPException:
                        raise
                    except Exception as exc:
                        logger.error(
                            "collection_download_failed operation=download "
                            "collection=%s.%s error_type=%s outcome=failed",
                            namespace,
                            name,
                            type(exc).__name__,
                            exc_info=True,
                        )
                        raise HTTPException(
                            status_code=502,
                            detail=f"Failed to download {namespace}.{name} via ansible-galaxy",
                        ) from exc

        links: list[str] = []

        for whl_name in sorted(cached_wheel_set):
            try:
                cached_wheel = cache.wheel_path(whl_name)
                whl_hash = sha256_file_hex(cached_wheel) if cached_wheel else ""
            except OSError:
                whl_hash = ""
            href = f"/wheels/{whl_name}"
            if whl_hash:
                href += f"#sha256={whl_hash}"
            links.append(f'<a href="{href}">{whl_name}</a>')

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
        _require_config_ready()

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

        cached = _read_cached_wheel(cache, filename)
        if cached is None:
            cached = _take_on_demand_wheel(_on_demand_wheels, filename)
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
            _require_config_ready()
            cached = _read_cached_wheel(cache, filename)
            if cached is None:
                cached = _take_on_demand_wheel(_on_demand_wheels, filename)
            if cached:
                logger.info("wheel_cache_hit_after_lock filename=%s size_bytes=%d", filename, len(cached))
                _record_serve("hit")
                return Response(
                    content=cached,
                    media_type="application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename={filename}"},
                )

            logger.info("wheel_cache_miss filename=%s", filename)
            generation = app.state.galaxy_config_generation

            try:
                cfg_path, servers, galaxy_bin = _get_galaxy_config()
                cfg_for_download, servers_for_download = _download_auth(cfg_path, servers)
                whl_name, whl_data = await _download_and_convert(
                    ns,
                    coll_name,
                    version,
                    ansible_cfg_path=cfg_for_download,
                    galaxy_servers=servers_for_download,
                    ansible_galaxy_bin=galaxy_bin,
                )
            except HTTPException:
                raise
            except Exception as exc:
                server_labels = [_safe_server_label(s.url) for s in servers] if servers else ["default"]
                logger.error(
                    "collection_download_failed operation=download collection=%s.%s "
                    "version=%s servers_tried=%s error_type=%s outcome=failed",
                    ns,
                    coll_name,
                    version,
                    ", ".join(server_labels),
                    type(exc).__name__,
                )
                _record_serve("miss", status="error")
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"Failed to download/convert {ns}.{coll_name} {version} "
                        f"via ansible-galaxy (servers tried: {', '.join(server_labels)})"
                    ),
                ) from exc

            _require_current_config(generation)
            _cache_downloaded_wheel(cache, whl_name, whl_data)

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
        if not app.state.galaxy_config_ready:
            raise HTTPException(status_code=503, detail="Galaxy configuration has not been restored yet")

        converted: list[str] = []
        failed: list[str] = []
        generation = app.state.galaxy_config_generation

        for tb in sorted(tarball_path.glob("*.tar.gz")):
            if tb.is_symlink() or not tb.is_file():
                logger.warning("Skipping non-regular tarball entry: %s", tb)
                failed.append(tb.name)
                continue
            try:
                tarball_data = await asyncio.to_thread(tb.read_bytes)
                whl_name, whl_data = await asyncio.to_thread(tarball_to_wheel, tarball_data)
                _require_current_config(generation)
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


def _load_servers_from_ansible_cfg(cfg_path: Path) -> list[GalaxyServerConfig] | None:
    """Parse Galaxy servers from an ``ansible.cfg`` file.

    This is used by the proxy's version-discovery path when no Gateway-pushed
    server list is present, such as local daemon mode where the Engine
    temporarily exposes a session-scoped ``ANSIBLE_CONFIG``.

    Args:
        cfg_path: Path to the config file.

    Returns:
        Ordered Galaxy server configs when ``server_list`` names usable
        servers.  An empty list when ``server_list`` is present but blank,
        names no usable servers, or the file cannot be parsed (fail-closed).
        ``None`` when the file has no ``[galaxy]`` section or no
        ``server_list`` option (no opinion → public Galaxy default).
    """
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(str(cfg_path), encoding="utf-8")
    except (configparser.Error, OSError):
        logger.debug("Failed to parse ansible.cfg for Galaxy servers: %s", cfg_path, exc_info=True)
        return []

    if not parser.has_section("galaxy"):
        return None

    if not parser.has_option("galaxy", "server_list"):
        return None

    raw_list = parser.get("galaxy", "server_list")
    if not raw_list.strip():
        return []

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
        try:
            validate_certs = read_server_validate_certs(parser, name)
        except ValueError:
            logger.warning("Invalid Galaxy TLS policy for server %r", name)
            return []
        servers.append(
            GalaxyServerConfig(
                name=name,
                url=url,
                token=token,
                auth_url=auth_url,
                validate_certs=validate_certs,
            )
        )
    return servers


def _read_cached_wheel(cache: ProxyCache, filename: str) -> bytes | None:
    """Read an artifact from cache, allowing Hub fallback on storage failure.

    Args:
        cache: Collection artifact cache.
        filename: Validated wheel filename.

    Returns:
        Cached bytes, or None when unavailable.
    """
    try:
        return cache.get_wheel(filename)
    except OSError as exc:
        logger.warning("wheel_cache_unavailable filename=%s error_type=%s", filename, type(exc).__name__)
        return None


def _cache_downloaded_wheel(cache: ProxyCache, filename: str, data: bytes) -> bool:
    """Cache an artifact without preventing a successful download from serving.

    Args:
        cache: Collection artifact cache.
        filename: Validated wheel filename.
        data: Converted wheel bytes.

    Returns:
        True when the disk cache accepted the wheel; False on a storage error.
    """
    try:
        cache.put_wheel(filename, data)
    except OSError as exc:
        logger.warning("wheel_cache_write_failed filename=%s error_type=%s", filename, type(exc).__name__)
        return False
    return True


def _remember_on_demand_wheel(store: dict[str, tuple[float, bytes]], filename: str, data: bytes) -> None:
    """Keep a bounded, short-lived in-memory wheel handoff after disk failure.

    Args:
        store: Per-app in-memory wheel handoff.
        filename: Wheel basename.
        data: Wheel bytes.
    """
    if len(data) > _ON_DEMAND_WHEEL_LIMIT_BYTES:
        return
    now = time.monotonic()
    for old_name, (expires_at, _old_data) in list(store.items()):
        if expires_at <= now:
            del store[old_name]
    while store and sum(len(item[1]) for item in store.values()) + len(data) > _ON_DEMAND_WHEEL_LIMIT_BYTES:
        del store[next(iter(store))]
    store[filename] = (now + _ON_DEMAND_WHEEL_TTL_S, data)


def _take_on_demand_wheel(store: dict[str, tuple[float, bytes]], filename: str) -> bytes | None:
    """Return and consume a live in-memory wheel handoff.

    Args:
        store: Per-app in-memory wheel handoff.
        filename: Wheel basename.

    Returns:
        Unexpired wheel bytes, or None when no handoff is available.
    """
    entry = store.pop(filename, None)
    if entry is None or entry[0] <= time.monotonic():
        return None
    return entry[1]


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
    if not cache.enabled:
        return []
    prefix = f"ansible_collection_{namespace}_{name}-"
    wheels: list[str] = []
    try:
        if cache.wheels_dir.is_dir():
            for whl in cache.wheels_dir.glob(f"{prefix}*.whl"):
                wheels.append(whl.name)
    except OSError as exc:
        logger.warning("wheel_cache_listing_failed collection=%s.%s error_type=%s", namespace, name, type(exc).__name__)
    return sorted(wheels)


def _cache_satisfies_spec(
    cache: ProxyCache,
    handoffs: dict[str, tuple[float, bytes]],
    spec: str,
    _visiting: set[str] | None = None,
    _memo: dict[str, bool] | None = None,
) -> bool:
    """Return whether cache has a matching wheel and its collection dependencies.

    Args:
        cache: Proxy's on-disk wheel cache.
        handoffs: Temporary in-memory wheels from failed disk writes.
        spec: Galaxy FQCN with an optional version constraint.
        _visiting: Specs already being checked to break dependency cycles.
        _memo: Completed top-level satisfaction results shared across a phase.

    Returns:
        Whether a matching wheel and its collection dependency closure are cached.
    """
    memo = {} if _memo is None else _memo
    if spec in memo:
        return memo[spec]
    memoize_result = _visiting is None
    fqcn, _, requested = spec.partition(":")
    try:
        namespace, name = fqcn.split(".", 1)
    except ValueError:
        if memoize_result:
            memo[spec] = False
        return False
    if not namespace or not name:
        if memoize_result:
            memo[spec] = False
        return False
    visiting = set() if _visiting is None else _visiting
    if spec in visiting:
        return True
    visiting = visiting | {spec}
    now = time.monotonic()
    filenames = set(_list_cached_wheels(cache, namespace, name))
    handoff_data = {
        filename: data
        for filename, (expires_at, data) in handoffs.items()
        if expires_at > now and filename.startswith(f"ansible_collection_{namespace}_{name}-")
    }
    filenames.update(handoff_data)
    if not requested or requested == "*":
        specifier = SpecifierSet()
    else:
        constraint = requested if requested.startswith(("<", ">", "!", "=", "~")) else f"=={requested}"
        try:
            specifier = SpecifierSet(constraint)
        except InvalidSpecifier:
            if memoize_result:
                memo[spec] = False
            return False

    candidates: list[tuple[Version, str]] = []
    for filename in filenames:
        try:
            _distribution, version, _build, _tags = parse_wheel_filename(filename)
        except InvalidWheelFilename:
            continue
        if specifier.contains(version, prereleases=True):
            candidates.append((version, filename))

    for _version, filename in sorted(candidates, reverse=True):
        data = handoff_data.get(filename)
        if data is None:
            try:
                data = cache.get_wheel(filename)
            except OSError:
                data = None
        if data is None:
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as wheel:
                metadata_path = next(path for path in wheel.namelist() if path.endswith(".dist-info/METADATA"))
                metadata = Parser().parsestr(wheel.read(metadata_path).decode("utf-8"))
        except (OSError, ValueError, KeyError, StopIteration, UnicodeError, zipfile.BadZipFile):
            # A legacy or corrupt cached wheel is not enough to prove its
            # dependency closure; let ansible-galaxy refresh it.
            continue

        dependencies_satisfied = True
        for raw_requirement in metadata.get_all("Requires-Dist", []):
            try:
                requirement = Requirement(raw_requirement)
            except InvalidRequirement:
                dependencies_satisfied = False
                break
            dep_package = normalize_pep503(requirement.name)
            if not dep_package.startswith("ansible-collection-"):
                continue
            try:
                dep_namespace, dep_name = python_to_fqcn(dep_package)
            except ValueError:
                dependencies_satisfied = False
                break
            dep_fqcn = f"{dep_namespace}.{dep_name}"
            dep_spec = f"{dep_fqcn}:{requirement.specifier}" if requirement.specifier else dep_fqcn
            if not _cache_satisfies_spec(cache, handoffs, dep_spec, visiting, memo):
                dependencies_satisfied = False
                break
        if dependencies_satisfied:
            if memoize_result:
                memo[spec] = True
            return True
    if memoize_result:
        memo[spec] = False
    return False


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
            server_labels = [_safe_server_label(s.url) for s in galaxy_servers] if galaxy_servers else ["default"]
            summary = download_error_summary(result.stderr)
            msg = f"Failed to download {spec} from configured Galaxy servers [{', '.join(server_labels)}]: {summary}"
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
