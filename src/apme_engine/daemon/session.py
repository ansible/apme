"""Session state management for FixSession bidirectional streaming (ADR-028).

Each fix session is an ephemeral assistant that holds working state between
approval gates. The engine (scan, remediate, format) stays stateless; only the
session coordinator is stateful.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from apme.v1.primary_pb2 import (
    FileDiff,
    FilePatch,
    FixReport,
    Proposal,
)

logger = logging.getLogger(__name__)

_DEFAULT_TTL = int(os.environ.get("APME_SESSION_TTL", "1800"))  # 30 min
_MAX_LIFETIME = int(os.environ.get("APME_SESSION_MAX_LIFETIME", "7200"))  # 2 hr
_MAX_SESSIONS = int(os.environ.get("APME_SESSION_MAX", "10"))
_REAP_INTERVAL = 60  # seconds


@dataclass
class SessionState:
    """Ephemeral per-session state held on the Primary.

    Attributes:
        session_id: Unique session identifier.
        original_files: Original file bytes keyed by relative path.
        working_files: Current working file bytes (mutated by fixes).
        tier1_patches: Applied Tier 1 patches.
        format_diffs: Format diffs from the formatting phase.
        proposals: Pending AI proposals keyed by proposal ID.
        current_tier: Current remediation tier (1, 2, or 3).
        report: Remediation report from the engine.
        temp_dir: Temporary directory for materialized files.
        created_at: Session creation timestamp.
        last_activity_at: Last client interaction timestamp.
        idempotency_ok: Whether formatter was idempotent.
        status: Session status (1=AWAITING_APPROVAL, 2=PROCESSING, 3=COMPLETE).
        ai_proposals: Raw engine AI proposals for downstream use.
        remaining_ai: Remaining AI-candidate violations.
        remaining_manual: Remaining manual-review violations.
    """

    session_id: str
    original_files: dict[str, bytes] = field(default_factory=dict)
    working_files: dict[str, bytes] = field(default_factory=dict)
    tier1_patches: list[FilePatch] = field(default_factory=list)
    format_diffs: list[FileDiff] = field(default_factory=list)
    proposals: dict[str, Proposal] = field(default_factory=dict)
    current_tier: int = 1
    report: FixReport | None = None
    temp_dir: Path | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    idempotency_ok: bool = True
    status: int = 2  # PROCESSING

    # Raw engine AI proposals (not proto) for downstream use
    ai_proposals: list[object] = field(default_factory=list)

    # Remaining violations from engine report
    remaining_ai: list[object] = field(default_factory=list)
    remaining_manual: list[object] = field(default_factory=list)

    @property
    def ttl_seconds(self) -> int:
        elapsed = (datetime.now(timezone.utc) - self.last_activity_at).total_seconds()
        return max(0, _DEFAULT_TTL - int(elapsed))

    @property
    def lifetime_seconds(self) -> int:
        return int((datetime.now(timezone.utc) - self.created_at).total_seconds())

    @property
    def expired(self) -> bool:
        return self.ttl_seconds <= 0 or self.lifetime_seconds >= _MAX_LIFETIME

    @property
    def expiring_soon(self) -> bool:
        return 0 < self.ttl_seconds <= 300

    def touch(self) -> None:
        self.last_activity_at = datetime.now(timezone.utc)

    def cleanup(self) -> None:
        if self.temp_dir and self.temp_dir.is_dir():
            with contextlib.suppress(OSError):
                shutil.rmtree(self.temp_dir)
            self.temp_dir = None


class SessionStore:
    """In-memory store of active fix sessions with background reaper."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._reaper_task: asyncio.Task[None] | None = None

    @property
    def count(self) -> int:
        return len(self._sessions)

    def create(self) -> SessionState:
        if len(self._sessions) >= _MAX_SESSIONS:
            msg = (
                f"Maximum concurrent sessions ({_MAX_SESSIONS}) reached. "
                "Close an existing session or wait for expiration."
            )
            raise ResourceExhaustedError(msg)
        session_id = uuid.uuid4().hex[:12]
        state = SessionState(session_id=session_id)
        self._sessions[session_id] = state
        logger.info("Session %s created (active: %d)", session_id, len(self._sessions))
        return state

    def get(self, session_id: str) -> SessionState | None:
        state = self._sessions.get(session_id)
        if state and state.expired:
            self._remove(session_id)
            return None
        return state

    def touch(self, session_id: str) -> None:
        state = self._sessions.get(session_id)
        if state:
            state.touch()

    def remove(self, session_id: str) -> bool:
        return self._remove(session_id)

    def _remove(self, session_id: str) -> bool:
        state = self._sessions.pop(session_id, None)
        if state:
            state.cleanup()
            logger.info("Session %s removed (active: %d)", session_id, len(self._sessions))
            return True
        return False

    def start_reaper(self) -> None:
        if self._reaper_task is None or self._reaper_task.done():
            self._reaper_task = asyncio.ensure_future(self._reap_loop())

    def stop_reaper(self) -> None:
        if self._reaper_task and not self._reaper_task.done():
            self._reaper_task.cancel()
            self._reaper_task = None

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(_REAP_INTERVAL)
            expired = [
                sid for sid, state in self._sessions.items() if state.expired
            ]
            for sid in expired:
                logger.info("Reaping expired session %s", sid)
                self._remove(sid)


class ResourceExhaustedError(Exception):
    """Raised when the session limit is exceeded."""
