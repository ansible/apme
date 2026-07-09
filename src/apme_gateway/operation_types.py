"""Types for the project operation lifecycle (ADR-052).

Defines the ``OperationStatus`` enum, ``OperationState`` dataclass, and
SSE event type constants used by the ``OperationRegistry`` and the
REST/SSE endpoints for project operations.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class OperationStatus(str, Enum):
    """Lifecycle states for a project operation.

    Terminal states: ``COMPLETED``, ``PR_SUBMITTED``, ``FAILED``,
    ``EXPIRED``, ``CANCELLED``.

    Attributes:
        QUEUED: Operation is queued for execution.
        CLONING: Repository is being cloned.
        SCANNING: Engine is scanning the project.
        AWAITING_APPROVAL: AI proposals require user review.
        APPLYING: Approved fixes are being applied.
        COMPLETED: Operation finished successfully.
        SUBMITTING_PR: Pull request is being created.
        PR_SUBMITTED: Pull request was created.
        FAILED: Operation encountered an error.
        EXPIRED: Operation session expired.
        CANCELLED: Operation was cancelled by the user.
    """

    QUEUED = "queued"
    CLONING = "cloning"
    SCANNING = "scanning"
    AWAITING_APPROVAL = "awaiting_approval"
    APPLYING = "applying"
    COMPLETED = "completed"
    SUBMITTING_PR = "submitting_pr"
    PR_SUBMITTED = "pr_submitted"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


TERMINAL_STATUSES: frozenset[OperationStatus] = frozenset(
    {
        OperationStatus.COMPLETED,
        OperationStatus.PR_SUBMITTED,
        OperationStatus.FAILED,
        OperationStatus.EXPIRED,
        OperationStatus.CANCELLED,
    }
)


@dataclass
class ProgressEntry:
    """Single progress log entry from the engine.

    Attributes:
        phase: Processing phase name (e.g. ``parsing``, ``validating``).
        message: Human-readable progress message.
        timestamp: UTC ISO-8601 timestamp.
        progress: Optional 0–100 percent.
        level: Optional severity/verbosity level.
    """

    phase: str
    message: str
    timestamp: str
    progress: float | None = None
    level: int | None = None


@dataclass
class Proposal:
    """AI remediation proposal from Primary.

    Attributes:
        id: Unique proposal identifier.
        rule_id: Rule that triggered this proposal.
        file: Target file path.
        tier: Remediation tier (1 = deterministic, 2 = AI).
        confidence: 0.0–1.0 confidence score.
        explanation: Human-readable rationale.
        diff_hunk: Unified diff showing the proposed change.
        status: ``proposed`` or ``declined``.
        suggestion: Suggested replacement text.
        line_start: Starting line number in the file.
    """

    id: str
    rule_id: str
    file: str
    tier: int = 0
    confidence: float = 0.0
    explanation: str = ""
    diff_hunk: str = ""
    status: str = "proposed"
    suggestion: str = ""
    line_start: int = 0


@dataclass
class OperationResult:
    """Aggregated result of a completed operation.

    Attributes:
        total_violations: Total violations found.
        fixable: Number of auto-fixable violations.
        ai_proposed: Number of AI proposals sent.
        ai_declined: Number declined by confidence filter.
        ai_accepted: Number accepted by the user.
        manual_review: Remaining violations needing manual review.
        remediated_count: Total violations remediated.
        fixed_violations: Details of each fixed violation.
        patches: File patches produced by remediation.
    """

    total_violations: int = 0
    fixable: int = 0
    ai_proposed: int = 0
    ai_declined: int = 0
    ai_accepted: int = 0
    manual_review: int = 0
    remediated_count: int = 0
    fixed_violations: list[dict[str, Any]] = field(default_factory=list)
    patches: list[dict[str, str]] = field(default_factory=list)


@dataclass
class OperationState:
    """Full mutable state of a single project operation.

    Managed exclusively by the ``OperationRegistry``.  SSE clients
    receive serialised snapshots and delta events derived from this
    state.

    Attributes:
        operation_id: Unique operation identifier (UUID hex).
        project_id: Owning project UUID.
        scan_id: Engine scan identifier.
        status: Current lifecycle state.
        scan_type: ``check`` or ``remediate``.
        started_at: UTC datetime when the operation was created.
        progress: Append-only progress log.
        proposals: Set when status is ``awaiting_approval``.
        result: Set when status is ``completed``.
        pr_url: Set when status is ``pr_submitted``.
        error: Set when status is ``failed``.
        clone_commit: HEAD SHA of the cloned repository.
        grpc_task: The background asyncio.Task driving Primary.
        approval_future: Resolved by ``POST /approve``.
        sse_subscribers: One queue per connected SSE client.
    """

    operation_id: str
    project_id: str
    scan_id: str
    status: OperationStatus
    scan_type: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    progress: list[ProgressEntry] = field(default_factory=list)
    proposals: list[Proposal] | None = None
    result: OperationResult | None = None
    pr_url: str | None = None
    error: str | None = None
    clone_commit: str = ""
    grpc_task: asyncio.Task[Any] | None = field(default=None, repr=False)
    approval_future: asyncio.Future[list[str]] | None = field(default=None, repr=False)
    sse_subscribers: list[asyncio.Queue[dict[str, Any]]] = field(default_factory=list, repr=False)

    def to_snapshot(self) -> dict[str, Any]:
        """Serialise the full state for an SSE ``snapshot`` event or REST ``GET /``.

        Returns:
            JSON-serialisable dictionary.
        """
        data: dict[str, Any] = {
            "operation_id": self.operation_id,
            "project_id": self.project_id,
            "scan_id": self.scan_id,
            "status": self.status.value,
            "scan_type": self.scan_type,
            "started_at": self.started_at.isoformat(),
            "progress": [
                {
                    "phase": p.phase,
                    "message": p.message,
                    "timestamp": p.timestamp,
                    "progress": p.progress,
                    "level": p.level,
                }
                for p in self.progress
            ],
        }
        if self.proposals is not None:
            data["proposals"] = [
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
                for p in self.proposals
            ]
        if self.result is not None:
            data["result"] = {
                "total_violations": self.result.total_violations,
                "fixable": self.result.fixable,
                "ai_proposed": self.result.ai_proposed,
                "ai_declined": self.result.ai_declined,
                "ai_accepted": self.result.ai_accepted,
                "manual_review": self.result.manual_review,
                "remediated_count": self.result.remediated_count,
                "fixed_violations": self.result.fixed_violations,
                "patches": self.result.patches,
            }
        if self.pr_url is not None:
            data["pr_url"] = self.pr_url
        if self.error is not None:
            data["error"] = self.error
        if self.clone_commit:
            data["clone_commit"] = self.clone_commit
        return data


class SSEEventType(str, Enum):
    """Server-Sent Event type identifiers.

    Attributes:
        SNAPSHOT: Full state sent on initial SSE connect.
        STATUS_CHANGED: Status transition delta.
        PROGRESS: New progress log entry.
        PROPOSALS: AI proposals delivered.
        PROPOSAL_UPDATED: Optimistic draft status change (ADR-062 Phase 2).
        RESULT: Operation completed with results.
        APPROVAL_ACK: Approval acknowledged.
        PR_CREATED: Pull request URL available.
        ERROR: Operation failed.
    """

    SNAPSHOT = "snapshot"
    STATUS_CHANGED = "status_changed"
    PROGRESS = "progress"
    PROPOSALS = "proposals"
    PROPOSAL_UPDATED = "proposal_updated"
    RESULT = "result"
    APPROVAL_ACK = "approval_ack"
    PR_CREATED = "pr_created"
    ERROR = "error_event"
