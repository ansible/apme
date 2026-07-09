"""In-memory registry for project operation state (ADR-052).

The ``OperationRegistry`` is the Gateway's authoritative source of truth
for in-flight project operations.  It holds one ``OperationState`` per
project, broadcasts delta events to SSE subscribers, and runs a TTL
reaper to evict terminal states.

Thread-safety is achieved via the GIL + single-threaded asyncio event loop
(all access is from the same loop).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import datetime, timezone
from typing import Any

from apme_gateway.operation_types import (
    TERMINAL_STATUSES,
    OperationResult,
    OperationState,
    OperationStatus,
    ProgressEntry,
    Proposal,
    SSEEventType,
)

logger = logging.getLogger(__name__)

_DEFAULT_TERMINAL_TTL = 600.0  # 10 minutes


class OperationRegistry:
    """In-memory store for active project operations.

    At most one non-terminal operation is allowed per project.
    """

    def __init__(self, terminal_ttl: float = _DEFAULT_TERMINAL_TTL) -> None:
        """Initialise the registry.

        Args:
            terminal_ttl: Seconds to retain terminal operations before reaping.
        """
        self._ops: dict[str, OperationState] = {}
        self._by_project: dict[str, str] = {}
        self._terminal_times: dict[str, float] = {}
        self._terminal_ttl = terminal_ttl
        self._reaper_task: asyncio.Task[None] | None = None
        self._project_locks: dict[str, asyncio.Lock] = {}

    def project_lock(self, project_id: str) -> asyncio.Lock:
        """Return a per-project lock for operate/abandon critical sections.

        Args:
            project_id: Project UUID.

        Returns:
            Lock shared by all callers for this project.
        """
        lock = self._project_locks.get(project_id)
        if lock is None:
            lock = asyncio.Lock()
            self._project_locks[project_id] = lock
        return lock

    # ── lifecycle ──────────────────────────────────────────────────────

    def start_reaper(self) -> None:
        """Start the background TTL reaper task."""
        if self._reaper_task is None or self._reaper_task.done():
            self._reaper_task = asyncio.create_task(self._reap_loop())

    async def shutdown(self) -> None:
        """Cancel the reaper and clean up all operations."""
        if self._reaper_task and not self._reaper_task.done():
            self._reaper_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reaper_task
        for op in list(self._ops.values()):
            if op.grpc_task and not op.grpc_task.done():
                op.grpc_task.cancel()
            for q in op.sse_subscribers:
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait({"_close": True})
            op.sse_subscribers.clear()
        self._ops.clear()
        self._by_project.clear()
        self._terminal_times.clear()
        self._project_locks.clear()

    # ── CRUD ──────────────────────────────────────────────────────────

    def create(
        self,
        *,
        operation_id: str,
        project_id: str,
        scan_id: str,
        scan_type: str,
    ) -> OperationState:
        """Create a new operation in QUEUED state.

        Args:
            operation_id: Unique operation identifier.
            project_id: Owning project UUID.
            scan_id: Engine scan identifier.
            scan_type: ``check`` or ``remediate``.

        Returns:
            The newly created ``OperationState``.

        Raises:
            ValueError: If the project already has a non-terminal operation.
        """
        existing_op_id = self._by_project.get(project_id)
        if existing_op_id and existing_op_id in self._ops:
            existing = self._ops[existing_op_id]
            if existing.status not in TERMINAL_STATUSES:
                msg = f"Project {project_id} already has an active operation {existing_op_id}"
                raise ValueError(msg)
            self._remove(existing_op_id)

        state = OperationState(
            operation_id=operation_id,
            project_id=project_id,
            scan_id=scan_id,
            status=OperationStatus.QUEUED,
            scan_type=scan_type,
        )
        self._ops[operation_id] = state
        self._by_project[project_id] = operation_id
        return state

    def get(self, operation_id: str) -> OperationState | None:
        """Look up an operation by ID.

        Args:
            operation_id: The operation identifier.

        Returns:
            The ``OperationState``, or ``None`` if not found.
        """
        return self._ops.get(operation_id)

    def get_by_project(self, project_id: str) -> OperationState | None:
        """Look up the current operation for a project.

        Args:
            project_id: The project UUID.

        Returns:
            The ``OperationState``, or ``None`` if no operation exists.
        """
        op_id = self._by_project.get(project_id)
        if op_id is None:
            return None
        return self._ops.get(op_id)

    def list_active(self) -> list[OperationState]:
        """Return all non-terminal operations.

        Returns:
            List of active ``OperationState`` objects.
        """
        return [op for op in self._ops.values() if op.status not in TERMINAL_STATUSES]

    def _remove(self, operation_id: str) -> None:
        """Remove an operation from the registry.

        Args:
            operation_id: The operation to remove.
        """
        op = self._ops.pop(operation_id, None)
        if op is None:
            return
        self._terminal_times.pop(operation_id, None)
        if self._by_project.get(op.project_id) == operation_id:
            del self._by_project[op.project_id]
            # Drop idle lock so long-lived gateways do not retain one entry
            # per historical project_id (Copilot: unbounded _project_locks).
            lock = self._project_locks.get(op.project_id)
            if lock is not None and not lock.locked():
                self._project_locks.pop(op.project_id, None)
        for q in op.sse_subscribers:
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait({"_close": True})
        op.sse_subscribers.clear()

    # ── state transitions ─────────────────────────────────────────────

    def transition(
        self,
        operation_id: str,
        new_status: OperationStatus,
        **extra: Any,
    ) -> None:
        """Move an operation to a new status and broadcast the change.

        Extra keyword arguments are set on the ``OperationState`` (e.g.
        ``error="some message"``).

        Args:
            operation_id: The operation to transition.
            new_status: Target status.
            **extra: Additional attributes to set on the state.
        """
        op = self._ops.get(operation_id)
        if op is None:
            return
        old_status = op.status
        op.status = new_status
        for k, v in extra.items():
            if hasattr(op, k):
                setattr(op, k, v)
        if new_status in TERMINAL_STATUSES:
            self._terminal_times[operation_id] = time.monotonic()
        logger.info(
            "Operation %s: %s → %s (project %s)",
            operation_id[:12],
            old_status.value,
            new_status.value,
            op.project_id[:12],
        )
        payload: dict[str, Any] = {
            "status": new_status.value,
            "previous": old_status.value,
        }
        if extra:
            payload.update(extra)
        self._broadcast(op, SSEEventType.STATUS_CHANGED, payload)

    def add_progress(self, operation_id: str, entry: ProgressEntry) -> None:
        """Append a progress entry and broadcast it.

        Args:
            operation_id: The operation to update.
            entry: The progress log entry.
        """
        op = self._ops.get(operation_id)
        if op is None:
            return
        op.progress.append(entry)
        self._broadcast(
            op,
            SSEEventType.PROGRESS,
            {
                "phase": entry.phase,
                "message": entry.message,
                "timestamp": entry.timestamp,
                "progress": entry.progress,
                "level": entry.level,
            },
        )

    def set_proposals(self, operation_id: str, proposals: list[Proposal]) -> None:
        """Store proposals and transition to AWAITING_APPROVAL.

        Also creates the ``approval_future`` that ``POST /approve`` will resolve.

        Args:
            operation_id: The operation to update.
            proposals: List of AI proposals.
        """
        op = self._ops.get(operation_id)
        if op is None:
            return
        op.proposals = proposals
        loop = asyncio.get_running_loop()
        op.approval_future = loop.create_future()
        self.transition(operation_id, OperationStatus.AWAITING_APPROVAL)
        self._broadcast(
            op,
            SSEEventType.PROPOSALS,
            {
                "proposals": [
                    {
                        "id": p.id,
                        "rule_id": p.rule_id,
                        "file": p.file,
                        "tier": p.tier,
                        "confidence": p.confidence,
                        "explanation": p.explanation,
                        "diff_hunk": p.diff_hunk,
                        "status": p.status,
                        "suggestion": p.suggestion,
                        "line_start": p.line_start,
                    }
                    for p in proposals
                ],
            },
        )

    def broadcast_proposal_updated(self, operation_id: str, proposals: list[dict[str, Any]]) -> None:
        """Broadcast optimistic draft updates (ADR-062 Phase 2).

        Args:
            operation_id: The operation to notify.
            proposals: Updated proposal detail dicts.
        """
        op = self._ops.get(operation_id)
        if op is None:
            return
        self._broadcast(op, SSEEventType.PROPOSAL_UPDATED, {"proposals": proposals})

    def set_result(self, operation_id: str, result: OperationResult) -> None:
        """Store the operation result and transition to COMPLETED.

        Args:
            operation_id: The operation to update.
            result: Aggregated operation result.
        """
        op = self._ops.get(operation_id)
        if op is None:
            return
        op.result = result
        self._broadcast(
            op,
            SSEEventType.RESULT,
            {
                "total_violations": result.total_violations,
                "fixable": result.fixable,
                "ai_proposed": result.ai_proposed,
                "ai_declined": result.ai_declined,
                "ai_accepted": result.ai_accepted,
                "manual_review": result.manual_review,
                "remediated_count": result.remediated_count,
                "fixed_violations": result.fixed_violations,
                "patches": result.patches,
            },
        )
        self.transition(operation_id, OperationStatus.COMPLETED)

    def set_pr_url(self, operation_id: str, pr_url: str) -> None:
        """Store the PR URL and transition to PR_SUBMITTED.

        Args:
            operation_id: The operation to update.
            pr_url: URL of the created pull request.
        """
        op = self._ops.get(operation_id)
        if op is None:
            return
        op.pr_url = pr_url
        self.transition(operation_id, OperationStatus.PR_SUBMITTED)
        self._broadcast(op, SSEEventType.PR_CREATED, {"pr_url": pr_url})

    # ── SSE subscriber management ─────────────────────────────────────

    def subscribe(self, operation_id: str) -> asyncio.Queue[dict[str, Any]] | None:
        """Add an SSE subscriber queue for an operation.

        Args:
            operation_id: The operation to subscribe to.

        Returns:
            An asyncio.Queue that will receive delta events,
            or ``None`` if the operation does not exist.
        """
        op = self._ops.get(operation_id)
        if op is None:
            return None
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        op.sse_subscribers.append(q)
        return q

    def unsubscribe(self, operation_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove an SSE subscriber queue.

        Args:
            operation_id: The operation the queue belongs to.
            queue: The queue to remove.
        """
        op = self._ops.get(operation_id)
        if op is None:
            return
        with contextlib.suppress(ValueError):
            op.sse_subscribers.remove(queue)

    # ── internal helpers ──────────────────────────────────────────────

    def _broadcast(self, op: OperationState, event_type: SSEEventType, data: dict[str, Any]) -> None:
        """Push an event to all SSE subscriber queues for an operation.

        Args:
            op: The operation state.
            event_type: SSE event type identifier.
            data: Event payload.
        """
        msg = {"event": event_type.value, "data": data}
        dead: list[asyncio.Queue[dict[str, Any]]] = []
        for q in op.sse_subscribers:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                dead.append(q)
                logger.warning("Dropping slow SSE subscriber for operation %s", op.operation_id[:12])
        for q in dead:
            with contextlib.suppress(ValueError):
                op.sse_subscribers.remove(q)

    async def _reap_loop(self) -> None:
        """Periodically evict terminal operations past their TTL."""
        while True:
            await asyncio.sleep(30)
            now = time.monotonic()
            expired = [op_id for op_id, ts in self._terminal_times.items() if (now - ts) >= self._terminal_ttl]
            for op_id in expired:
                logger.debug("Reaping terminal operation %s", op_id[:12])
                self._remove(op_id)


_registry: OperationRegistry | None = None


def get_operation_registry() -> OperationRegistry:
    """Return the global singleton ``OperationRegistry``.

    Creates one on first call.

    Returns:
        The global registry instance.
    """
    global _registry  # noqa: PLW0603
    if _registry is None:
        _registry = OperationRegistry()
    return _registry


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns:
        ISO-formatted UTC timestamp.
    """
    return datetime.now(timezone.utc).isoformat()
