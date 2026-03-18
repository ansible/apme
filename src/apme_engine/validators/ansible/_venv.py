"""Venv resolution and collection environment helpers for ansible validator rules.

Venvs are ephemeral — created on demand via ``build_venv`` and cached by UV
wheels so subsequent builds are near-instant.  Same code path in containers
(UV cache pre-warmed at image build) and local developer machines.
"""

import sys
from pathlib import Path

SUPPORTED_VERSIONS = ["2.18", "2.19", "2.20"]
DEFAULT_VERSION = "2.20"


def resolve_venv_root(version: str) -> Path | None:
    """Return a venv root with ansible-core for the given version.

    Delegates to ``build_venv`` which reuses an existing cached venv or
    creates a new one.  UV wheel cache makes repeated builds near-instant.

    Args:
        version: Ansible version string (e.g. "2.20").

    Returns:
        Path to venv root, or None if build fails.
    """
    from apme_engine.collection_cache.venv_builder import build_venv

    parts = version.split(".")
    pip_version = ".".join(parts[:2]) + ".0" if len(parts) < 3 else version
    try:
        return build_venv(pip_version, collection_specs=[])
    except Exception as exc:
        sys.stderr.write(f"Ansible venv build failed for {version}: {exc}\n")
        sys.stderr.flush()
        return None


def resolve_ansible_playbook(version: str) -> Path | None:
    """Find ansible-playbook for a given version.

    Args:
        version: Ansible version string.

    Returns:
        Path to ansible-playbook binary, or None.
    """
    venv = resolve_venv_root(version)
    if venv is not None:
        candidate = venv / "bin" / "ansible-playbook"
        if candidate.is_file():
            return candidate
    return None


def setup_collections_env(collection_specs: list[str], cache_root: Path) -> dict[str, str] | None:
    """Build ANSIBLE_COLLECTIONS_PATH pointing at the cache so ansible finds collections.

    Args:
        collection_specs: List of collection specs (used to determine if setup needed).
        cache_root: Root of the collection cache.

    Returns:
        Env dict with ANSIBLE_COLLECTIONS_PATH if paths exist, else None.
    """
    if not collection_specs:
        return None
    from apme_engine.collection_cache.config import galaxy_cache_dir, github_cache_dir

    paths = []
    galaxy = galaxy_cache_dir(cache_root)
    if galaxy.is_dir():
        paths.append(str(galaxy))
    github = github_cache_dir(cache_root)
    if github.is_dir():
        for org_dir in github.iterdir():
            if org_dir.is_dir():
                paths.append(str(org_dir))
    if paths:
        return {"ANSIBLE_COLLECTIONS_PATH": ":".join(paths)}
    return None
