"""Build per-matrix venvs: ansible-core + collections installed from cache or proxy."""

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

_PROXY_ENV = "APME_GALAXY_PROXY_URL"


def _proxy_url() -> str | None:
    """Return the galaxy proxy URL if configured, else None.

    Returns:
        The proxy URL string, or None if not set.
    """
    return os.environ.get(_PROXY_ENV, "").strip() or None


def _spec_to_pip(spec: str) -> str:
    """Convert a collection spec to a pip package name.

    ``community.general:9.0.0`` → ``ansible-collection-community-general==9.0.0``
    ``ansible.posix``           → ``ansible-collection-ansible-posix``

    Args:
        spec: Collection specifier (namespace.collection or namespace.collection:version).

    Returns:
        pip-installable package specifier.
    """
    namespace, collection = _parse_collection_spec(spec)
    pkg = f"ansible-collection-{namespace}-{collection}"
    if ":" in spec:
        version = spec.split(":", 1)[1].strip()
        if version:
            pkg += f"=={version}"
    return pkg


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

    Includes the proxy URL when APME_GALAXY_PROXY_URL is set so that
    symlink-based and pip-based venvs coexist without collision.

    Args:
        ansible_core_version: Ansible core version string.
        collection_specs: List of collection specifiers.

    Returns:
        Hex digest string (first 16 chars) for cache key.
    """
    parts = [ansible_core_version] + sorted(s.strip() for s in collection_specs)
    url = _proxy_url()
    if url:
        parts.append(f"proxy={url}")
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


def _install_collections_via_proxy(
    pip_python: Path,
    collection_specs: list[str],
    proxy_url: str,
    use_uv: bool,
) -> None:
    """Install collections into the venv via the galaxy proxy (PEP 503).

    Converts each collection spec to a pip package name and installs using
    ``--extra-index-url`` pointed at the galaxy proxy.  The proxy fetches
    tarballs from Galaxy on demand, converts to wheels, and caches them.

    Python dependencies declared in collections' ``requirements.txt`` are
    included in the wheel metadata, so pip/uv resolves them automatically.

    Args:
        pip_python: Python interpreter inside the venv.
        collection_specs: Collection specifiers (e.g. ansible.posix, community.general:9.0.0).
        proxy_url: Base URL of the galaxy proxy (e.g. http://localhost:8765).
        use_uv: Whether to use uv for installation.

    Raises:
        subprocess.CalledProcessError: When the pip/uv install command fails.
    """
    simple_url = proxy_url.rstrip("/") + "/simple/"
    pip_specs = [_spec_to_pip(s) for s in collection_specs]

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

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(f"Warning: proxy collection install failed: {result.stderr or result.stdout}\n")
        sys.stderr.flush()
        raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)


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

    proxy = _proxy_url()
    if proxy and collection_specs:
        _install_collections_via_proxy(pip_python, collection_specs, proxy, use_uv)
    elif collection_specs:
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


def create_base_venv(
    venv_dir: Path,
    ansible_core_version: str,
    python_exe: str | None = None,
) -> None:
    """Create a virtual environment and install ansible-core into it.

    Unlike :func:`build_venv`, this writes into a caller-specified directory
    with no hash-based naming.  Intended for session-scoped venvs where
    the layout is ``sessions/<sid>/<version>/venv/``.

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


def install_collections_incremental(
    venv_dir: Path,
    collection_specs: list[str],
    cache_root: Path | None = None,
) -> None:
    """Install additional collections into an existing venv (additive only).

    Handles both the proxy path (``APME_GALAXY_PROXY_URL`` set — uses
    ``uv pip install --extra-index-url``) and the local-cache path
    (symlinks from the collection cache + ``ansible-builder introspect``
    for Python deps).

    Safe to call repeatedly with overlapping specs — already-installed
    packages are no-ops for pip/uv, and existing symlinks are updated.

    Args:
        venv_dir: Root of the virtualenv (must already exist with ansible-core).
        collection_specs: Collection specifiers to install (e.g. ``"ansible.posix"``).
        cache_root: Override for the collection cache root (symlink path only).

    Raises:
        FileNotFoundError: If a collection is not in cache (symlink path).
    """
    if not collection_specs:
        return

    pip_python = get_venv_python(venv_dir)
    use_uv = _uv_available()
    proxy = _proxy_url()

    if proxy:
        _install_collections_via_proxy(pip_python, collection_specs, proxy, use_uv)
    else:
        root = cache_root or get_cache_root()
        site = _venv_site_packages(venv_dir)
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
            dest.symlink_to(path.resolve())

        _install_collection_python_deps(venv_dir, site, pip_python, use_uv)
