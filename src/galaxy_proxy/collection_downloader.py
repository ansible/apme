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

    Raises:
        ValueError: If any server name is empty or duplicated.
    """
    cfg = configparser.ConfigParser(interpolation=None)

    server_names = [s.name for s in servers]
    seen: set[str] = set()
    for name in server_names:
        if not name:
            msg = "GalaxyServerConfig.name must be non-empty"
            raise ValueError(msg)
        if name in seen:
            msg = f"Duplicate Galaxy server name {name!r} — pass a unique name= for each server"
            raise ValueError(msg)
        seen.add(name)

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
    (programmatic config) can be provided — not both.  When ``servers``
    is set, a temporary ``ansible.cfg`` is written and pointed to via
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

    Raises:
        ValueError: When both ``ansible_cfg_path`` and ``servers`` are provided.
    """
    if ansible_cfg_path and servers:
        msg = "ansible_cfg_path and servers are mutually exclusive"
        raise ValueError(msg)

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
    process: asyncio.subprocess.Process | None = None

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
            failed = _compute_failed_specs(collection_specs, tarballs)
            return DownloadResult(
                tarball_paths=tarballs,
                failed_specs=failed,
                stderr=stderr_text or stdout_text,
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
        if process is not None:
            try:
                process.kill()
                await process.wait()
            except ProcessLookupError:
                pass
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


def _compute_failed_specs(collection_specs: list[str], tarballs: list[Path]) -> list[str]:
    """Determine which specs were not downloaded by matching tarball prefixes.

    Builds the expected tarball prefix from each FQCN (``namespace-name-``)
    and checks whether any downloaded tarball filename starts with it.
    This avoids ambiguous reverse-parsing of tarball names.

    Args:
        collection_specs: Requested collection specifiers.
        tarballs: Paths to downloaded tarballs.

    Returns:
        List of specs that have no matching tarball.
    """
    tarball_names = [t.name.lower() for t in tarballs]
    failed: list[str] = []
    for spec in collection_specs:
        fqcn = _spec_fqcn(spec)
        ns, _, name = fqcn.partition(".")
        if not name:
            failed.append(spec)
            continue
        prefix = f"{ns}-{name}-".lower()
        if not any(tb.startswith(prefix) for tb in tarball_names):
            failed.append(spec)
    return failed


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
        if tarball_path.is_symlink() or not tarball_path.is_file():
            logger.warning("Skipping non-regular tarball: %s", tarball_path)
            continue
        try:
            tarball_data = tarball_path.read_bytes()
            whl_name, whl_data = tarball_to_wheel(tarball_data)
            safe_name = Path(whl_name)
            if safe_name.is_absolute() or safe_name.name != whl_name:
                logger.warning("Unsafe wheel name from %s: %r", tarball_path, whl_name)
                continue
            whl_path = cache_dir / whl_name
            fd, tmp = tempfile.mkstemp(dir=cache_dir, suffix=".tmp")
            tmp_path = Path(tmp)
            ok = False
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(whl_data)
                os.replace(tmp_path, whl_path)
                ok = True
            finally:
                if not ok:
                    tmp_path.unlink(missing_ok=True)
            results.append((whl_name, whl_path))
            logger.info("Converted %s -> %s", tarball_path.name, whl_name)
        except Exception:
            logger.exception("Failed to convert tarball: %s", tarball_path.name)

    return results
