"""Venv resolution and collection environment helpers for ansible validator rules.

Venvs are session-scoped: each CLI invocation or named session gets its own
isolated venv via ``VenvSessionManager``.  UV wheel cache (pre-warmed at
container build time) makes fresh venv creation near-instant (~1-2s).
"""

import sys
from pathlib import Path

from apme_engine.collection_cache.venv_session import VenvSession, VenvSessionManager

SUPPORTED_VERSIONS = ["2.18", "2.19", "2.20"]
DEFAULT_VERSION = "2.20"

_manager: VenvSessionManager | None = None


def get_session_manager(ttl_seconds: int = 3600) -> VenvSessionManager:
    """Return the module-level session manager, creating it on first call.

    Args:
        ttl_seconds: TTL for named sessions (default 1 hour).

    Returns:
        The singleton VenvSessionManager instance.
    """
    global _manager  # noqa: PLW0603
    if _manager is None:
        _manager = VenvSessionManager(ttl_seconds=ttl_seconds)
    return _manager


def resolve_venv_root(
    version: str,
    collection_specs: list[str] | None = None,
    session_id: str | None = None,
) -> Path | None:
    """Return a venv root with ansible-core for the given version.

    Uses ``VenvSessionManager`` for concurrency-safe, session-scoped venvs.
    Without a ``session_id``, creates an ephemeral venv (cleaned up on release).
    With a ``session_id``, the venv persists for the session TTL and can be
    reused by subsequent calls.

    Args:
        version: Ansible version string (e.g. "2.20").
        collection_specs: Optional collection specifiers to install.
        session_id: Optional session ID for venv reuse across invocations.

    Returns:
        Path to venv root, or None if build fails.
    """
    mgr = get_session_manager()
    try:
        session = mgr.acquire(
            ansible_version=version,
            collection_specs=collection_specs,
            session_id=session_id,
        )
        return session.venv_root
    except Exception as exc:
        sys.stderr.write(f"Ansible venv build failed for {version}: {exc}\n")
        sys.stderr.flush()
        return None


def resolve_session(
    version: str,
    collection_specs: list[str] | None = None,
    session_id: str | None = None,
) -> VenvSession | None:
    """Resolve a full VenvSession (includes session_id for later release/touch).

    Args:
        version: Ansible version string.
        collection_specs: Optional collection specifiers.
        session_id: Optional session ID for reuse.

    Returns:
        VenvSession or None if build fails.
    """
    mgr = get_session_manager()
    try:
        return mgr.acquire(
            ansible_version=version,
            collection_specs=collection_specs,
            session_id=session_id,
        )
    except Exception as exc:
        sys.stderr.write(f"Ansible venv build failed for {version}: {exc}\n")
        sys.stderr.flush()
        return None


def release_session(session_id: str) -> None:
    """Release a session (ephemeral sessions are deleted immediately).

    Args:
        session_id: Session to release.
    """
    mgr = get_session_manager()
    mgr.release(session_id)


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
