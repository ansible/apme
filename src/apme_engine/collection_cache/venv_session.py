"""Session-scoped venvs with file locking, TTL expiry, and reaping.

Each session gets its own isolated venv directory, eliminating the concurrency
bugs present in the old shared hash-keyed approach.  Sessions can be ephemeral
(auto-cleaned on release) or named (persist for ``ttl_seconds`` after last use).

Concurrency safety:
    Creation is serialized per session via ``fcntl.flock`` on a ``.lock`` file.
    Once created, the venv is read-only and safe for concurrent subprocess calls
    (e.g. multiple ``ansible-doc`` invocations).

Storage layout::

    $CACHE_ROOT/sessions/
        <session_id>/
            venv/             # the actual virtualenv
            session.json      # metadata
            .lock             # flock target
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from apme_engine.collection_cache.config import get_cache_root
from apme_engine.collection_cache.venv_builder import build_venv

_DEFAULT_TTL = 3600


@dataclass
class VenvSession:
    """Metadata for a session-scoped venv.

    Attributes:
        session_id: Unique identifier (user-provided or auto-generated).
        venv_root: Path to the venv directory.
        ansible_version: ansible-core version installed in the venv.
        collection_specs: Collection specifiers symlinked into the venv.
        created_at: Unix timestamp of creation.
        last_used_at: Unix timestamp of last touch or acquire.
        ephemeral: If True, venv is deleted on release.
    """

    session_id: str
    venv_root: Path
    ansible_version: str
    collection_specs: list[str] = field(default_factory=list)
    created_at: float = 0.0
    last_used_at: float = 0.0
    ephemeral: bool = False


class VenvSessionManager:
    """Manage session-scoped venvs with locking, TTL, and reaping."""

    def __init__(
        self,
        sessions_root: Path | None = None,
        ttl_seconds: int = _DEFAULT_TTL,
    ) -> None:
        """Initialize the session manager.

        Args:
            sessions_root: Directory under which session directories are created.
                Defaults to ``$CACHE_ROOT/sessions/``.
            ttl_seconds: How long an unused named session persists before reaping.
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
        ansible_version: str,
        collection_specs: list[str] | None = None,
        session_id: str | None = None,
    ) -> VenvSession:
        """Get or create a session venv.  Thread/process safe via file lock.

        If ``session_id`` is None, an ephemeral session is created with a random
        UUID that will be cleaned up on release.

        If a session with the given ID already exists and its ansible version and
        collection specs match, it is reused instantly (warm hit).

        Args:
            ansible_version: e.g. "2.20.0" or "2.20".
            collection_specs: Collection specifiers to symlink into the venv.
            session_id: Optional reusable session identifier.

        Returns:
            A VenvSession with a ready-to-use venv.
        """
        specs = collection_specs or []
        ephemeral = session_id is None
        sid = session_id or uuid.uuid4().hex[:12]
        session_dir = self._root / sid
        session_dir.mkdir(parents=True, exist_ok=True)
        lock_path = session_dir / ".lock"
        meta_path = session_dir / "session.json"
        venv_dir = session_dir / "venv"

        parts = ansible_version.split(".")
        pip_version = ".".join(parts[:2]) + ".0" if len(parts) < 3 else ansible_version

        with open(lock_path, "w") as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                existing = self._read_meta(meta_path)
                if (
                    existing is not None
                    and existing.ansible_version == pip_version
                    and sorted(existing.collection_specs) == sorted(specs)
                    and venv_dir.is_dir()
                ):
                    existing.last_used_at = time.time()
                    existing.ephemeral = ephemeral
                    self._write_meta(meta_path, existing)
                    return existing

                # Version or specs changed, or venv missing — rebuild
                if venv_dir.is_dir():
                    shutil.rmtree(venv_dir)

                venv_root = build_venv(
                    ansible_core_version=pip_version,
                    collection_specs=specs,
                    venvs_root=session_dir,
                )
                # build_venv uses a hash-based subdir name; normalize to "venv"
                if venv_root.name != "venv":
                    target = session_dir / "venv"
                    if target.exists():
                        shutil.rmtree(target)
                    venv_root.rename(target)
                    venv_root = target

                now = time.time()
                session = VenvSession(
                    session_id=sid,
                    venv_root=venv_root,
                    ansible_version=pip_version,
                    collection_specs=specs,
                    created_at=now,
                    last_used_at=now,
                    ephemeral=ephemeral,
                )
                self._write_meta(meta_path, session)
                return session
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)

    def touch(self, session_id: str) -> bool:
        """Update last_used_at to prevent expiry.

        Args:
            session_id: Session to touch.

        Returns:
            True if session exists and was touched, False otherwise.
        """
        meta_path = self._root / session_id / "session.json"
        if not meta_path.is_file():
            return False
        session = self._read_meta(meta_path)
        if session is None:
            return False
        session.last_used_at = time.time()
        self._write_meta(meta_path, session)
        return True

    def release(self, session_id: str) -> bool:
        """Release a session.  Ephemeral sessions are deleted immediately.

        Named sessions remain on disk until they expire (reaped by reap_expired).

        Args:
            session_id: Session to release.

        Returns:
            True if session was found (and possibly deleted), False otherwise.
        """
        session_dir = self._root / session_id
        if not session_dir.is_dir():
            return False
        meta = self._read_meta(session_dir / "session.json")
        if meta is not None and meta.ephemeral:
            shutil.rmtree(session_dir, ignore_errors=True)
        return True

    def get(self, session_id: str) -> VenvSession | None:
        """Look up a session by ID without creating or modifying it.

        Args:
            session_id: Session to look up.

        Returns:
            The session if it exists, None otherwise.
        """
        meta_path = self._root / session_id / "session.json"
        return self._read_meta(meta_path)

    def list_sessions(self) -> list[VenvSession]:
        """List all sessions with valid metadata.

        Returns:
            List of VenvSession objects, sorted by last_used_at descending.
        """
        sessions: list[VenvSession] = []
        if not self._root.is_dir():
            return sessions
        for child in self._root.iterdir():
            if not child.is_dir():
                continue
            meta = self._read_meta(child / "session.json")
            if meta is not None:
                sessions.append(meta)
        sessions.sort(key=lambda s: s.last_used_at, reverse=True)
        return sessions

    def delete(self, session_id: str) -> bool:
        """Forcefully delete a session and its venv.

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
        """Delete sessions whose last_used_at + ttl has passed.

        Ephemeral sessions that somehow were not cleaned up are also reaped.

        Returns:
            Count of sessions deleted.
        """
        now = time.time()
        reaped = 0
        if not self._root.is_dir():
            return reaped
        for child in self._root.iterdir():
            if not child.is_dir():
                continue
            meta = self._read_meta(child / "session.json")
            if meta is None:
                if not any(child.iterdir()):
                    child.rmdir()
                continue
            age = now - meta.last_used_at
            if age > self._ttl or meta.ephemeral:
                shutil.rmtree(child, ignore_errors=True)
                reaped += 1
        return reaped

    @staticmethod
    def _read_meta(path: Path) -> VenvSession | None:
        """Read session metadata from JSON file.

        Args:
            path: Path to session.json.

        Returns:
            Parsed session or None if file is missing/corrupt.
        """
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return VenvSession(
                session_id=data["session_id"],
                venv_root=Path(data["venv_root"]),
                ansible_version=data["ansible_version"],
                collection_specs=data.get("collection_specs", []),
                created_at=data.get("created_at", 0.0),
                last_used_at=data.get("last_used_at", 0.0),
                ephemeral=data.get("ephemeral", False),
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    @staticmethod
    def _write_meta(path: Path, session: VenvSession) -> None:
        """Write session metadata to JSON atomically.

        Args:
            path: Path to session.json.
            session: Session to serialize.
        """
        data = asdict(session)
        data["venv_root"] = str(session.venv_root)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
