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
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from apme.v1.common_pb2 import ProgressUpdate
from apme.v1.primary_pb2 import (
    FileDiff,
    FilePatch,
    FixOptions,
    FixReport,
    Proposal,
    ScanOptions,
)
from apme_engine.daemon.deadline import _parse_int_env, operation_deadline_mono
from apme_engine.engine.models import ViolationDict

logger = logging.getLogger(__name__)

_DEFAULT_TTL = int(os.environ.get("APME_SESSION_TTL", "1800"))  # 30 min
_MAX_LIFETIME = _parse_int_env("APME_SESSION_MAX_LIFETIME", 7200)  # 2 hr
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
        status: Session status (1=AWAITING_APPROVAL, 2=PROCESSING,
            3=COMPLETE, 4=AWAITING_AI_TRIAGE).
        fix_options: Fix options from the client's first upload chunk.
        scan_options: Scan options from the client's first upload chunk.
        ai_proposals: Raw engine AI proposals for downstream use.
        tier1_proposals: Raw engine Tier 1 proposals when interactive
            (ADR-062 Phase 3); empty when Tier 1 auto-applies.
        awaiting_tier1_gate: True while Gate 1 (deterministic) proposals
            are pending; cleared after Tier 1 ApprovalRequest.
        awaiting_assess: True while ADR-064 assess-pause findings are
            pending BeginRemediate (before Gate 1 ProposalsReady).
        assess_findings: Proto Violations last emitted in FindingsReady
            (for resume replay).
        awaiting_ai_triage: True while AI escalation triage is pending
            AiEscalateRequest (before Gate 2 AI runs).
        ai_triage_candidates: Proto Violations last emitted in
            AiTriageReady (for resume replay).
        ai_escalate_targets: ``(path, frozenset[rule_id])`` allow-list from
            AiEscalateRequest; empty frozenset of rules means entire path.
            ``None`` means no triage filter (allow all); ``[]`` means skip AI.
        remaining_ai: Remaining AI-candidate violations.
        remaining_manual: Remaining manual-review violations.
        dep_health_violations: Dependency-health violations that do not
            participate in graph remediation but must survive approval and
            final reporting.
        approved_ids: Set of proposal IDs approved by the user.
        approved_proposals: Metadata snapshots of approved proposals.
        rejected_proposals: Metadata snapshots of rejected proposals retained
            for FixCompletedEvent telemetry across approval gates.
        scan_id: Client-provided scan identifier for event correlation.
        project_root: Project root path from the first upload chunk.
        progress_logs: Pipeline milestone logs collected during processing.
        galaxy_cfg_path: Session-scoped ansible.cfg for Galaxy auth (ADR-045).
        venv_path: Session venv root path for convergence validator calls.
        ansible_core_version: Ansible-core version from session venv (ADR-040).
        installed_collections: ``(fqcn, version, source, license, supplier)`` tuples from session venv (ADR-040).
        installed_packages: ``(name, version, license, supplier)`` tuples from session venv (ADR-040).
        dependency_tree: Raw ``uv pip tree`` output (ADR-040).
        requirements_files: Requirement file paths found in project (ADR-040).
        content_graph: Persisted ``ContentGraph`` across approval gates
            (ADR-044 Phase 3).  Typed as ``object`` to avoid coupling.
        graph_originals: Original file text keyed by path, used by
            ``splice_modifications`` after approval.
        graph_engine: ``GraphRemediationEngine`` retained across Option C
            gates so Gate 2 can continue on the same graph (typed as
            ``object`` to avoid coupling).
        pre_gate2_files: Snapshot of ``working_files`` taken before Gate 2
            AI assessment mutates the graph. Restored when the user declines
            AI proposals so unapproved AI / post-AI Tier 1 never leak into
            commit/PR payloads.
        operation_budget_s: Adaptive wall-clock budget for current compute
            phase (ADR-068); 0 when unset.
        operation_started_at: ``time.monotonic()`` when budget tracking began.
        last_progress_at: ``time.monotonic()`` at last server progress event.
        operation_generation: Monotonic counter incremented at each phase
            anchor; progress events carry this for stall filtering (ADR-068).
        max_lifetime_deadline_mono: Absolute session cap in monotonic time.
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
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_activity_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    idempotency_ok: bool = True
    status: int = 2  # PROCESSING
    fix_options: FixOptions | None = None
    scan_options: ScanOptions | None = None

    # Raw engine AI / Tier 1 proposals (not proto) for downstream use
    ai_proposals: list[object] = field(default_factory=list)
    tier1_proposals: list[object] = field(default_factory=list)
    awaiting_tier1_gate: bool = False
    awaiting_assess: bool = False
    assess_findings: list[object] = field(default_factory=list)
    awaiting_ai_triage: bool = False
    ai_triage_candidates: list[object] = field(default_factory=list)
    # (path, rule_ids) — empty rule_ids means all AI-candidates on path
    ai_escalate_targets: list[tuple[str, frozenset[str]]] | None = None

    # Remaining violations from engine report
    remaining_ai: list[ViolationDict] = field(default_factory=list)
    remaining_manual: list[ViolationDict] = field(default_factory=list)
    dep_health_violations: list[ViolationDict] = field(default_factory=list)

    # Proposal IDs approved by the user (for FixCompletedEvent)
    approved_ids: set[str] = field(default_factory=set)
    # Metadata snapshots of approved proposals (rule_id, file, tier, confidence)
    approved_proposals: list[dict[str, object]] = field(default_factory=list)
    # Metadata snapshots of rejected proposals preserved for telemetry.
    rejected_proposals: dict[str, dict[str, object]] = field(default_factory=dict)

    # Identifiers captured from the first upload chunk for event emission
    scan_id: str = ""
    project_root: str = ""

    # Pipeline milestone logs collected during processing for FixCompletedEvent
    progress_logs: list[ProgressUpdate] = field(default_factory=list)

    # Session-scoped ansible.cfg for Galaxy auth (ADR-045).
    # Written by Primary from proto galaxy_servers; cleaned up with temp_dir.
    galaxy_cfg_path: Path | None = None

    # Manifest data captured from the first scan pass (ADR-040)
    venv_path: str = ""
    ansible_core_version: str = ""
    installed_collections: list[tuple[str, str, str, str, str]] = field(default_factory=list)
    installed_packages: list[tuple[str, str, str, str]] = field(default_factory=list)
    dependency_tree: str = ""
    requirements_files: list[str] = field(default_factory=list)

    # Graph engine state persisted across approval gates (ADR-044 Phase 3).
    # Typed as ``object`` to avoid importing ContentGraph in this module.
    content_graph: object | None = None
    graph_originals: dict[str, str] = field(default_factory=dict)
    graph_engine: object | None = None
    # working_files before Gate 2 AI assessment (restore on decline-all).
    pre_gate2_files: dict[str, bytes] = field(default_factory=dict)

    # ADR-068: adaptive operation deadline (decoupled from idle TTL).
    operation_budget_s: int = 0
    operation_started_at: float = 0.0
    last_progress_at: float = 0.0
    operation_generation: int = 0
    max_lifetime_deadline_mono: float = 0.0

    @property
    def ttl_seconds(self) -> int:
        """Remaining idle TTL in seconds."""
        elapsed = (datetime.now(UTC) - self.last_activity_at).total_seconds()
        return max(0, _DEFAULT_TTL - int(elapsed))

    @property
    def lifetime_seconds(self) -> int:
        """Total session age in seconds."""
        return int((datetime.now(UTC) - self.created_at).total_seconds())

    @property
    def expired(self) -> bool:
        """True if session has timed out or exceeded max lifetime."""
        return self.ttl_seconds <= 0 or self.lifetime_seconds >= _MAX_LIFETIME

    @property
    def expiring_soon(self) -> bool:
        """True if session will expire within 5 minutes."""
        return 0 < self.ttl_seconds <= 300

    def touch(self) -> None:
        """Reset idle timer to now."""
        self.last_activity_at = datetime.now(UTC)

    def init_lifetime_deadline(self) -> None:
        """Record absolute session lifetime cap in monotonic time (ADR-068)."""
        self.max_lifetime_deadline_mono = time.monotonic() + _MAX_LIFETIME

    def reanchor_lifetime_deadline(self) -> None:
        """Re-anchor lifetime cap after resume (ADR-068)."""
        remaining = max(0, _MAX_LIFETIME - self.lifetime_seconds)
        self.max_lifetime_deadline_mono = time.monotonic() + remaining

    def begin_operation_phase(self, budget_s: int) -> None:
        """Begin a new operation phase with fresh budget and progress anchors.

        Args:
            budget_s: Total allowed seconds for the current compute phase.
        """
        now = time.monotonic()
        self.operation_budget_s = max(0, budget_s)
        self.operation_started_at = now
        self.last_progress_at = now
        self.operation_generation += 1
        self.touch()

    def start_operation(self, budget_s: int) -> None:
        """Begin tracking wall-clock operation budget (ADR-068).

        Deprecated alias for :meth:`begin_operation_phase`.

        Args:
            budget_s: Total allowed seconds for the current compute phase.
        """
        self.begin_operation_phase(budget_s)

    def record_progress(self, *, task_linked: bool = True) -> None:
        """Refresh idle TTL; optionally reset stall clock for task-linked work.

        Args:
            task_linked: When true, reset ``last_progress_at`` (ADR-068).
        """
        if task_linked:
            self.last_progress_at = time.monotonic()
        self.touch()

    def operation_budget_remaining(self) -> int:
        """Seconds remaining on the operation budget (0 when unset or expired).

        Returns:
            Remaining budget seconds.
        """
        if self.operation_budget_s <= 0 or self.operation_started_at <= 0:
            return 0
        deadline = operation_deadline_mono(
            operation_started_at=self.operation_started_at,
            operation_budget_s=self.operation_budget_s,
            max_lifetime_deadline_mono=self.max_lifetime_deadline_mono,
        )
        return max(0, int(deadline - time.monotonic()))

    def cleanup(self) -> None:
        """Remove temp directory and session-scoped Galaxy config if present."""
        if self.galaxy_cfg_path and self.galaxy_cfg_path.parent.is_dir():
            with contextlib.suppress(OSError):
                shutil.rmtree(self.galaxy_cfg_path.parent)
            self.galaxy_cfg_path = None
        if self.temp_dir and self.temp_dir.is_dir():
            with contextlib.suppress(OSError):
                shutil.rmtree(self.temp_dir)
            self.temp_dir = None


class SessionStore:
    """In-memory store of active fix sessions with background reaper."""

    def __init__(self) -> None:
        """Initialize empty session store."""
        self._sessions: dict[str, SessionState] = {}
        self._reaper_task: asyncio.Task[None] | None = None

    @property
    def count(self) -> int:
        """Number of active sessions."""
        return len(self._sessions)

    def create(self) -> SessionState:
        """Create a new session, raising ResourceExhaustedError if at limit.

        Returns:
            New SessionState.

        Raises:
            ResourceExhaustedError: If at max concurrent sessions.
        """
        if len(self._sessions) >= _MAX_SESSIONS:
            msg = (
                f"Maximum concurrent sessions ({_MAX_SESSIONS}) reached. "
                "Close an existing session or wait for expiration."
            )
            raise ResourceExhaustedError(msg)
        session_id = uuid.uuid4().hex[:12]
        state = SessionState(session_id=session_id)
        state.init_lifetime_deadline()
        self._sessions[session_id] = state
        logger.info("Session %s created (active: %d)", session_id, len(self._sessions))
        return state

    def get(self, session_id: str) -> SessionState | None:
        """Look up a session by ID, returning None if missing or expired.

        Args:
            session_id: Session identifier.

        Returns:
            SessionState or None if expired/missing.
        """
        state = self._sessions.get(session_id)
        if state and state.expired:
            self._remove(session_id)
            return None
        return state

    def touch(self, session_id: str) -> None:
        """Refresh a session's idle timer.

        Args:
            session_id: Session identifier.
        """
        state = self._sessions.get(session_id)
        if state:
            state.touch()

    def remove(self, session_id: str) -> bool:
        """Remove and clean up a session by ID.

        Args:
            session_id: Session identifier.

        Returns:
            True if session was removed.
        """
        return self._remove(session_id)

    def _remove(self, session_id: str) -> bool:
        state = self._sessions.pop(session_id, None)
        if state:
            state.cleanup()
            logger.info("Session %s removed (active: %d)", session_id, len(self._sessions))
            return True
        return False

    def start_reaper(self) -> None:
        """Start the background reaper task."""
        if self._reaper_task is None or self._reaper_task.done():
            self._reaper_task = asyncio.ensure_future(self._reap_loop())

    def stop_reaper(self) -> None:
        """Cancel the background reaper task."""
        if self._reaper_task and not self._reaper_task.done():
            self._reaper_task.cancel()
            self._reaper_task = None

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(_REAP_INTERVAL)
            expired = [sid for sid, state in self._sessions.items() if state.expired]
            for sid in expired:
                logger.info("Reaping expired session %s", sid)
                self._remove(sid)


class ResourceExhaustedError(Exception):
    """Raised when the session limit is exceeded."""
