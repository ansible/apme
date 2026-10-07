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
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from apme.v1.common_pb2 import ProgressUpdate
from apme.v1.engine_pb2 import (
    FileDiff,
    FilePatch,
    FixOptions,
    FixReport,
    Proposal,
    ScanOptions,
)
from apme_engine.config_env import get_env_float, get_env_int
from apme_engine.daemon.deadline import operation_deadline_mono
from apme_engine.engine.models import ViolationDict

logger = logging.getLogger(__name__)

_DEFAULT_TTL = int(os.environ.get("APME_SESSION_TTL", "1800"))  # 30 min
_MAX_LIFETIME = get_env_int("APME_SESSION_MAX_LIFETIME", 7200)  # 2 hr
_MAX_SESSIONS = int(os.environ.get("APME_SESSION_MAX", "10"))
_REAP_INTERVAL = 60  # seconds


# Backward-compatibility alias: identical to the canonical parser, kept
# because tests import this name (single canonical home: config_env).
_parse_float_env = get_env_float


# PE-20 admission hardening. FixSession is unauthenticated, so a single
# client can starve scans by bursting session creation (each session can
# trigger pip/galaxy builds). The concurrent-venv-build semaphore below caps
# the expensive resource (pip/galaxy builds) and is active by default. The
# creation interval (APME_SESSION_CREATE_INTERVAL_S, default 1.0s, 0 disables)
# rate-limits session creation bursts. The legacy
# APME_SESSION_MIN_CREATE_INTERVAL_S is honored as a deprecated alias when
# the new variable is unset. Neither mechanism is authentication. Real caller
# auth is tracked in GitHub #664.
_MIN_CREATE_INTERVAL_S = _parse_float_env("APME_SESSION_MIN_CREATE_INTERVAL_S", 0.0)
_CREATE_INTERVAL_S = _parse_float_env("APME_SESSION_CREATE_INTERVAL_S", 1.0)
# Never-sealed sessions (upload_sealed=False) that stay idle past this age
# are reaped early (default 10min) so abandoned uploads cannot exhaust the
# session cap for the full TTL (N17). Zero disables the early reap.
_IDLE_EXPIRE_NEVER_SEALED_S = _parse_float_env("APME_SESSION_IDLE_EXPIRE_S", 600.0)
# Floors use the shared config_env capability directly so the two modules
# cannot drift (below-minimum values fall back to defaults with a warning).
_MAX_CONCURRENT_VENV_BUILDS = get_env_int("APME_SESSION_MAX_CONCURRENT_VENV_BUILDS", 3, min_value=1)
# Bound the semaphore wait so hung pip/galaxy builds cannot starve warm-hit
# scans indefinitely (3 hung builds would otherwise stall all new scans
# holding streams/sessions). The engine takes a semaphore-free warm-hit peek
# first (VenvSessionManager.peek_warm) and only cold/incremental builds wait
# for a slot, so the bound only applies to scans that actually build.
_VENV_BUILD_WAIT_S = get_env_float("APME_SESSION_VENV_BUILD_WAIT_S", 600.0, positive_only=True)

_venv_build_semaphore: asyncio.Semaphore | None = None


def _effective_create_interval_s() -> float:
    """Return the enforced session creation interval.

    Prefers ``APME_SESSION_CREATE_INTERVAL_S`` (default 1.0s); when that
    variable is unset, falls back to the deprecated
    ``APME_SESSION_MIN_CREATE_INTERVAL_S`` so existing deployments keep
    working. The environment is read at call time (never from import-time
    globals) so operator changes apply without a daemon restart; when
    neither variable is set, the module globals remain as a
    monkeypatchable fallback for tests.

    Returns:
        Effective minimum seconds between session creates (0 disables).
    """
    if "APME_SESSION_CREATE_INTERVAL_S" in os.environ:
        return get_env_float("APME_SESSION_CREATE_INTERVAL_S", 1.0)
    if "APME_SESSION_MIN_CREATE_INTERVAL_S" in os.environ:
        return get_env_float("APME_SESSION_MIN_CREATE_INTERVAL_S", 0.0)
    # Neither variable configured: honor module globals (import-time
    # defaults, monkeypatchable in tests) with legacy-new precedence.
    if _MIN_CREATE_INTERVAL_S > 0:
        return _MIN_CREATE_INTERVAL_S
    return _CREATE_INTERVAL_S


def _idle_expire_never_sealed_s() -> float:
    """Return the never-sealed early-reap age, read at call time.

    Reads ``APME_SESSION_IDLE_EXPIRE_S`` from the environment on every
    call (default 600s; non-positive disables) so operator changes apply
    without a restart. Falls back to the import-time module global when
    the variable is unset (monkeypatchable in tests).

    Returns:
        Idle seconds after which a never-sealed session is reaped early.
    """
    if "APME_SESSION_IDLE_EXPIRE_S" in os.environ:
        return get_env_float("APME_SESSION_IDLE_EXPIRE_S", 600.0)
    return _IDLE_EXPIRE_NEVER_SEALED_S


def get_venv_build_semaphore() -> asyncio.Semaphore:
    """Return the process-wide cap on concurrent venv builds (PE-20).

    The venv build itself lives outside this module (``VenvSessionManager``);
    the engine's venv-acquire path should guard its build/install work with
    this semaphore (or :func:`limit_venv_builds`).

    Returns:
        Shared ``asyncio.Semaphore`` sized by
        ``APME_SESSION_MAX_CONCURRENT_VENV_BUILDS`` (default 3).
    """
    global _venv_build_semaphore
    if _venv_build_semaphore is None:
        _venv_build_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_VENV_BUILDS)
    return _venv_build_semaphore


@contextlib.asynccontextmanager
async def limit_venv_builds() -> AsyncIterator[Callable[[asyncio.Future[object]], None]]:
    """Cap concurrent venv (pip/galaxy) builds across sessions (PE-20).

    The wait is bounded by ``APME_SESSION_VENV_BUILD_WAIT_S`` (default 600s)
    so hung builds fail fast instead of starving warm-hit scans forever.

    Yields:
        Callable[[asyncio.Future[object]], None]: Transfers slot release to an
        executor future's completion. Call with the future from
        ``run_in_executor`` before awaiting so cancellation of the awaiting
        coroutine does not release the slot while the worker thread is still
        running. When omitted, the slot releases when the context exits.

    Raises:
        ResourceExhaustedError: If no build slot frees within the wait bound
            (surfaced as gRPC RESOURCE_EXHAUSTED so clients back off).
    """
    sem = get_venv_build_semaphore()
    try:
        await asyncio.wait_for(sem.acquire(), timeout=_VENV_BUILD_WAIT_S)
    except TimeoutError as exc:
        raise ResourceExhaustedError(
            f"No venv build slot freed within {_VENV_BUILD_WAIT_S:.0f}s "
            f"({_MAX_CONCURRENT_VENV_BUILDS} slots); try again shortly"
        ) from exc
    transferred = False

    def _release_on(fut: asyncio.Future[object]) -> None:
        nonlocal transferred
        transferred = True
        fut.add_done_callback(lambda _f: sem.release())

    try:
        yield _release_on
    finally:
        if not transferred:
            sem.release()


@dataclass
class SessionState:
    """Ephemeral per-session state held on the Engine.

    Attributes:
        session_id: Unique session identifier.
        original_files: Original file bytes keyed by relative path.
        working_files: Current working file bytes (mutated by fixes).
        tier1_patches: Applied Tier 1 patches.
        format_diffs: Format diffs from the formatting phase.
        proposals: Pending AI proposals keyed by proposal ID.
        review_declined_proposals: Declined-only review rows (display/replay;
            not applied via ApprovalRequest).
        current_tier: Current remediation tier (1, 2, or 3).
        report: Remediation report from the engine.
        temp_dir: Temporary directory for materialized files.
        created_at: Session creation timestamp.
        last_activity_at: Last client interaction timestamp (wall-clock,
            for TTL display). The never-sealed idle reap uses the
            monotonic companion ``_last_activity_mono`` instead.
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
        upload_sealed: True once the terminal upload chunk has been
            processed; duplicate ``chunk.last`` retries replay state instead
            of re-running remediation. Dies with the session (close,
            disconnect, reaper) so no cross-session seal set can leak.
        upload_sealed_hash: SHA-256 over the sealed payload (sorted
            normalized path + content pairs). A retried terminal chunk
            replays state only when its payload hash matches; differing
            bytes abort so a corrected payload can never be silently
            dropped in favor of stale state.
        upload_sealed_chunk_hash: SHA-256 over the terminal chunk payload
            alone (same normalize+dedup as append). Retried terminal
            chunks are compared against this, not the full-payload hash:
            the full payload covers all accumulated files while a single
            chunk covers only its own files, so comparing a retried chunk
            to the full hash can never match for multi-chunk uploads.
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
    review_declined_proposals: dict[str, Proposal] = field(default_factory=dict)
    current_tier: int = 1
    report: FixReport | None = None
    temp_dir: Path | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_activity_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    # Monotonic companion to ``last_activity_at`` for the never-sealed idle
    # reap. Wall-clock can jump (NTP, VM suspend); the early reap compares
    # monotonic timestamps so idle sessions are reaped on real elapsed time.
    # ``touch()`` refreshes both; wall time stays for TTL display/compat.
    _last_activity_mono: float = field(default_factory=time.monotonic)
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
    # Written by Engine from proto galaxy_servers; cleaned up with temp_dir.
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

    # Terminal upload chunk already processed (PE-34). Set before the first
    # ``chunk.last`` runs so transport retries replay current state instead
    # of double-executing remediation. Lives on the session so it dies with
    # it (close/disconnect/reaper) — no servicer-side set to leak.
    upload_sealed: bool = False
    # Payload hash captured at seal time (see ``upload_sealed_hash`` docs).
    upload_sealed_hash: str = ""
    # Terminal-chunk hash captured at seal time for retry comparison.
    upload_sealed_chunk_hash: str = ""

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
        return self.ttl_seconds <= 0 or self.lifetime_seconds >= _MAX_LIFETIME or self.never_sealed_idle_expired

    @property
    def never_sealed_idle_expired(self) -> bool:
        """True when a never-sealed session has been idle past the early reap age.

        Sealed sessions (``upload_sealed=True``) are exempt — they use the
        full idle TTL. A non-positive ``APME_SESSION_IDLE_EXPIRE_S``
        disables the early reap. Idle age is measured on the monotonic
        clock (``_last_activity_mono``) so wall-clock jumps cannot extend
        or collapse the reap window; the threshold is read at call time
        via :func:`_idle_expire_never_sealed_s`.
        """
        if self.upload_sealed:
            return False
        idle_expire = _idle_expire_never_sealed_s()
        if idle_expire <= 0:
            return False
        idle_s = time.monotonic() - self._last_activity_mono
        return idle_s >= idle_expire

    @property
    def expiring_soon(self) -> bool:
        """True if session will expire within 5 minutes."""
        return 0 < self.ttl_seconds <= 300

    def touch(self) -> None:
        """Reset idle timer to now (wall-clock display and monotonic reap clock)."""
        self.last_activity_at = datetime.now(UTC)
        self._last_activity_mono = time.monotonic()

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

    def reset_partial_run_state(self) -> None:
        """Drop partial-run mutations so a retried terminal chunk starts clean.

        Called when processing fails after seal: restores uploaded bytes and
        clears per-run artifacts without removing the session from the store.
        """
        self.working_files = dict(self.original_files)
        self.format_diffs = []
        self.tier1_patches = []
        self.proposals.clear()
        self.review_declined_proposals.clear()
        self.progress_logs = []
        self.report = None
        self.ai_proposals = []
        self.tier1_proposals = []
        self.remaining_ai = []
        self.remaining_manual = []
        self.dep_health_violations = []
        self.awaiting_tier1_gate = False
        self.awaiting_assess = False
        self.awaiting_ai_triage = False
        self.content_graph = None
        self.graph_engine = None
        self.status = 2  # PROCESSING
        self._cleanup_temp_dir()

    def cleanup(self) -> None:
        """Remove temp directory and session-scoped Galaxy config if present."""
        self._cleanup_galaxy_cfg()
        self._cleanup_temp_dir()

    def _cleanup_galaxy_cfg(self) -> None:
        """Remove session-scoped Galaxy config directory if present."""
        if self.galaxy_cfg_path and self.galaxy_cfg_path.parent.is_dir():
            with contextlib.suppress(OSError):
                shutil.rmtree(self.galaxy_cfg_path.parent)
            self.galaxy_cfg_path = None

    def _cleanup_temp_dir(self) -> None:
        """Remove materialized working directory if present."""
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
        self._last_create_mono: float = 0.0

    @property
    def count(self) -> int:
        """Number of active sessions."""
        return len(self._sessions)

    def create(self) -> SessionState:
        """Create a new session, raising ResourceExhaustedError if at limit.

        Enforces the session cap first, then the creation interval
        (``APME_SESSION_CREATE_INTERVAL_S``, default 1.0s, 0 disables;
        legacy ``APME_SESSION_MIN_CREATE_INTERVAL_S`` honored when the
        new variable is unset) so operators get burst protection for
        unauthenticated clients (PE-20; real caller auth is GitHub #664).

        Returns:
            New SessionState.

        Raises:
            ResourceExhaustedError: If at max concurrent sessions, or if
                called sooner than the creation interval after the previous
                successful creation.
        """
        # NOTE: the cap check + interval check + insert below contain no
        # awaits, so they are atomic on the single event-loop thread that
        # runs FixSession handlers — concurrent streams interleave only at
        # await points, never inside create().
        if len(self._sessions) >= _MAX_SESSIONS:
            msg = (
                f"Maximum concurrent sessions ({_MAX_SESSIONS}) reached. "
                "Close an existing session or wait for expiration."
            )
            raise ResourceExhaustedError(msg)
        now_mono = time.monotonic()
        effective_interval = _effective_create_interval_s()
        if (
            self._last_create_mono
            and effective_interval > 0
            and (now_mono - self._last_create_mono) < effective_interval
        ):
            msg = f"Session creation rate limited: at most one session per {effective_interval}s. Retry shortly."
            raise ResourceExhaustedError(msg)
        session_id = uuid.uuid4().hex[:12]
        state = SessionState(session_id=session_id)
        state.init_lifetime_deadline()
        self._sessions[session_id] = state
        self._last_create_mono = now_mono
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
