"""Download collection tarballs via ``ansible-galaxy collection download``.

Delegates Galaxy authentication (SSO, token exchange, multi-server fallback)
to the authoritative ``ansible-galaxy`` CLI.  The proxy's responsibility
narrows to tarball-to-wheel conversion and PEP 503 serving (ADR-045).
"""

from __future__ import annotations

import asyncio
import configparser
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class GalaxyServerConfig:
    """Configuration for a single upstream Galaxy / Automation Hub server.

    Mirrors the per-server section in ``ansible.cfg``::

        [galaxy_server.my_hub]
        url = https://hub.example.com/api/galaxy/
        token = secret-token-here
        auth_url = https://sso.example.com/token  (SSO only)

    Attributes:
        name: Short label for logging and ansible.cfg section name.
        url: Base URL of the Galaxy or Automation Hub API.
        token: Optional API token for authentication.
        auth_url: Optional SSO/Keycloak token endpoint (for Automation Hub).
    """

    name: str
    url: str
    token: str | None = None
    auth_url: str | None = None


@dataclass
class DownloadResult:
    """Result of an ``ansible-galaxy collection download`` invocation.

    Attributes:
        tarball_paths: Paths to downloaded ``.tar.gz`` files.
        failed_specs: Collection specs that could not be downloaded.
        stderr: Combined stderr output from the subprocess.
    """

    tarball_paths: list[Path] = field(default_factory=list)
    failed_specs: list[str] = field(default_factory=list)
    stderr: str = ""


def write_temp_ansible_cfg(
    servers: list[GalaxyServerConfig],
    dest_dir: Path,
) -> Path:
    """Write a temporary ``ansible.cfg`` with Galaxy server sections.

    The generated config uses a ``[galaxy]`` section with ``server_list``
    pointing to per-server ``[galaxy_server.<name>]`` sections — the same
    format ``ansible-galaxy`` reads natively.

    Args:
        servers: Ordered list of Galaxy server configurations.
        dest_dir: Directory to write the config file into.

    Returns:
        Path to the written ``ansible.cfg``.
    """
    cfg = configparser.ConfigParser(interpolation=None)

    server_names = [s.name for s in servers]
    cfg.add_section("galaxy")
    cfg.set("galaxy", "server_list", ",".join(server_names))

    for srv in servers:
        section = f"galaxy_server.{srv.name}"
        cfg.add_section(section)
        cfg.set(section, "url", srv.url)
        if srv.token:
            cfg.set(section, "token", srv.token)
        if srv.auth_url:
            cfg.set(section, "auth_url", srv.auth_url)

    cfg_path = dest_dir / "ansible.cfg"
    fd = os.open(str(cfg_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        cfg.write(f)
    return cfg_path


def _find_tarballs(directory: Path) -> list[Path]:
    """Find all ``.tar.gz`` files in a directory (non-recursive).

    Args:
        directory: Directory to scan for tarballs.

    Returns:
        Sorted list of ``.tar.gz`` file paths.
    """
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.tar.gz"))


async def download_collections(
    collection_specs: list[str],
    download_dir: Path,
    *,
    ansible_cfg_path: Path | None = None,
    servers: list[GalaxyServerConfig] | None = None,
    ansible_galaxy_bin: str | None = None,
    timeout: float = 300.0,
) -> DownloadResult:
    """Download collection tarballs via ``ansible-galaxy collection download``.

    Either ``ansible_cfg_path`` (user's existing config) or ``servers``
    (programmatic config) can be provided.  When ``servers`` is set, a
    temporary ``ansible.cfg`` is written and pointed to via
    ``ANSIBLE_CONFIG``.

    Tarballs are written to ``download_dir``.  The caller is responsible
    for converting them to wheels afterward.

    Args:
        collection_specs: Galaxy collection specifiers
            (e.g. ``["community.general:>=9.0", "ansible.posix"]``).
        download_dir: Directory to download tarballs into.
        ansible_cfg_path: Path to an existing ``ansible.cfg``.
        servers: Galaxy server configs (generates a temp ansible.cfg).
        ansible_galaxy_bin: Override for the ``ansible-galaxy`` binary path.
        timeout: Subprocess timeout in seconds.

    Returns:
        DownloadResult with paths to downloaded tarballs and any failures.
    """
    if not collection_specs:
        return DownloadResult()

    download_dir.mkdir(parents=True, exist_ok=True)

    galaxy_bin = ansible_galaxy_bin or "ansible-galaxy"

    normalized_specs = []
    for spec in collection_specs:
        if ":" in spec:
            fqcn, version = spec.split(":", 1)
            normalized_specs.append(f"{fqcn.strip()}:{version.strip()}")
        else:
            normalized_specs.append(spec.strip())

    cmd = [
        galaxy_bin,
        "collection",
        "download",
        "--download-path",
        str(download_dir),
        "--no-deps",
        *normalized_specs,
    ]

    env = dict(os.environ)
    temp_cfg_dir = None

    try:
        if servers:
            temp_cfg_dir = Path(tempfile.mkdtemp(prefix="apme-galaxy-cfg-"))
            cfg_path = write_temp_ansible_cfg(servers, temp_cfg_dir)
            env["ANSIBLE_CONFIG"] = str(cfg_path)
        elif ansible_cfg_path:
            env["ANSIBLE_CONFIG"] = str(ansible_cfg_path)

        logger.debug(
            "Running: %s (ANSIBLE_CONFIG=%s)",
            " ".join(cmd),
            env.get("ANSIBLE_CONFIG", "<default>"),
        )

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        stdout_text = stdout_bytes.decode("utf-8", errors="replace")

        tarballs = _find_tarballs(download_dir)

        if process.returncode != 0:
            logger.warning(
                "ansible-galaxy collection download failed (rc=%d): %s",
                process.returncode,
                stderr_text or stdout_text,
            )
            downloaded_fqcns = _extract_fqcns_from_tarballs(tarballs)
            failed = [s for s in collection_specs if _spec_fqcn(s) not in downloaded_fqcns]
            return DownloadResult(
                tarball_paths=tarballs,
                failed_specs=failed,
                stderr=stderr_text,
            )

        logger.info(
            "Downloaded %d tarball(s) for %d collection(s)",
            len(tarballs),
            len(collection_specs),
        )
        return DownloadResult(tarball_paths=tarballs, stderr=stderr_text)

    except asyncio.TimeoutError:
        logger.error(
            "ansible-galaxy collection download timed out after %.0fs",
            timeout,
        )
        return DownloadResult(
            failed_specs=list(collection_specs),
            stderr=f"Timed out after {timeout}s",
        )
    except FileNotFoundError:
        logger.error("ansible-galaxy binary not found: %s", galaxy_bin)
        return DownloadResult(
            failed_specs=list(collection_specs),
            stderr=f"ansible-galaxy binary not found: {galaxy_bin}",
        )
    finally:
        if temp_cfg_dir and temp_cfg_dir.is_dir():
            import shutil

            shutil.rmtree(temp_cfg_dir, ignore_errors=True)


def download_collections_sync(
    collection_specs: list[str],
    download_dir: Path,
    *,
    ansible_cfg_path: Path | None = None,
    servers: list[GalaxyServerConfig] | None = None,
    ansible_galaxy_bin: str | None = None,
    timeout: float = 300.0,
) -> DownloadResult:
    """Synchronous wrapper around :func:`download_collections`.

    Intended for use in ``run_in_executor()`` contexts where an event loop
    is not available.

    Args:
        collection_specs: Galaxy collection specifiers.
        download_dir: Directory to download tarballs into.
        ansible_cfg_path: Path to an existing ``ansible.cfg``.
        servers: Galaxy server configs (generates a temp ansible.cfg).
        ansible_galaxy_bin: Override for the ``ansible-galaxy`` binary path.
        timeout: Subprocess timeout in seconds.

    Returns:
        DownloadResult with paths to downloaded tarballs and any failures.
    """
    return asyncio.run(
        download_collections(
            collection_specs,
            download_dir,
            ansible_cfg_path=ansible_cfg_path,
            servers=servers,
            ansible_galaxy_bin=ansible_galaxy_bin,
            timeout=timeout,
        )
    )


def _spec_fqcn(spec: str) -> str:
    """Extract the FQCN portion from a collection spec.

    Args:
        spec: Collection specifier like ``"community.general:>=9.0"``
            or ``"ansible.posix"``.

    Returns:
        The FQCN portion (before any ``:``) in lowercase.
    """
    return spec.split(":")[0].strip().lower()


def _extract_fqcns_from_tarballs(tarballs: list[Path]) -> set[str]:
    """Extract FQCNs from tarball filenames.

    Galaxy tarballs follow the naming pattern ``{namespace}-{name}-{version}.tar.gz``.

    Args:
        tarballs: Paths to tarball files.

    Returns:
        Set of lowercase FQCNs extracted from filenames.
    """
    fqcns: set[str] = set()
    for path in tarballs:
        stem = path.name.removesuffix(".tar.gz")
        parts = stem.rsplit("-", 1)
        if len(parts) == 2:
            ns_name = parts[0]
            ns_parts = ns_name.split("-", 1)
            if len(ns_parts) == 2:
                fqcns.add(f"{ns_parts[0]}.{ns_parts[1]}".lower())
    return fqcns


def convert_tarballs_in_dir(
    tarball_dir: Path,
    cache_dir: Path,
) -> list[tuple[str, Path]]:
    """Convert all tarballs in a directory to wheels.

    Args:
        tarball_dir: Directory containing ``.tar.gz`` files.
        cache_dir: Directory to write ``.whl`` files into.

    Returns:
        List of ``(wheel_filename, wheel_path)`` tuples for successfully
        converted tarballs.
    """
    from galaxy_proxy.converter import tarball_to_wheel

    cache_dir.mkdir(parents=True, exist_ok=True)
    results: list[tuple[str, Path]] = []

    for tarball_path in _find_tarballs(tarball_dir):
        try:
            tarball_data = tarball_path.read_bytes()
            whl_name, whl_data = tarball_to_wheel(tarball_data)
            whl_path = cache_dir / whl_name
            whl_path.write_bytes(whl_data)
            results.append((whl_name, whl_path))
            logger.info("Converted %s -> %s", tarball_path.name, whl_name)
        except Exception:
            logger.exception("Failed to convert tarball: %s", tarball_path.name)

    return results
