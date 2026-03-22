"""PEP 503 Simple Repository API server for Ansible collections."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response

from galaxy_proxy.converter import tarball_to_wheel
from galaxy_proxy.galaxy_client import GalaxyClient, GalaxyServer
from galaxy_proxy.metadata import sha256_file
from galaxy_proxy.naming import (
    is_collection_package,
    normalize_pep503,
    python_to_fqcn,
    wheel_filename,
)
from galaxy_proxy.proxy.cache import ProxyCache
from galaxy_proxy.proxy.passthrough import PyPIPassthrough

logger = logging.getLogger(__name__)


def create_app(
    galaxy_url: str = "https://galaxy.ansible.com",
    galaxy_token: str | None = None,
    pypi_url: str = "https://pypi.org",
    cache_dir: Path | None = None,
    metadata_ttl: float = 600.0,
    enable_passthrough: bool = True,
    *,
    galaxy_servers: list[GalaxyServer] | None = None,
) -> FastAPI:
    """Create and configure the proxy FastAPI application.

    When ``galaxy_servers`` is provided, the proxy tries each server in order
    (like ``ansible.cfg``'s ``galaxy_server_list``).  Otherwise falls back to
    ``galaxy_url`` / ``galaxy_token`` for backward compatibility.

    Args:
        galaxy_url: Primary Galaxy API base URL when not using ``galaxy_servers``.
        galaxy_token: Optional bearer token for Galaxy authentication.
        pypi_url: Base URL for PyPI passthrough (non-collection packages).
        cache_dir: Optional cache root; defaults to XDG cache layout.
        metadata_ttl: Seconds before cached version metadata expires.
        enable_passthrough: Whether to forward non-collection packages to PyPI.
        galaxy_servers: Ordered list of Galaxy servers (ansible.cfg-style).

    Returns:
        Configured FastAPI application instance.
    """
    cache = ProxyCache(cache_dir=cache_dir, metadata_ttl=metadata_ttl)
    galaxy = GalaxyClient(
        galaxy_url=galaxy_url,
        token=galaxy_token,
        servers=galaxy_servers,
    )
    passthrough = PyPIPassthrough(pypi_url=pypi_url) if enable_passthrough else None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: ARG001
        yield
        await galaxy.close()
        if passthrough:
            await passthrough.close()

    app = FastAPI(title="Ansible Collection Proxy", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/simple/", response_class=HTMLResponse)
    async def root_index() -> str:
        """Root index page.

        For the PoC this returns a minimal page. pip doesn't need the root
        index when installing a specific package by name.

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

    @app.get("/simple/{package_name}/", response_class=HTMLResponse)
    async def project_page(package_name: str) -> HTMLResponse:
        """PEP 503 project page listing available versions.

        Args:
            package_name: Requested package name from the URL path.

        Returns:
            HTML Simple API response (collection listing or passthrough).

        Raises:
            HTTPException: When passthrough is disabled for a non-collection
                package, or Galaxy version listing fails.
        """
        normalized = normalize_pep503(package_name)

        if not is_collection_package(normalized):
            if passthrough is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Package {package_name!r} is not an Ansible collection and passthrough is disabled",
                )
            html, status = await passthrough.fetch_project_page(normalized)
            return HTMLResponse(content=html, status_code=status)

        namespace, name = python_to_fqcn(normalized)

        cached = cache.get_metadata(namespace, name)
        if cached:
            versions = cached.versions
        else:
            try:
                versions = await galaxy.list_versions(namespace, name)
            except Exception as exc:
                logger.exception("Failed to fetch versions for %s.%s", namespace, name)
                raise HTTPException(
                    status_code=502,
                    detail=f"Failed to fetch versions for {namespace}.{name} from Galaxy",
                ) from exc
            cache.put_metadata(namespace, name, versions)

        links: list[str] = []
        for version in versions:
            whl_name = wheel_filename(namespace, name, version)

            cached_wheel = cache.wheel_path(whl_name)
            whl_hash = sha256_file(cached_wheel) if cached_wheel else ""

            href = f"/wheels/{whl_name}"
            if whl_hash:
                href += f"#sha256={whl_hash}"

            links.append(f'<a href="{href}">{whl_name}</a>')

        html = "<!DOCTYPE html>\n<html><body>\n" + "\n".join(links) + "\n</body></html>\n"
        return HTMLResponse(content=html)

    @app.get("/wheels/{filename}")
    async def serve_wheel(filename: str) -> Response:
        """Serve a wheel file, converting from Galaxy tarball on cache miss.

        Args:
            filename: Requested wheel filename from the URL path.

        Returns:
            Binary wheel response with appropriate content headers.

        Raises:
            HTTPException: When the filename is invalid, namespace/name cannot
                be parsed, or Galaxy fetch fails.
        """
        if not filename.endswith(".whl") or "/" in filename or "\\" in filename or ".." in filename:
            raise HTTPException(status_code=404, detail=f"Invalid wheel filename: {filename}")

        cached = cache.get_wheel(filename)
        if cached:
            logger.info("Cache hit: %s", filename)
            return Response(
                content=cached,
                media_type="application/octet-stream",
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )

        parts = filename.replace(".whl", "").split("-")
        # ansible_collection_{ns}_{name}-{version}-py3-none-any
        if len(parts) < 5 or not parts[0].startswith("ansible_collection_"):
            raise HTTPException(status_code=404, detail=f"Invalid wheel filename: {filename}")

        prefix_parts = parts[0].removeprefix("ansible_collection_").split("_", 1)
        if len(prefix_parts) != 2:
            raise HTTPException(status_code=404, detail=f"Cannot parse namespace/name: {filename}")

        ns, coll_name = prefix_parts
        version = parts[1]

        logger.info(
            "Cache miss: %s — fetching %s.%s %s from Galaxy",
            filename,
            ns,
            coll_name,
            version,
        )

        try:
            _detail, tarball_data = await galaxy.get_version_and_download(ns, coll_name, version)
        except Exception as exc:
            logger.exception("Failed to fetch %s.%s %s from Galaxy", ns, coll_name, version)
            raise HTTPException(
                status_code=502,
                detail=f"Failed to fetch {ns}.{coll_name} {version} from Galaxy",
            ) from exc

        whl_name, whl_data = tarball_to_wheel(tarball_data)
        cache.put_wheel(whl_name, whl_data)
        logger.info("Converted and cached: %s (%d bytes)", whl_name, len(whl_data))

        return Response(
            content=whl_data,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename={whl_name}"},
        )

    return app
