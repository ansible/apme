"""Pydantic response models for the REST API."""

from __future__ import annotations

from pydantic import BaseModel


class SessionSummary(BaseModel):  # type: ignore[misc]
    """Session list item.

    Attributes:
        session_id: Deterministic project hash.
        project_path: Filesystem path of the project.
        first_seen: ISO 8601 timestamp of first event.
        last_seen: ISO 8601 timestamp of most recent event.
    """

    session_id: str
    project_path: str
    first_seen: str
    last_seen: str


class ScanSummary(BaseModel):  # type: ignore[misc]
    """Scan list item.

    Attributes:
        scan_id: UUID of the scan run.
        session_id: Owning session hash.
        project_path: Project root path.
        source: Origin of the scan (cli, ci, gateway).
        created_at: ISO 8601 timestamp.
        scan_type: Either "scan" or "fix".
        total_violations: Total violation count.
        auto_fixable: Count of tier-1 fixable violations.
        ai_candidate: Count of tier-2 AI-candidate violations.
        manual_review: Count of tier-3 manual violations.
    """

    scan_id: str
    session_id: str
    project_path: str
    source: str
    created_at: str
    scan_type: str
    total_violations: int
    auto_fixable: int
    ai_candidate: int
    manual_review: int


class ViolationDetail(BaseModel):  # type: ignore[misc]
    """Violation row.

    Attributes:
        id: Auto-increment ID.
        rule_id: Rule identifier (e.g. L001).
        level: Severity level string.
        message: Human-readable description.
        file: Relative file path.
        line: Line number or None.
        path: YAML path within the file.
        remediation_class: Numeric remediation tier.
        scope: Numeric rule scope.
    """

    id: int
    rule_id: str
    level: str
    message: str
    file: str
    line: int | None
    path: str
    remediation_class: int
    scope: int


class ProposalDetail(BaseModel):  # type: ignore[misc]
    """Proposal row.

    Attributes:
        id: Auto-increment ID.
        proposal_id: Engine-generated proposal UUID.
        rule_id: Rule that triggered the proposal.
        file: File the proposal targets.
        tier: Proposal tier (2 or 3).
        confidence: AI confidence score.
        status: approved, rejected, or pending.
    """

    id: int
    proposal_id: str
    rule_id: str
    file: str
    tier: int
    confidence: float
    status: str


class LogEntry(BaseModel):  # type: ignore[misc]
    """Pipeline log entry.

    Attributes:
        id: Auto-increment ID.
        message: Log message text.
        phase: Pipeline subsystem.
        progress: Progress fraction 0.0-1.0.
        level: Numeric log level.
    """

    id: int
    message: str
    phase: str
    progress: float
    level: int


class ScanDetail(BaseModel):  # type: ignore[misc]
    """Full scan with violations, proposals, and logs.

    Attributes:
        scan_id: UUID of the scan run.
        session_id: Owning session hash.
        project_path: Project root path.
        source: Origin of the scan.
        created_at: ISO 8601 timestamp.
        scan_type: Either "scan" or "fix".
        total_violations: Total violation count.
        auto_fixable: Count of tier-1 fixable violations.
        ai_candidate: Count of tier-2 AI-candidate violations.
        manual_review: Count of tier-3 manual violations.
        diagnostics_json: Raw diagnostics JSON string.
        violations: List of violation rows.
        proposals: List of proposal rows.
        logs: List of log entries.
    """

    scan_id: str
    session_id: str
    project_path: str
    source: str
    created_at: str
    scan_type: str
    total_violations: int
    auto_fixable: int
    ai_candidate: int
    manual_review: int
    diagnostics_json: str | None
    violations: list[ViolationDetail]
    proposals: list[ProposalDetail]
    logs: list[LogEntry]


class SessionDetail(BaseModel):  # type: ignore[misc]
    """Session with its scans.

    Attributes:
        session_id: Deterministic project hash.
        project_path: Filesystem path.
        first_seen: First event timestamp.
        last_seen: Most recent event timestamp.
        scans: List of scans in this session.
    """

    session_id: str
    project_path: str
    first_seen: str
    last_seen: str
    scans: list[ScanSummary]


class TopViolation(BaseModel):  # type: ignore[misc]
    """Top-violated rule.

    Attributes:
        rule_id: Rule identifier.
        count: Number of times the rule was violated.
    """

    rule_id: str
    count: int


class PaginatedResponse(BaseModel):  # type: ignore[misc]
    """Wrapper for paginated list responses.

    Attributes:
        total: Total number of items matching the query.
        limit: Page size.
        offset: Current offset.
        items: List of result items.
    """

    total: int
    limit: int
    offset: int
    items: list[SessionSummary] | list[ScanSummary] | list[TopViolation]


class HealthStatus(BaseModel):  # type: ignore[misc]
    """Gateway health response.

    Attributes:
        status: Overall health (ok or degraded).
        database: Database connectivity status.
    """

    status: str
    database: str
