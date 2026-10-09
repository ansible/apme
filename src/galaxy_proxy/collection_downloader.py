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
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


def read_server_validate_certs(parser: configparser.ConfigParser, name: str) -> bool | None:
    """Resolve native TLS policy with environment precedence and Ansible booleans.

    Args:
        parser: Parsed ansible.cfg configuration.
        name: Galaxy server identifier.

    Returns:
        Explicit or inherited TLS verification policy, or None for CLI defaults.

    Raises:
        ValueError: When a configured TLS boolean is invalid.
    """  # noqa: DOC502 -- nested boolean parser raises

    def parse(value: str, setting: str) -> bool:
        normalized = value.strip().lower()
        if normalized in {"1", "yes", "y", "true", "t", "on"}:
            return True
        if normalized in {"0", "no", "n", "false", "f", "off"}:
            return False
        raise ValueError(f"Invalid Galaxy TLS boolean for {setting}")

    per_server = os.environ.get(f"ANSIBLE_GALAXY_SERVER_{name.upper()}_VALIDATE_CERTS")
    if per_server is not None:
        return parse(per_server, f"ANSIBLE_GALAXY_SERVER_{name.upper()}_VALIDATE_CERTS")
    section = f"galaxy_server.{name}"
    if parser.has_option(section, "validate_certs"):
        return parse(parser.get(section, "validate_certs"), f"[{section}] validate_certs")
    global_override = os.environ.get("ANSIBLE_GALAXY_IGNORE")
    if global_override is not None:
        return not parse(global_override, "ANSIBLE_GALAXY_IGNORE")
    if parser.has_option("galaxy", "ignore_certs"):
        return not parse(parser.get("galaxy", "ignore_certs"), "[galaxy] ignore_certs")
    return None


def _safe_server_label(raw_url: str) -> str:
    """Return an upstream server label without credentials or URL details.

    Args:
        raw_url: Upstream server URL.

    Returns:
        Host and port label suitable for operational logs.
    """
    parsed = urlsplit(raw_url)
    if not parsed.hostname:
        return "unknown"
    try:
        return f"{parsed.hostname}:{parsed.port}" if parsed.port else parsed.hostname
    except ValueError:
        return "unknown"


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
        validate_certs: Per-server TLS verification, or None to inherit the CLI default.
    """

    name: str
    url: str
    token: str | None = None
    auth_url: str | None = None
    validate_certs: bool | None = None


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
    format ``ansible-galaxy`` reads natively. TLS certificates are verified
    by default; an explicit ``ANSIBLE_GALAXY_IGNORE=true`` opts out.

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
    cfg.set("galaxy", "ignore_certs", "false")

    for srv in servers:
        section = f"galaxy_server.{srv.name}"
        cfg.add_section(section)
        cfg.set(section, "url", srv.url)
        if srv.token:
            cfg.set(section, "token", srv.token)
        if srv.auth_url:
            cfg.set(section, "auth_url", srv.auth_url)
        if srv.validate_certs is not None:
            cfg.set(section, "validate_certs", str(srv.validate_certs).lower())

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


def _inject_galaxy_env(env: dict[str, str], servers: list[GalaxyServerConfig]) -> None:
    """Set ``ANSIBLE_GALAXY_SERVER_*`` env vars for ``ansible-galaxy``.

    ``ansible-galaxy`` reads per-server config from env vars of the form
    ``ANSIBLE_GALAXY_SERVER_{NAME}_{KEY}`` (e.g.
    ``ANSIBLE_GALAXY_SERVER_CERTIFIED_URL``).  This avoids writing a temp
    ``ansible.cfg`` and is the preferred injection path (ADR-045).

    Args:
        env: Mutable env dict for the subprocess.
        servers: Galaxy server configurations to inject.

    Raises:
        ValueError: If any server has an empty name, a name with invalid
            characters, or if duplicate names exist.
    """
    _NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
    seen: set[str] = set()
    for s in servers:
        name = s.name.strip() if s.name else ""
        if not name:
            msg = "Galaxy server name must not be empty"
            raise ValueError(msg)
        if not _NAME_RE.match(name):
            msg = f"Galaxy server name contains invalid characters: {s.name!r}"
            raise ValueError(msg)
        upper = name.upper()
        if upper in seen:
            msg = f"Duplicate Galaxy server name: {s.name!r}"
            raise ValueError(msg)
        seen.add(upper)

    names = [s.name.strip() for s in servers]
    env["ANSIBLE_GALAXY_SERVER_LIST"] = ",".join(names)
    for s in servers:
        prefix = f"ANSIBLE_GALAXY_SERVER_{s.name.strip().upper()}_"
        env[f"{prefix}URL"] = s.url
        if s.token:
            env[f"{prefix}TOKEN"] = s.token
        if s.auth_url:
            env[f"{prefix}AUTH_URL"] = s.auth_url
        if s.validate_certs is not None:
            env[f"{prefix}VALIDATE_CERTS"] = str(s.validate_certs).lower()
        else:
            env.pop(f"{prefix}VALIDATE_CERTS", None)


def download_error_summary(stderr: str) -> str:
    """Classify a CLI failure without exposing credentials or response bodies.

    Args:
        stderr: Captured CLI diagnostics (never returned verbatim).

    Returns:
        A fixed, safe operational explanation.
    """
    message = stderr.lower()
    if "certificate_verify_failed" in message or "certificate verify failed" in message:
        return "TLS certificate verification failed; configure Hub CA trust or an explicit TLS opt-out"
    if (
        re.search(r"\bhttp(?:\s+error|\s+code|\s+status(?:\s+code)?)?\s*[:=]?\s*(?:401|403)\b", message)
        or "unauthorized" in message
        or "forbidden" in message
    ):
        return "Configured Galaxy servers rejected authentication or permissions"
    if "could not satisfy" in message or "failed to resolve" in message:
        return "Configured Galaxy servers could not resolve the required collection version or dependencies"
    if "timed out" in message:
        return "Configured Galaxy server download timed out"
    return "Collection download failed; check configured Galaxy server connectivity and availability"


def _default_ansible_galaxy_bin() -> str:
    """Resolve the best ``ansible-galaxy`` executable for this process.

    Checks PATH first via ``shutil.which``, then falls back to the active
    interpreter's sibling scripts directory (useful in tox/venv contexts
    where PATH may be minimal), and finally returns the bare name for
    last-resort PATH lookup.

    Returns:
        Executable path or the literal ``"ansible-galaxy"`` for PATH lookup.
    """
    if resolved := shutil.which("ansible-galaxy"):
        return resolved

    script_name = "ansible-galaxy.exe" if os.name == "nt" else "ansible-galaxy"
    candidate = Path(sys.executable).resolve().parent / script_name
    if candidate.is_file():
        return str(candidate)

    return "ansible-galaxy"


async def download_collections(
    collection_specs: list[str],
    download_dir: Path,
    *,
    ansible_cfg_path: Path | None = None,
    servers: list[GalaxyServerConfig] | None = None,
    ansible_galaxy_bin: str | None = None,
    timeout: float = 300.0,
    include_dependencies: bool = False,
) -> DownloadResult:
    """Download collection tarballs via ``ansible-galaxy collection download``.

    Either ``ansible_cfg_path`` (user's existing config) or ``servers``
    (programmatic config) can be provided — not both.  A non-empty
    ``servers`` list injects per-server ``ANSIBLE_GALAXY_SERVER_*`` env
    vars.  An empty list fails closed without starting ``ansible-galaxy``.
    When neither is provided, ``ansible-galaxy`` inherits the container's
    environment (which may already have Galaxy server env vars set via
    pod configuration).

    Args:
        collection_specs: Galaxy collection specifiers
            (e.g. ``["community.general:>=9.0", "ansible.posix"]``).
        download_dir: Directory to download tarballs into.
        ansible_cfg_path: Path to an existing ``ansible.cfg``.
        servers: Galaxy server configs (injected as env vars).  ``[]`` fails
            closed.  ``None`` leaves server discovery to ``ansible.cfg`` or
            the process environment.
        ansible_galaxy_bin: Override for the ``ansible-galaxy`` binary path.
        timeout: Subprocess timeout in seconds.
        include_dependencies: Whether ``ansible-galaxy`` should also download
            dependencies declared by the requested collections.

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

    if servers is not None and not servers:
        return DownloadResult(
            failed_specs=list(collection_specs),
            stderr="No configured Galaxy servers",
        )

    download_dir.mkdir(parents=True, exist_ok=True)

    galaxy_bin = ansible_galaxy_bin or _default_ansible_galaxy_bin()
    logger.info(
        "galaxy_backend_call operation=download servers=%s collections=%s",
        ",".join(f"{server.name}@{_safe_server_label(server.url)}" for server in servers or []),
        ",".join(collection_specs),
    )

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
    ]
    if not include_dependencies:
        cmd.append("--no-deps")
    cmd.extend(normalized_specs)

    env = dict(os.environ)
    process: asyncio.subprocess.Process | None = None
    temp_cfg_dir: Path | None = None
    started = time.perf_counter()
    status = "error"

    try:
        # Env vars outrank INI, so only inject when the caller did not supply
        # ansible.cfg (their galaxy.ignore_certs setting must win).
        if ansible_cfg_path is None:
            env.setdefault("ANSIBLE_GALAXY_IGNORE", "false")
        if servers:
            try:
                _inject_galaxy_env(env, servers)
            except ValueError:
                for key in list(env):
                    if key == "ANSIBLE_GALAXY_SERVER_LIST" or key.startswith("ANSIBLE_GALAXY_SERVER_"):
                        env.pop(key, None)
                temp_cfg_dir = Path(tempfile.mkdtemp(prefix="apme-galaxy-proxy-"))
                env["ANSIBLE_CONFIG"] = str(write_temp_ansible_cfg(servers, temp_cfg_dir))
                logger.info("Falling back to temp ansible.cfg for Galaxy config because server names are not env-safe")
        elif ansible_cfg_path:
            env["ANSIBLE_CONFIG"] = str(ansible_cfg_path)

        logger.debug(
            "Running: %s (galaxy_servers=%s, ANSIBLE_CONFIG=%s)",
            " ".join(cmd),
            env.get("ANSIBLE_GALAXY_SERVER_LIST", "<none>"),
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
                download_error_summary(stderr_text or stdout_text),
            )
            failed = _compute_failed_specs(collection_specs, tarballs)
            return DownloadResult(
                tarball_paths=tarballs,
                failed_specs=failed,
                stderr=stderr_text or stdout_text,
            )

        logger.info("galaxy_backend_response operation=download status=200 tarballs=%d", len(tarballs))
        status = "ok"
        return DownloadResult(tarball_paths=tarballs, stderr=stderr_text)

    except TimeoutError:
        status = "timeout"
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
        logger.info(
            "galaxy_download_result collections=%d tarballs=%d status=%s duration_ms=%.1f size_bytes=%d",
            len(collection_specs),
            len(_find_tarballs(download_dir)),
            status,
            (time.perf_counter() - started) * 1000,
            sum(path.stat().st_size for path in _find_tarballs(download_dir) if path.is_file()),
        )
        if temp_cfg_dir is not None:
            shutil.rmtree(temp_cfg_dir, ignore_errors=True)
        try:
            from apme_engine.observability import record_galaxy_fetch

            record_galaxy_fetch(
                time.perf_counter() - started,
                operation="download",
                status=status,
                collections_requested=len(collection_specs),
            )
        except Exception:  # noqa: BLE001 — never fail downloads for metrics
            logger.debug("Failed to record Galaxy download metrics", exc_info=True)


def download_collections_sync(
    collection_specs: list[str],
    download_dir: Path,
    *,
    ansible_cfg_path: Path | None = None,
    servers: list[GalaxyServerConfig] | None = None,
    ansible_galaxy_bin: str | None = None,
    timeout: float = 300.0,
    include_dependencies: bool = False,
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
        include_dependencies: Whether to download collection dependencies.

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
            include_dependencies=include_dependencies,
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
