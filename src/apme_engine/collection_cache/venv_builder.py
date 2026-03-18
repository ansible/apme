"""Build per-matrix venvs: ansible-core + collections symlinked from cache."""

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

from apme_engine.collection_cache.config import get_cache_root
from apme_engine.collection_cache.manager import (
    _parse_collection_spec,
    collection_path_in_cache,
)


def _uv_available() -> bool:
    """Check if uv is available on PATH.

    Returns:
        True if uv executable is found, False otherwise.
    """
    return shutil.which("uv") is not None


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


def _venv_key(ansible_core_version: str, collection_specs: list[str]) -> str:
    """Stable key for (ansible-core version, collection set) to reuse venvs.

    Args:
        ansible_core_version: Ansible core version string.
        collection_specs: List of collection specifiers.

    Returns:
        Hex digest string (first 16 chars) for cache key.
    """
    parts = [ansible_core_version] + sorted(s.strip() for s in collection_specs)
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _resolve_collection_path(
    spec: str,
    cache_root: Path,
) -> Path | None:
    """Resolve a collection spec to its path in the cache (galaxy first, then github).

    Args:
        spec: Collection specifier (namespace.collection or with version).
        cache_root: Root path of the collection cache.

    Returns:
        Path to collection if found, None otherwise.
    """
    namespace, collection = _parse_collection_spec(spec)
    path = collection_path_in_cache(namespace, collection, cache_root=cache_root, source="galaxy")
    if path is not None:
        return path
    return collection_path_in_cache(namespace, collection, cache_root=cache_root, source="github")


def _install_collection_python_deps(
    venv_root: Path,
    site_packages: Path,
    pip_python: Path,
    use_uv: bool,
) -> None:
    """Discover and install Python dependencies declared by collections.

    Uses ``ansible-builder introspect`` to scan all installed collections
    for Python deps (handles ``meta/execution-environment.yml`` and
    fallback ``requirements.txt``, filters out ansible-core/test tools,
    consolidates into one file).  Then installs via uv/pip.

    Failures are logged but do not abort the build — missing Python
    deps only affect rules that fully import module code (e.g. L059).

    Args:
        venv_root: Root of the ephemeral venv.
        site_packages: Path to the venv's site-packages directory.
        pip_python: Path to the venv's python executable.
        use_uv: Whether to use uv for installation.
    """
    discovered_reqs = venv_root / "discovered_requirements.txt"
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "ansible_builder",
                "introspect",
                str(site_packages),
                "--write-pip",
                str(discovered_reqs),
                "--sanitize",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        sys.stderr.write(f"Warning: ansible-builder introspect failed: {e}\n")
        sys.stderr.flush()
        return

    if not discovered_reqs.is_file() or not discovered_reqs.read_text().strip():
        return

    try:
        if use_uv:
            subprocess.run(
                ["uv", "pip", "install", "--python", str(pip_python), "-r", str(discovered_reqs)],
                check=True,
                capture_output=True,
                text=True,
            )
        else:
            subprocess.run(
                [str(pip_python), "-m", "pip", "install", "-r", str(discovered_reqs)],
                check=True,
                capture_output=True,
                text=True,
            )
    except subprocess.CalledProcessError as e:
        sys.stderr.write(f"Warning: failed to install collection Python deps: {e.stderr or e}\n")
        sys.stderr.flush()


def build_venv(
    ansible_core_version: str,
    collection_specs: list[str],
    cache_root: Path | None = None,
    venvs_root: Path | None = None,
    python_exe: str | None = None,
    symlink_collections: bool = True,
) -> Path:
    """Create or reuse a venv with ansible-core and collections from the cache.

    Collections are symlinked from the cache into the venv's site-packages
    ansible_collections tree so Ansible finds them on the path.

    Uses uv for venv creation and pip install when available.

    Args:
        ansible_core_version: e.g. "2.15.0".
        collection_specs: List of "namespace.collection" or "namespace.collection:version".
        cache_root: Collection cache root; uses get_cache_root() if None.
        venvs_root: Directory to create keyed venvs under; defaults to cache_root/venvs.
        python_exe: Python to use for venv (e.g. "python3.12"); None = default.
        symlink_collections: If True, symlink collections; if False, copy (read-only still).

    Returns:
        Path to the venv root (e.g. venvs_root/<key>/).

    Raises:
        FileNotFoundError: If a collection in collection_specs is not in the cache.

    """
    root = cache_root or get_cache_root()
    base = venvs_root or (root / "venvs")
    base.mkdir(parents=True, exist_ok=True)

    key = _venv_key(ansible_core_version, collection_specs)
    venv_root = base / key

    if venv_root.is_dir() and (venv_root / "pyvenv.cfg").is_file():
        # Reuse existing venv; caller can optionally verify with ansible-doc
        site = _venv_site_packages(venv_root)
        ac = site / "ansible_collections"
        if ac.is_dir():
            return venv_root
        # Partial venv (e.g. no collections yet); continue to add collections below

    # Create venv
    use_uv = _uv_available()
    if use_uv:
        cmd = ["uv", "venv", str(venv_root)]
        if python_exe:
            cmd.extend(["--python", python_exe])
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    else:
        cmd = [sys.executable, "-m", "venv", str(venv_root)]
        if python_exe:
            cmd = [python_exe, "-m", "venv", str(venv_root)]
        subprocess.run(cmd, check=True, capture_output=True, text=True)

    # Install ansible-core
    pip_python = venv_root / "bin" / "python"
    if os.name == "nt":
        pip_python = venv_root / "Scripts" / "python.exe"
    if use_uv:
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(pip_python),
                f"ansible-core=={ansible_core_version}",
            ],
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

    site = _venv_site_packages(venv_root)
    ac = site / "ansible_collections"
    ac.mkdir(parents=True, exist_ok=True)

    for spec in collection_specs:
        path = _resolve_collection_path(spec, root)
        if path is None:
            raise FileNotFoundError(
                f"Collection not in cache: {spec}. Pull it first (e.g. apme-scan cache pull-galaxy {spec})."
            )
        namespace, collection = _parse_collection_spec(spec)
        ns_dir = ac / namespace
        ns_dir.mkdir(parents=True, exist_ok=True)
        dest = ns_dir / collection
        if dest.exists():
            if dest.is_symlink():
                dest.unlink()
            else:
                shutil.rmtree(dest)
        if symlink_collections:
            dest.symlink_to(path.resolve())
        else:
            shutil.copytree(path, dest)

    if collection_specs:
        _install_collection_python_deps(venv_root, site, pip_python, use_uv)

    return venv_root


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
