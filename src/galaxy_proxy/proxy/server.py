"""PEP 503 Simple Repository API server for Ansible collections.

Serves Python wheels converted from Galaxy collection tarballs.  Tarballs
are obtained via ``ansible-galaxy collection download`` (ADR-045), not a
custom httpx client.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response

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
)
from galaxy_proxy.proxy.cache import ProxyCache
from galaxy_proxy.proxy.passthrough import PyPIPassthrough

logger = logging.getLogger(__name__)


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

    @app.get("/health")  # type: ignore[untyped-decorator]
    async def health() -> dict[str, str]:
        return {"status": "ok"}

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

        For collections, lists all cached wheel versions.  If no wheels are
        cached, triggers a download via ``ansible-galaxy`` for the latest
        version.

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
            if passthrough is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Package {package_name!r} is not an Ansible collection and passthrough is disabled",
                )
            html, status = await passthrough.fetch_project_page(normalized)
            return HTMLResponse(content=html, status_code=status)

        try:
            namespace, name = python_to_fqcn(normalized)
        except ValueError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"Package {package_name!r} is not a valid Ansible collection name",
            ) from exc

        cached_wheels = _list_cached_wheels(cache, namespace, name)

        if not cached_wheels:
            lock_key = f"{namespace}.{name}:latest"
            lock = _download_locks.get(lock_key)
            if lock is None:
                lock = _download_locks.setdefault(lock_key, asyncio.Lock())
            async with lock:
                cached_wheels = _list_cached_wheels(cache, namespace, name)
                if not cached_wheels:
                    try:
                        whl_name, whl_data = await _download_and_convert(
                            namespace,
                            name,
                            "",
                            ansible_cfg_path=ansible_cfg_path,
                            galaxy_servers=galaxy_servers,
                            ansible_galaxy_bin=ansible_galaxy_bin,
                        )
                        cache.put_wheel(whl_name, whl_data)
                        logger.info("On-demand download for %s.%s: %s", namespace, name, whl_name)
                        cached_wheels = [whl_name]
                    except Exception:
                        logger.warning(
                            "On-demand download failed for %s.%s — returning empty listing",
                            namespace,
                            name,
                            exc_info=True,
                        )

        links: list[str] = []
        for whl_name in cached_wheels:
            cached_wheel = cache.wheel_path(whl_name)
            whl_hash = sha256_file_hex(cached_wheel) if cached_wheel else ""
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

        cached = cache.get_wheel(filename)
        if cached:
            logger.info("Cache hit: %s", filename)
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

        lock_key = f"{ns}.{coll_name}:{version}"
        lock = _download_locks.get(lock_key)
        if lock is None:
            lock = _download_locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            cached = cache.get_wheel(filename)
            if cached:
                logger.info("Cache hit after lock: %s", filename)
                return Response(
                    content=cached,
                    media_type="application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename={filename}"},
                )

            logger.info(
                "Cache miss: %s — downloading %s.%s %s via ansible-galaxy",
                filename,
                ns,
                coll_name,
                version,
            )

            try:
                whl_name, whl_data = await _download_and_convert(
                    ns,
                    coll_name,
                    version,
                    ansible_cfg_path=ansible_cfg_path,
                    galaxy_servers=galaxy_servers,
                    ansible_galaxy_bin=ansible_galaxy_bin,
                )
            except Exception as exc:
                logger.exception("Failed to download/convert %s.%s %s", ns, coll_name, version)
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"Failed to download/convert {ns}.{coll_name} {version} via ansible-galaxy"
                        + (f": {exc}" if str(exc) else "")
                    ),
                ) from exc

            cache.put_wheel(whl_name, whl_data)
            logger.info("Converted and cached: %s (%d bytes)", whl_name, len(whl_data))

        return Response(
            content=whl_data,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename={whl_name}"},
        )

    @app.post("/convert-tarballs")  # type: ignore[untyped-decorator]
    async def convert_tarballs(tarball_dir: str) -> dict[str, list[str]]:
        """Convert all tarballs in a directory to wheels and cache them.

        This endpoint supports the flow where Primary sends collection specs
        and the proxy converts pre-downloaded tarballs to wheels.

        Args:
            tarball_dir: Path to directory containing ``.tar.gz`` files
                (resolved to absolute internally).

        Returns:
            Dict with ``converted`` (wheel filenames) and ``failed`` (tarball names).

        Raises:
            HTTPException: When the tarball directory does not exist.
        """
        raw_tarball_path = Path(tarball_dir)
        for component in (raw_tarball_path, *raw_tarball_path.parents):
            try:
                if component.is_symlink():
                    raise HTTPException(status_code=400, detail="Symlinks not allowed")
            except FileNotFoundError:
                break
        tarball_path = raw_tarball_path.resolve()
        if not tarball_path.is_dir():
            raise HTTPException(status_code=400, detail=f"Not a directory: {tarball_dir}")

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

    with tempfile.TemporaryDirectory(prefix="apme-galaxy-dl-") as tmp:
        download_dir = Path(tmp)

        result = await download_collections(
            [spec],
            download_dir,
            ansible_cfg_path=ansible_cfg_path,
            servers=galaxy_servers,
            ansible_galaxy_bin=ansible_galaxy_bin,
        )

        if result.failed_specs:
            msg = f"Failed to download {spec}: {result.stderr}"
            raise RuntimeError(msg)

        if not result.tarball_paths:
            msg = f"No tarball found after downloading {spec}"
            raise RuntimeError(msg)

        tarball_path = result.tarball_paths[0]
        tarball_data = await asyncio.to_thread(tarball_path.read_bytes)
        whl_name, whl_data = await asyncio.to_thread(tarball_to_wheel, tarball_data)
        return whl_name, whl_data
