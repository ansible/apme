"""Session-scoped venvs with multi-version layout and incremental installs.

Each session (identified by a client-provided ``session_id``) can hold
multiple venvs, one per ``ansible-core`` version — like tox matrix entries.
Collections are installed *incrementally* into the active core-version venv;
old core-version venvs are retained until TTL reaping.

Write authority / read-only consumers:
    The Primary orchestrator is the sole venv authority (calls ``acquire()``).
    Validators mount the sessions volume read-only and receive a ``venv_path``
    in ``ValidateRequest``.

Concurrency safety:
    Creation and mutation are serialised per session via ``fcntl.flock``
    on a ``.lock`` file inside the session directory.

Storage layout::

    $SESSIONS_ROOT/
        <session_id>/
            <core_version>/
                venv/             # the actual virtualenv
                meta.json         # installed_collections, timestamps
            session.json          # session-level metadata (created_at, last_used)
            .lock                 # flock target
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import shutil
import subprocess
import sys
import textwrap
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger("apme.venv")

_PROXY_ENV = "APME_GALAXY_PROXY_URL"


def get_data_root() -> Path:
    """Return the APME data root directory for session venv storage.

    Reads ``APME_COLLECTION_CACHE`` from the environment.  Falls back to
    ``~/.apme-data/collection-cache`` when unset.

    Returns:
        Path to the data root directory.
    """
    base = os.environ.get("APME_COLLECTION_CACHE", "").strip()
    if base:
        return Path(base).expanduser().resolve()
    return Path(os.path.expanduser("~/.apme-data/collection-cache")).resolve()


def _proxy_url() -> str | None:
    """Return the galaxy proxy URL if configured, else None.

    Returns:
        The proxy URL string, or None if not set.
    """
    return os.environ.get(_PROXY_ENV, "").strip() or None


def _uv_available() -> bool:
    """Check if uv is available on PATH.

    Returns:
        True if uv executable is found, False otherwise.
    """
    return shutil.which("uv") is not None


def get_venv_python(venv_root: Path) -> Path:
    """Return the python executable inside the venv.

    Args:
        venv_root: Root path of the virtual environment.

    Returns:
        Path to the python executable.

    Raises:
        FileNotFoundError: If venv has no python executable.
    """
    exe = venv_root / "Scripts" / "python.exe" if os.name == "nt" else venv_root / "bin" / "python"
    if not exe.is_file():
        raise FileNotFoundError(f"venv has no python: {venv_root}")
    return exe


def _venv_site_packages(venv_root: Path) -> Path:
    """Return site-packages path for a venv (e.g. venv/lib/python3.12/site-packages).

    Args:
        venv_root: Root path of the virtual environment.

    Returns:
        Path to site-packages directory.

    Raises:
        FileNotFoundError: If venv has no lib dir or pythonX.Y directory.
    """
    lib = venv_root / "lib"
    if not lib.is_dir():
        raise FileNotFoundError(f"venv has no lib dir: {venv_root}")
    py_dirs = list(lib.glob("python*"))
    if not py_dirs:
        raise FileNotFoundError(f"venv has no pythonX.Y in lib: {venv_root}")
    site = py_dirs[0] / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    return site


def _spec_to_pip(spec: str) -> str:
    """Convert a collection spec to a pip package name.

    ``community.general:9.0.0`` -> ``ansible-collection-community-general==9.0.0``
    ``ansible.posix``           -> ``ansible-collection-ansible-posix``

    Args:
        spec: Collection specifier (namespace.collection or namespace.collection:version).

    Returns:
        pip-installable package specifier.

    Raises:
        ValueError: If spec does not contain a dot (expected namespace.collection).
    """
    base = spec.split(":")[0].strip()
    if "." not in base:
        raise ValueError(f"Invalid collection spec (expected namespace.collection): {spec}")
    namespace, collection = base.split(".", 1)
    pkg = f"ansible-collection-{namespace}-{collection}"
    if ":" in spec:
        version = spec.split(":", 1)[1].strip()
        if version:
            pkg += f"=={version}"
    return pkg


def _run_pip_install(
    pip_python: Path,
    pip_specs: list[str],
    simple_url: str,
    use_uv: bool,
) -> subprocess.CompletedProcess[str]:
    """Run a single pip/uv install command and return the result.

    Args:
        pip_python: Python interpreter inside the venv.
        pip_specs: Pip package specifiers to install.
        simple_url: PEP 503 simple index URL.
        use_uv: Whether to use uv for installation.

    Returns:
        CompletedProcess with stdout/stderr captured.
    """
    if use_uv:
        cmd = [
            "uv",
            "pip",
            "install",
            "--python",
            str(pip_python),
            "--extra-index-url",
            simple_url,
            *pip_specs,
        ]
    else:
        cmd = [
            str(pip_python),
            "-m",
            "pip",
            "install",
            "--extra-index-url",
            simple_url,
            *pip_specs,
        ]
    return subprocess.run(cmd, capture_output=True, text=True)


def _install_collections_via_proxy(
    pip_python: Path,
    collection_specs: list[str],
    proxy_url: str,
    use_uv: bool,
) -> list[str]:
    """Install collections into the venv via the galaxy proxy (PEP 503).

    Attempts a bulk install first.  On failure, falls back to installing
    each collection individually so that one broken collection does not
    block all others.

    Args:
        pip_python: Python interpreter inside the venv.
        collection_specs: Collection specifiers to install.
        proxy_url: Base URL of the galaxy proxy.
        use_uv: Whether to use uv for installation.

    Returns:
        List of collection specs that failed to install (empty on full success).
    """
    simple_url = proxy_url.rstrip("/") + "/simple/"
    pip_specs = [_spec_to_pip(s) for s in collection_specs]

    result = _run_pip_install(pip_python, pip_specs, simple_url, use_uv)
    if result.returncode == 0:
        return []

    logger.warning(
        "Bulk collection install failed, falling back to individual installs: %s",
        result.stderr or result.stdout,
    )

    failed: list[str] = []
    for spec in collection_specs:
        pip_spec = _spec_to_pip(spec)
        individual = _run_pip_install(pip_python, [pip_spec], simple_url, use_uv)
        if individual.returncode != 0:
            logger.warning(
                "Collection install failed for %s: %s",
                spec,
                individual.stderr or individual.stdout,
            )
            failed.append(spec)
        else:
            logger.info("Collection installed successfully: %s", spec)

    return failed


def create_base_venv(
    venv_dir: Path,
    ansible_core_version: str,
    python_exe: str | None = None,
) -> None:
    """Create a virtual environment and install ansible-core into it.

    Args:
        venv_dir: Exact directory for the virtualenv (created if absent).
        ansible_core_version: Pip-compatible version, e.g. ``"2.17.0"``.
        python_exe: Python interpreter for ``uv venv --python`` (optional).
    """
    use_uv = _uv_available()
    if use_uv:
        cmd = ["uv", "venv", str(venv_dir)]
        if python_exe:
            cmd.extend(["--python", python_exe])
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    else:
        cmd = [sys.executable, "-m", "venv", str(venv_dir)]
        if python_exe:
            cmd = [python_exe, "-m", "venv", str(venv_dir)]
        subprocess.run(cmd, check=True, capture_output=True, text=True)

    pip_python = get_venv_python(venv_dir)
    if use_uv:
        subprocess.run(
            ["uv", "pip", "install", "--python", str(pip_python), f"ansible-core=={ansible_core_version}"],
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        subprocess.run(
            [str(pip_python), "-m", "pip", "install", f"ansible-core=={ansible_core_version}"],
            check=True,
            capture_output=True,
            text=True,
        )


def install_collections_incremental(
    venv_dir: Path,
    collection_specs: list[str],
) -> list[str]:
    """Install collections into an existing venv via the galaxy proxy.

    Uses ``APME_GALAXY_PROXY_URL`` to install collections as pip packages
    through the proxy's PEP 503 simple index.  Safe to call repeatedly
    with overlapping specs — already-installed packages are no-ops.

    Individual collection failures are non-fatal: the scan continues with
    whatever collections could be installed.  Failed specs are returned
    so callers can report them.

    Args:
        venv_dir: Root of the virtualenv (must already exist with ansible-core).
        collection_specs: Collection specifiers to install.

    Returns:
        List of collection specs that failed to install (empty on full success).

    Raises:
        RuntimeError: If APME_GALAXY_PROXY_URL is not configured.
    """
    if not collection_specs:
        return []

    proxy = _proxy_url()
    if not proxy:
        raise RuntimeError(
            "APME_GALAXY_PROXY_URL must be set. The galaxy proxy is the sole collection installation path."
        )

    pip_python = get_venv_python(venv_dir)
    use_uv = _uv_available()
    return _install_collections_via_proxy(pip_python, collection_specs, proxy, use_uv)


def list_installed_packages(venv_dir: Path) -> list[tuple[str, str, str, str]]:
    """List Python packages installed in a session venv.

    Uses ``importlib.metadata`` via the venv's Python to retrieve name,
    version, license, and author in a single subprocess call.

    Args:
        venv_dir: Root of the virtual environment.

    Returns:
        List of ``(name, version, license, supplier)`` tuples.  Empty list on error.
    """
    try:
        pip_python = get_venv_python(venv_dir)
    except FileNotFoundError:
        return []

    script = textwrap.dedent("""\
        import importlib.metadata, json, sys
        result = []
        for dist in importlib.metadata.distributions():
            meta = dist.metadata
            result.append({
                "name": meta["Name"] or "",
                "version": meta["Version"] or "",
                "license": meta.get("License", "") or "",
                "supplier": meta.get("Author", "") or meta.get("Author-email", "") or "",
            })
        result.sort(key=lambda e: (e.get("name", "").lower(), e.get("version", "")))
        json.dump(result, sys.stdout)
    """)
    cmd = [str(pip_python), "-c", script]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Failed to list packages in %s: %s", venv_dir, exc)
        return []

    if result.returncode != 0:
        logger.warning("Package listing failed in %s: %s", venv_dir, result.stderr)
        return []

    try:
        entries = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Failed to parse package list output from %s", venv_dir)
        return []

    return [
        (e["name"], e.get("version", ""), e.get("license", ""), e.get("supplier", ""))
        for e in entries
        if isinstance(e, dict) and "name" in e
    ]


def get_dependency_tree(venv_dir: Path) -> str:
    """Return the dependency tree for a session venv as raw text.

    Runs ``uv pip tree`` (preferred) or returns an empty string if
    uv is unavailable or the command fails.

    Args:
        venv_dir: Root of the virtual environment.

    Returns:
        Human-readable dependency tree, or ``""`` on error.
    """
    if not _uv_available():
        return ""

    try:
        pip_python = get_venv_python(venv_dir)
    except FileNotFoundError:
        return ""

    cmd = ["uv", "pip", "tree", "--python", str(pip_python)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Failed to get dependency tree in %s: %s", venv_dir, exc)
        return ""

    if result.returncode != 0:
        logger.warning("uv pip tree failed in %s: %s", venv_dir, result.stderr)
        return ""

    return result.stdout.strip()


def _read_collection_metadata(coll_dir: Path, namespace: str) -> tuple[str, str]:
    """Read license and supplier from a collection's metadata files.

    Tries ``MANIFEST.json`` first (installed collections), then falls back
    to ``galaxy.yml`` (source collections).

    Args:
        coll_dir: Path to the collection directory.
        namespace: Collection namespace (used as supplier fallback).

    Returns:
        ``(license, supplier)`` tuple.  Empty strings if no metadata found.
    """
    manifest_file = coll_dir / "MANIFEST.json"
    if manifest_file.is_file():
        try:
            info = json.loads(manifest_file.read_text()).get("collection_info", {})
            license_val = ", ".join(info.get("license", [])) or ""
            supplier = ", ".join(info.get("authors", [])) or info.get("namespace", namespace) or ""
            return (license_val, supplier)
        except (json.JSONDecodeError, OSError):
            pass

    galaxy_file = coll_dir / "galaxy.yml"
    if galaxy_file.is_file():
        try:
            import yaml  # noqa: PLC0415
        except ImportError:
            pass
        else:
            try:
                galaxy = yaml.safe_load(galaxy_file.read_text()) or {}
                raw_license = galaxy.get("license", [])
                license_val = ", ".join(raw_license) if isinstance(raw_license, list) else str(raw_license or "")
                supplier = ", ".join(galaxy.get("authors", [])) or galaxy.get("namespace", namespace) or ""
                return (license_val, supplier)
            except (OSError, yaml.YAMLError):
                pass

    return ("", "")


def list_installed_collections(venv_dir: Path) -> list[tuple[str, str, str, str]]:
    """List Ansible collections installed in a session venv.

    Runs ``ansible-galaxy collection list --format json -p /dev/null`` using
    the venv's Python.  The ``-p /dev/null`` suppresses user-level collection
    paths so only venv-installed collections are reported.  Reads
    ``MANIFEST.json`` / ``galaxy.yml`` for license and supplier metadata.

    Args:
        venv_dir: Root of the virtual environment.

    Returns:
        List of ``(fqcn, version, license, supplier)`` tuples.  Empty list on error.
    """
    try:
        pip_python = get_venv_python(venv_dir)
    except FileNotFoundError:
        return []

    cmd = [
        str(pip_python),
        "-m",
        "ansible",
        "galaxy",
        "collection",
        "list",
        "--format",
        "json",
        "-p",
        "/dev/null",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Failed to list collections in %s: %s", venv_dir, exc)
        return []

    if result.returncode != 0:
        logger.warning("ansible-galaxy collection list failed in %s: %s", venv_dir, result.stderr)
        return []

    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Failed to parse collection list output from %s", venv_dir)
        return []

    seen: set[str] = set()
    collections: list[tuple[str, str, str, str]] = []
    for install_path, entries in data.items():
        if not isinstance(entries, dict):
            continue
        for fqcn, info in entries.items():
            if fqcn.startswith("ansible._"):
                continue
            if fqcn in seen:
                continue
            seen.add(fqcn)
            version = info.get("version", "") if isinstance(info, dict) else ""
            if version == "*":
                version = ""
            ns, name = fqcn.split(".", 1)
            coll_dir = Path(install_path) / ns / name
            lic, supplier = _read_collection_metadata(coll_dir, ns)
            collections.append((fqcn, version, lic, supplier))
    return sorted(collections)


_DEFAULT_TTL = 3600
_re = __import__("re")
_SAFE_SESSION_RE = _re.compile(r"^[A-Za-z0-9_\-]+$")
_SAFE_VERSION_RE = _re.compile(r"^\d+\.\d+(\.\d+)?$")


def _sanitize_session_id(session_id: str) -> str:
    """Validate session_id is a safe filesystem name (no path traversal).

    Args:
        session_id: Raw session identifier from the client.

    Returns:
        The validated session_id (unchanged if safe).

    Raises:
        ValueError: If session_id contains unsafe characters.
    """
    if not session_id or not _SAFE_SESSION_RE.match(session_id):
        raise ValueError(f"Invalid session_id {session_id!r}: must be non-empty and contain only [A-Za-z0-9_-]")
    return session_id


def _normalize_version(raw: str) -> str:
    """Ensure a three-part pip version string (e.g. ``"2.17"`` -> ``"2.17.0"``).

    Args:
        raw: Version string with 2 or 3 parts (e.g. ``"2.20"`` or ``"2.20.1"``).

    Returns:
        Normalised ``X.Y.Z`` version string.

    Raises:
        ValueError: If raw is not a valid version (must match ``X.Y`` or ``X.Y.Z``).
    """
    if not _SAFE_VERSION_RE.match(raw.strip()):
        raise ValueError(f"Invalid ansible version {raw!r}: must match X.Y or X.Y.Z")
    parts = raw.strip().split(".")
    return ".".join(parts[:2]) + ".0" if len(parts) < 3 else raw.strip()


@dataclass
class VenvSession:
    """Metadata for a session-scoped venv (one per core version within a session).

    Attributes:
        session_id: Client-provided session identifier.
        venv_root: Path to the venv directory.
        ansible_version: Normalised ansible-core version installed.
        installed_collections: Collection specifiers actually present in the venv.
        failed_collections: Collection specifiers that could not be installed.
        created_at: Unix timestamp of venv creation.
        last_used_at: Unix timestamp of last acquire / touch.
    """

    session_id: str
    venv_root: Path
    ansible_version: str
    installed_collections: list[str] = field(default_factory=list)
    failed_collections: list[str] = field(default_factory=list)
    created_at: float = 0.0
    last_used_at: float = 0.0


class VenvSessionManager:
    """Manage session-scoped venvs with locking, TTL, and reaping.

    Sessions support multiple ``ansible-core`` versions (tox-style matrix).
    Collections are installed incrementally — only missing collections are
    added, never removed.  This supports use-cases like VSCode extensions
    where the workspace scope may grow between scans.
    """

    def __init__(
        self,
        sessions_root: Path | None = None,
        ttl_seconds: int = _DEFAULT_TTL,
    ) -> None:
        """Initialise the session manager.

        Args:
            sessions_root: Directory under which session directories are
                created.  Defaults to ``$CACHE_ROOT/sessions/``.
            ttl_seconds: How long an unused venv persists before reaping.
        """
        self._root = sessions_root or (get_data_root() / "sessions")
        self._root.mkdir(parents=True, exist_ok=True)
        self._ttl = ttl_seconds

    @property
    def sessions_root(self) -> Path:
        """Root directory containing all session directories."""
        return self._root

    def acquire(
        self,
        session_id: str,
        ansible_version: str,
        collection_specs: list[str] | None = None,
    ) -> VenvSession:
        """Get or create a session venv, installing only missing collections.

        If a venv for ``(session_id, ansible_version)`` exists and already
        contains all requested collections, it is reused instantly (warm hit).
        Otherwise only the *delta* (new collections) is installed.

        Individual collection install failures are non-fatal — the session
        records only successfully installed collections and logs warnings
        for any that failed.  The ``failed_collections`` attribute lists
        specs that could not be installed.

        New core versions create sibling venvs under the same session
        directory — existing ones are never destroyed.

        Args:
            session_id: Client-provided session identifier.
            ansible_version: e.g. ``"2.17.0"`` or ``"2.17"``.
            collection_specs: Collection specifiers to ensure are installed.

        Returns:
            A ``VenvSession`` with a ready-to-use venv.
        """
        session_id = _sanitize_session_id(session_id)
        specs = collection_specs or []
        pip_version = _normalize_version(ansible_version)

        session_dir = self._root / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        lock_path = session_dir / ".lock"
        version_dir = session_dir / pip_version
        meta_path = version_dir / "meta.json"
        venv_dir = version_dir / "venv"

        logger.info("Venv: acquiring session=%s core=%s", session_id, pip_version)
        t0 = time.monotonic()

        with open(lock_path, "w") as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                self._touch_session(session_dir)

                existing = self._read_version_meta(meta_path)
                if existing is not None and venv_dir.is_dir():
                    installed = set(existing.installed_collections)
                    missing = set(specs) - installed

                    if not missing:
                        existing.last_used_at = time.time()
                        self._write_version_meta(meta_path, existing)
                        dur = (time.monotonic() - t0) * 1000
                        logger.info(
                            "Venv: ready (%.0fms, warm hit, %d collections)",
                            dur,
                            len(existing.installed_collections),
                        )
                        return existing

                    logger.debug("Venv: installing %d missing collections", len(missing))
                    failed = install_collections_incremental(venv_dir, sorted(missing))
                    succeeded = set(specs) - set(failed)
                    existing.installed_collections = sorted(installed | succeeded)
                    existing.failed_collections = sorted(failed)
                    existing.last_used_at = time.time()
                    self._write_version_meta(meta_path, existing)
                    dur = (time.monotonic() - t0) * 1000
                    if failed:
                        logger.warning(
                            "Venv: ready with warnings (%.0fms, %d collections installed, %d failed: %s)",
                            dur,
                            len(existing.installed_collections),
                            len(failed),
                            ", ".join(failed),
                        )
                    else:
                        logger.info(
                            "Venv: ready (%.0fms, incremental, %d collections)",
                            dur,
                            len(existing.installed_collections),
                        )
                    return existing

                version_dir.mkdir(parents=True, exist_ok=True)
                if venv_dir.is_dir():
                    shutil.rmtree(venv_dir)

                logger.info("Venv: cold start — creating venv core=%s", pip_version)
                create_base_venv(venv_dir, pip_version)
                failed = []
                if specs:
                    logger.debug("Venv: installing %d collections", len(specs))
                    failed = install_collections_incremental(venv_dir, specs)

                succeeded_specs = sorted(set(specs) - set(failed))
                now = time.time()
                session = VenvSession(
                    session_id=session_id,
                    venv_root=venv_dir,
                    ansible_version=pip_version,
                    installed_collections=succeeded_specs,
                    failed_collections=sorted(failed),
                    created_at=now,
                    last_used_at=now,
                )
                self._write_version_meta(meta_path, session)
                dur = (time.monotonic() - t0) * 1000
                if failed:
                    logger.warning(
                        "Venv: ready with warnings (%.0fms, cold start, %d collections installed, %d failed: %s)",
                        dur,
                        len(succeeded_specs),
                        len(failed),
                        ", ".join(failed),
                    )
                else:
                    logger.info(
                        "Venv: ready (%.0fms, cold start, %d collections)",
                        dur,
                        len(succeeded_specs),
                    )
                return session
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)

    def touch(self, session_id: str) -> bool:
        """Update ``last_used_at`` on all venvs in the session to prevent expiry.

        Args:
            session_id: Session to touch.

        Returns:
            True if the session directory exists, False otherwise.
        """
        session_id = _sanitize_session_id(session_id)
        session_dir = self._root / session_id
        if not session_dir.is_dir():
            return False
        now = time.time()
        for ver_dir in session_dir.iterdir():
            if not ver_dir.is_dir() or ver_dir.name.startswith("."):
                continue
            meta_path = ver_dir / "meta.json"
            meta = self._read_version_meta(meta_path)
            if meta is not None:
                meta.last_used_at = now
                self._write_version_meta(meta_path, meta)
        self._touch_session(session_dir)
        return True

    def release(self, session_id: str) -> bool:
        """No-op for named sessions — TTL handles cleanup.

        Args:
            session_id: Session to release.

        Returns:
            True if the session directory exists, False otherwise.
        """
        session_id = _sanitize_session_id(session_id)
        return (self._root / session_id).is_dir()

    def get(
        self,
        session_id: str,
        ansible_version: str | None = None,
    ) -> VenvSession | None:
        """Look up a session by ID and optional core version.

        Without ``ansible_version``, returns the most recently used venv
        across all core versions in the session.

        Args:
            session_id: Session to look up.
            ansible_version: Optional core version to narrow the lookup.

        Returns:
            The matching ``VenvSession`` or ``None``.
        """
        session_id = _sanitize_session_id(session_id)
        session_dir = self._root / session_id
        if not session_dir.is_dir():
            return None

        if ansible_version:
            pip_version = _normalize_version(ansible_version)
            return self._read_version_meta(session_dir / pip_version / "meta.json")

        best: VenvSession | None = None
        for child in session_dir.iterdir():
            if not child.is_dir() or child.name.startswith("."):
                continue
            meta = self._read_version_meta(child / "meta.json")
            if meta and (best is None or meta.last_used_at > best.last_used_at):
                best = meta
        return best

    def list_sessions(self) -> list[VenvSession]:
        """List all session venvs across all sessions and core versions.

        Returns:
            List of ``VenvSession`` objects sorted by ``last_used_at`` descending.
        """
        sessions: list[VenvSession] = []
        if not self._root.is_dir():
            return sessions
        for sid_dir in self._root.iterdir():
            if not sid_dir.is_dir():
                continue
            for ver_dir in sid_dir.iterdir():
                if not ver_dir.is_dir() or ver_dir.name.startswith("."):
                    continue
                meta = self._read_version_meta(ver_dir / "meta.json")
                if meta is not None:
                    sessions.append(meta)
        sessions.sort(key=lambda s: s.last_used_at, reverse=True)
        return sessions

    def delete(self, session_id: str) -> bool:
        """Forcefully delete an entire session and all its core-version venvs.

        Args:
            session_id: Session to delete.

        Returns:
            True if the session directory existed and was removed.
        """
        session_id = _sanitize_session_id(session_id)
        session_dir = self._root / session_id
        if not session_dir.is_dir():
            return False
        shutil.rmtree(session_dir, ignore_errors=True)
        return True

    def reap_expired(self) -> int:
        """Delete individual core-version venvs past their TTL.

        Each core-version venv inside a session can expire independently.
        If all venvs under a session are reaped, the session directory is
        removed as well.

        Returns:
            Count of individual venvs deleted.
        """
        now = time.time()
        reaped = 0
        if not self._root.is_dir():
            return reaped

        for sid_dir in self._root.iterdir():
            if not sid_dir.is_dir():
                continue
            versions_remain = False
            for ver_dir in list(sid_dir.iterdir()):
                if not ver_dir.is_dir() or ver_dir.name.startswith("."):
                    continue
                if ver_dir.name == "session.json":
                    continue
                meta = self._read_version_meta(ver_dir / "meta.json")
                if meta is None:
                    shutil.rmtree(ver_dir, ignore_errors=True)
                    continue
                if now - meta.last_used_at > self._ttl:
                    shutil.rmtree(ver_dir, ignore_errors=True)
                    reaped += 1
                else:
                    versions_remain = True
            if not versions_remain:
                shutil.rmtree(sid_dir, ignore_errors=True)
        return reaped

    @staticmethod
    def _touch_session(session_dir: Path) -> None:
        """Update session-level metadata (``session.json``).

        Args:
            session_dir: Path to the session directory.
        """
        session_path = session_dir / "session.json"
        now = time.time()
        if session_path.is_file():
            try:
                data = json.loads(session_path.read_text(encoding="utf-8"))
                data["last_used_at"] = now
            except (json.JSONDecodeError, KeyError):
                data = {"created_at": now, "last_used_at": now}
        else:
            data = {"created_at": now, "last_used_at": now}
        tmp = session_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, session_path)

    @staticmethod
    def _read_version_meta(path: Path) -> VenvSession | None:
        """Read per-version metadata from JSON file.

        Args:
            path: Path to ``meta.json``.

        Returns:
            Parsed ``VenvSession`` or ``None`` if file is missing/corrupt.
        """
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return VenvSession(
                session_id=data["session_id"],
                venv_root=Path(data["venv_root"]),
                ansible_version=data["ansible_version"],
                installed_collections=data.get("installed_collections", []),
                failed_collections=data.get("failed_collections", []),
                created_at=data.get("created_at", 0.0),
                last_used_at=data.get("last_used_at", 0.0),
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    @staticmethod
    def _write_version_meta(path: Path, session: VenvSession) -> None:
        """Write per-version metadata to JSON atomically.

        Args:
            path: Path to ``meta.json``.
            session: Session to serialise.
        """
        data = asdict(session)
        data["venv_root"] = str(session.venv_root)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
