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
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from apme_engine.collection_cache.config import get_cache_root
from apme_engine.collection_cache.venv_builder import (
    create_base_venv,
    install_collections_incremental,
)

_DEFAULT_TTL = 3600


def _normalize_version(raw: str) -> str:
    """Ensure a three-part pip version string (e.g. ``"2.17"`` → ``"2.17.0"``).

    Args:
        raw: Version string with 2 or 3 parts.

    Returns:
        Normalised ``X.Y.Z`` version string.
    """
    parts = raw.split(".")
    return ".".join(parts[:2]) + ".0" if len(parts) < 3 else raw


@dataclass
class VenvSession:
    """Metadata for a session-scoped venv (one per core version within a session).

    Attributes:
        session_id: Client-provided session identifier.
        venv_root: Path to the venv directory.
        ansible_version: Normalised ansible-core version installed.
        installed_collections: Collection specifiers actually present in the venv.
        created_at: Unix timestamp of venv creation.
        last_used_at: Unix timestamp of last acquire / touch.
    """

    session_id: str
    venv_root: Path
    ansible_version: str
    installed_collections: list[str] = field(default_factory=list)
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
        self._root = sessions_root or (get_cache_root() / "sessions")
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

        New core versions create sibling venvs under the same session
        directory — existing ones are never destroyed.

        Args:
            session_id: Client-provided session identifier.
            ansible_version: e.g. ``"2.17.0"`` or ``"2.17"``.
            collection_specs: Collection specifiers to ensure are installed.

        Returns:
            A ``VenvSession`` with a ready-to-use venv.
        """
        specs = collection_specs or []
        pip_version = _normalize_version(ansible_version)

        session_dir = self._root / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        lock_path = session_dir / ".lock"
        version_dir = session_dir / pip_version
        meta_path = version_dir / "meta.json"
        venv_dir = version_dir / "venv"

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
                        return existing

                    install_collections_incremental(venv_dir, sorted(missing))
                    existing.installed_collections = sorted(installed | set(specs))
                    existing.last_used_at = time.time()
                    self._write_version_meta(meta_path, existing)
                    return existing

                version_dir.mkdir(parents=True, exist_ok=True)
                if venv_dir.is_dir():
                    shutil.rmtree(venv_dir)

                create_base_venv(venv_dir, pip_version)
                if specs:
                    install_collections_incremental(venv_dir, specs)

                now = time.time()
                session = VenvSession(
                    session_id=session_id,
                    venv_root=venv_dir,
                    ansible_version=pip_version,
                    installed_collections=sorted(specs),
                    created_at=now,
                    last_used_at=now,
                )
                self._write_version_meta(meta_path, session)
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
