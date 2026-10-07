"""Pydantic response models for the REST API."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


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


class ActivitySummary(BaseModel):  # type: ignore[misc]
    """Activity list item (a persisted check or remediate run).

    Attributes:
        scan_id: UUID of the run (``scans.scan_id`` column).
        session_id: Owning session hash.
        project_path: Project root path.
        project_id: Owning project UUID when linked (None for CLI/playground).
        source: Origin of the run (cli, ci, gateway).
        created_at: ISO 8601 timestamp.
        scan_type: Either ``check`` or ``remediate`` (stored in ``scans.scan_type``).
        total_violations: Total violation count (initial, before Tier 1 fixes).
        fixable: Tier-1 auto-fixable violation count (applied or dry-run).
        ai_candidate: Count of tier-2 AI-candidate violations.
        ai_proposed: AI proposals offered to the user.
        ai_declined: Violations the AI could not fix.
        ai_accepted: AI proposals the user approved and applied.
        manual_review: Count of tier-3 manual violations.
        remediated_count: Total applied (Tier 1 + AI accepted).
        pr_url: URL of the PR created from this activity (ADR-050), if any.
        branch_name: Head branch pushed during SCM submit (ADR-050), if any.
        commit_sha: SHA of the commit pushed during SCM submit (ADR-050), if any.
    """

    scan_id: str
    session_id: str
    project_path: str
    project_id: str | None = None
    source: str
    created_at: str
    scan_type: str
    total_violations: int
    fixable: int
    ai_candidate: int
    ai_proposed: int = 0
    ai_declined: int = 0
    ai_accepted: int = 0
    manual_review: int
    remediated_count: int = 0
    pr_url: str | None = None
    branch_name: str | None = None
    commit_sha: str | None = None


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
        remediation_resolution: Numeric remediation resolution status.
        scope: Numeric rule scope.
        validator_source: Validator that produced this (native, opa, ansible, gitleaks).
        original_yaml: Full node YAML as originally written.
        fixed_yaml: Node YAML after transforms (fixed violations only).
        co_fixes: Other rule IDs whose fixes are included in this node's diff.
        node_line_start: File line where the node starts.
        node_type: ContentGraph NodeType value (task, block, play, …).
        ai_reason: Why the AI could not fix this violation (ai_abstained only).
        ai_suggestion: Manual remediation guidance from the AI (ai_abstained only).
        audit_metadata: Parsed audit rule payloads when present.
        suppressed: True if this violation matches an active suppression (ADR-055).
        review_status: Human/gate decision (ADR-062); null if never reviewed.
    """

    id: int
    rule_id: str
    level: str
    message: str
    file: str
    line: int | None
    path: str
    remediation_class: int
    remediation_resolution: int = 0
    scope: int
    validator_source: str = ""
    original_yaml: str = ""
    fixed_yaml: str = ""
    co_fixes: list[str] = Field(default_factory=list)
    node_line_start: int = 0
    node_type: str = ""
    ai_reason: str = ""
    ai_suggestion: str = ""
    audit_metadata: dict[str, object] | None = None
    suppressed: bool = False
    review_status: str | None = None


class ProposalDetail(BaseModel):  # type: ignore[misc]
    """Proposal row (ephemeral working set or historically rebuilt; ADR-062).

    Attributes:
        id: Auto-increment ID (0 when rebuilt in memory).
        proposal_id: Gateway-stable proposal id within the scan.
        rule_id: Primary rule that triggered the proposal.
        file: File the proposal targets.
        tier: Proposal tier (1 deterministic, 2+ AI).
        confidence: AI confidence score.
        status: proposed, approved, rejected, declined, or pending.
        path: Node identity path (optional additive).
        node_type: ContentGraph NodeType value (task, block, play, …).
        source: deterministic, ai, ai-candidate, or outcome (optional additive).
        gate: tier1 or ai (optional additive).
        rule_ids: All rule ids on this approval unit (optional additive).
        violation_ids: Linked violation PKs (optional additive).
        line_start: First line of the node/finding, 0 when unknown (optional additive).
        line_end: Last line of the node/finding, 0 when unknown (optional additive).
        diff_hunk: Unified diff when available (optional additive).
        explanation: AI explanation when available (optional additive).
        suggestion: Manual suggestion when available (optional additive).
        engine_proposal_id: Live engine proposal id when bridged (optional).
        draft: True while optimistic UI edits are not yet gate-committed.
    """

    id: int
    proposal_id: str
    rule_id: str
    file: str
    tier: int
    confidence: float
    status: str
    path: str = ""
    node_type: str = ""
    source: str = "outcome"
    gate: str = ""
    rule_ids: list[str] = Field(default_factory=list)
    violation_ids: list[int] = Field(default_factory=list)
    line_start: int = Field(default=0, ge=0)
    line_end: int = Field(default=0, ge=0)
    diff_hunk: str = ""
    explanation: str = ""
    suggestion: str = ""
    engine_proposal_id: str | None = None
    draft: bool = False


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


class PatchDetail(BaseModel):  # type: ignore[misc]
    """Per-file diff from a check or remediate run.

    Attributes:
        id: Auto-increment ID.
        file: Relative file path.
        diff: Unified diff text.
    """

    id: int
    file: str
    diff: str


class ActivityDetail(BaseModel):  # type: ignore[misc]
    """Full activity record with violations, proposals, logs, and patches.

    Attributes:
        scan_id: UUID of the run (``scans.scan_id`` column).
        session_id: Owning session hash.
        project_id: Owning project UUID (None for CLI/playground runs).
        project_path: Project root path.
        source: Origin of the run.
        created_at: ISO 8601 timestamp.
        scan_type: Either ``check`` or ``remediate`` (stored in ``scans.scan_type``).
        total_violations: Total violation count (initial, before Tier 1 fixes).
        fixable: Tier-1 auto-fixable violation count (applied or dry-run).
        ai_candidate: Count of tier-2 AI-candidate violations.
        ai_proposed: AI proposals offered to the user.
        ai_declined: Violations the AI could not fix.
        ai_accepted: AI proposals the user approved and applied.
        manual_review: Count of tier-3 manual violations.
        remediated_count: Total applied (Tier 1 + AI accepted).
        pr_url: URL of the PR created from this activity (ADR-050), if any.
        branch_name: Head branch pushed during SCM submit (ADR-050), if any.
        commit_sha: SHA of the commit pushed during SCM submit (ADR-050), if any.
        diagnostics_json: Raw diagnostics JSON string.
        violations: List of violation rows.
        proposals: List of proposal rows.
        logs: List of log entries.
        patches: Per-file diffs.
    """

    scan_id: str
    session_id: str
    project_id: str | None = None
    project_path: str
    source: str
    created_at: str
    scan_type: str
    total_violations: int
    fixable: int
    ai_candidate: int
    ai_proposed: int = 0
    ai_declined: int = 0
    ai_accepted: int = 0
    manual_review: int
    remediated_count: int = 0
    pr_url: str | None = None
    branch_name: str | None = None
    commit_sha: str | None = None
    diagnostics_json: str | None
    violations: list[ViolationDetail]
    proposals: list[ProposalDetail]
    logs: list[LogEntry]
    patches: list[PatchDetail] = Field(default_factory=list)


class SessionDetail(BaseModel):  # type: ignore[misc]
    """Session with its activity history.

    Attributes:
        session_id: Deterministic project hash.
        project_path: Filesystem path.
        first_seen: First event timestamp.
        last_seen: Most recent event timestamp.
        scans: List of activity rows for this session (``scans`` table).
    """

    session_id: str
    project_path: str
    first_seen: str
    last_seen: str
    scans: list[ActivitySummary]


class TopViolation(BaseModel):  # type: ignore[misc]
    """Top-violated rule.

    Attributes:
        rule_id: Rule identifier.
        count: Number of times the rule was violated.
    """

    rule_id: str
    count: int


class TrendPoint(BaseModel):  # type: ignore[misc]
    """Violation trend data point for a session.

    Attributes:
        scan_id: UUID of the run (``scans.scan_id`` column).
        created_at: ISO 8601 timestamp.
        total_violations: Total violation count.
        fixable: Tier-1 auto-fixable violation count.
        scan_type: Either ``check`` or ``remediate`` (stored in ``scans.scan_type``).
    """

    scan_id: str
    created_at: str
    total_violations: int
    fixable: int
    scan_type: str


class RemediationRateEntry(BaseModel):  # type: ignore[misc]
    """Remediation frequency for a specific rule.

    Attributes:
        rule_id: Rule identifier.
        fix_count: Number of times this rule appeared in remediate runs.
    """

    rule_id: str
    fix_count: int


class AiAcceptanceEntry(BaseModel):  # type: ignore[misc]
    """AI proposal acceptance statistics per rule.

    Attributes:
        rule_id: Rule identifier.
        approved: Count of approved proposals.
        rejected: Count of rejected proposals.
        pending: Count of pending proposals.
        avg_confidence: Average AI confidence score.
    """

    rule_id: str
    approved: int
    rejected: int
    pending: int
    avg_confidence: float


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
    items: (
        list[SessionSummary]
        | list[ActivitySummary]
        | list[TopViolation]
        | list[ProjectSummary]
        | list[NotificationSchema]
    )


class AiModelInfo(BaseModel):  # type: ignore[misc]
    """AI model available from the Abbenay daemon.

    Attributes:
        id: Model identifier (e.g. ``anthropic/claude-sonnet-4``).
        provider: LLM provider engine name.
        name: Human-readable model name.
    """

    id: str
    provider: str
    name: str


class ComponentHealth(BaseModel):  # type: ignore[misc]
    """Health status for a single service component.

    Attributes:
        name: Human-readable component name.
        status: Health status (ok, unavailable, or degraded).
        address: Network address of the component.
        detail: Optional human-readable detail (e.g. last-push freshness
            for the Galaxy Proxy component).
    """

    name: str
    status: str
    address: str
    detail: str | None = None


class HealthStatus(BaseModel):  # type: ignore[misc]
    """Gateway health response.

    Attributes:
        status: Overall health (ok or degraded).
        database: Database connectivity status.
        database_type: Human-readable database engine name (e.g. PostgreSQL, SQLite).
        components: Health status of each upstream service.
    """

    status: str
    database: str
    database_type: str = "unknown"
    components: list[ComponentHealth] = Field(default_factory=list)


# ── Project schemas (ADR-037, ADR-052) ────────────────────────────────


class ActiveOperationSummary(BaseModel):  # type: ignore[misc]
    """Summary of an in-flight operation for a project.

    Attributes:
        operation_id: Unique operation identifier.
        status: Current lifecycle state.
        scan_type: ``check`` or ``remediate``.
        started_at: ISO-8601 start time.
    """

    operation_id: str
    status: str
    scan_type: str
    started_at: str


class ProjectSummary(BaseModel):  # type: ignore[misc]
    """Summary representation of a project for list views.

    Attributes:
        id: Unique identifier.
        name: Display label.
        repo_url: SCM clone URL.
        branch: Target branch.
        created_at: ISO-8601 creation timestamp.
        health_score: Computed 0-100 score.
        total_violations: Count from latest check (scan row).
        violation_trend: Direction indicator.
        scan_count: Number of completed runs (``scan_count`` / scans table).
        last_scanned_at: ISO timestamp of most recent run (``last_scanned_at`` column).
        scm_provider: Explicit SCM provider type (ADR-050), or None for auto-detect.
        has_scm_token: Whether a project-level SCM token is configured (ADR-050).
        last_scanned_commit: Git SHA of the commit used in the most recent scan.
        has_new_commits: True when the remote branch HEAD is ahead of last_scanned_commit.
        active_operation: Summary of any in-flight operation (ADR-052), or null.
    """

    id: str
    name: str
    repo_url: str
    branch: str
    created_at: str
    health_score: int
    total_violations: int = 0
    violation_trend: str = "stable"
    scan_count: int = 0
    last_scanned_at: str | None = None
    scm_provider: str | None = None
    has_scm_token: bool = False
    last_scanned_commit: str = ""
    has_new_commits: bool = False
    active_operation: ActiveOperationSummary | None = None


class ProjectDetail(ProjectSummary):
    """Full project representation with latest activity summary.

    Attributes:
        latest_scan: Summary of the most recent run, if any (``latest_scan`` field name unchanged).
        severity_breakdown: Violation counts keyed by severity level.
    """

    latest_scan: ActivitySummary | None = None
    severity_breakdown: dict[str, int] = Field(default_factory=dict)


class CreateProjectRequest(BaseModel):  # type: ignore[misc]
    """Request body for creating a project.

    Attributes:
        name: Display label.
        repo_url: HTTPS clone URL.
        branch: Branch to clone (default main). 1-100 chars; letters,
            digits, '.', '_', '/', '-'; must satisfy git check-ref-format
            component rules. Invalid names fail with 422.
        scm_token: Per-project SCM token for PR creation (ADR-050).
        scm_provider: Explicit SCM provider type (ADR-050). Auto-detected if omitted.
    """

    name: str
    repo_url: str
    branch: str = Field(
        default="main",
        max_length=100,
        description=(
            "Branch to clone (default main). 1-100 chars; "
            "letters, digits, '.', '_', '/', '-'; must satisfy git "
            "check-ref-format component rules. Invalid names fail with 422."
        ),
    )
    scm_token: str | None = None
    scm_provider: str | None = None

    @field_validator("branch")  # type: ignore[untyped-decorator]
    @classmethod
    def _validate_branch(cls, v: str) -> str:
        """Reject traversal and git-invalid branch names with a 422.

        Args:
            v: Candidate branch name.

        Returns:
            The validated branch name unchanged.

        Raises:
            ValueError: If the name fails git ref-format validation.
        """
        from apme_gateway.scm.urls import validate_branch_name  # noqa: PLC0415

        validated = validate_branch_name(v)
        if validated is None:
            msg = "branch name validation returned None"
            raise ValueError(msg)
        return validated


class UpdateProjectRequest(BaseModel):  # type: ignore[misc]
    """Partial update for project fields.

    Attributes:
        name: New display label.
        repo_url: New clone URL.
        branch: New branch.
        scm_token: New SCM token (ADR-050). Set to empty string to clear.
        scm_provider: Explicit provider type (ADR-050). Set to empty string to clear.
    """

    name: str | None = None
    repo_url: str | None = None
    branch: str | None = Field(
        default=None,
        max_length=100,
        description=(
            "New branch to clone. 1-100 chars; "
            "letters, digits, '.', '_', '/', '-'; must satisfy git "
            "check-ref-format component rules. Invalid names fail with 422."
        ),
    )
    scm_token: str | None = None
    scm_provider: str | None = None

    @field_validator("branch")  # type: ignore[untyped-decorator]
    @classmethod
    def _validate_branch(cls, v: str | None) -> str | None:
        """Reject traversal and git-invalid branch names with a 422.

        Args:
            v: Candidate branch name (``None`` leaves the field unchanged).

        Returns:
            The validated branch name unchanged.
        """
        from apme_gateway.scm.urls import validate_branch_name  # noqa: PLC0415

        return validate_branch_name(v)


# ── Dependency manifest schemas (ADR-040) ────────────────────────────


class CollectionRefSchema(BaseModel):  # type: ignore[misc]
    """A collection discovered in a project's session venv.

    Attributes:
        fqcn: Fully-qualified collection name.
        version: Installed version string.
        source: Origin — galaxy, local, or git.
        license: SPDX license identifier from collection metadata.
        supplier: Author or namespace from collection metadata.
    """

    fqcn: str
    version: str
    source: str
    license: str = ""
    supplier: str = ""


class PythonPackageRefSchema(BaseModel):  # type: ignore[misc]
    """A Python package discovered in a project's session venv.

    Attributes:
        name: PyPI package name.
        version: Installed version string.
        license: License identifier from package metadata.
        supplier: Author from package metadata.
    """

    name: str
    version: str
    license: str = ""
    supplier: str = ""


class ProjectDependencies(BaseModel):  # type: ignore[misc]
    """Full dependency manifest for a project (ADR-040).

    Attributes:
        ansible_core_version: ansible-core version from the session venv.
        collections: Collections installed in the session venv.
        python_packages: Python packages installed in the session venv.
        requirements_files: Requirement file paths found in the project.
        dependency_tree: Raw ``uv pip tree`` output showing package relationships.
    """

    ansible_core_version: str = ""
    collections: list[CollectionRefSchema] = Field(default_factory=list)
    python_packages: list[PythonPackageRefSchema] = Field(default_factory=list)
    requirements_files: list[str] = Field(default_factory=list)
    dependency_tree: str = ""


class CollectionSummary(BaseModel):  # type: ignore[misc]
    """Collection seen across projects.

    Attributes:
        fqcn: Fully-qualified collection name.
        version: Version from the most recently scanned project.
        source: Classification — specified, learned, or dependency.
        project_count: Number of projects using this collection.
    """

    fqcn: str
    version: str
    source: str
    project_count: int


class CollectionProjectRef(BaseModel):  # type: ignore[misc]
    """A project that depends on a specific collection.

    Attributes:
        id: Project UUID.
        name: Project display label.
        health_score: Project health score.
        collection_version: Version of the collection in this project.
        last_scan_id: Scan ID where this collection was last seen.
    """

    id: str
    name: str
    health_score: int
    collection_version: str
    last_scan_id: str = ""


class CollectionDetail(BaseModel):  # type: ignore[misc]
    """Detail view for a single collection (ADR-040).

    Attributes:
        fqcn: Fully-qualified collection name.
        versions: All version strings seen across projects.
        source: Primary origin.
        project_count: Number of projects using this collection.
        projects: Projects that depend on this collection.
    """

    fqcn: str
    versions: list[str] = Field(default_factory=list)
    source: str = "galaxy"
    project_count: int = 0
    projects: list[CollectionProjectRef] = Field(default_factory=list)


class PythonPackageSummary(BaseModel):  # type: ignore[misc]
    """Python package seen across projects.

    Attributes:
        name: PyPI package name.
        version: Version from the most recently scanned project.
        project_count: Number of projects using this package.
    """

    name: str
    version: str
    project_count: int


class PythonPackageProjectRef(BaseModel):  # type: ignore[misc]
    """A project that depends on a specific Python package.

    Attributes:
        id: Project UUID.
        name: Project display label.
        health_score: Project health score.
        package_version: Version of the package in this project.
        last_scan_id: Scan ID where this package was last seen.
    """

    id: str
    name: str
    health_score: int = 0
    package_version: str = ""
    last_scan_id: str = ""


class PythonPackageDetail(BaseModel):  # type: ignore[misc]
    """Detail view for a single Python package (ADR-040).

    Attributes:
        name: PyPI package name.
        versions: All version strings seen across projects.
        project_count: Number of projects using this package.
        projects: Projects that depend on this package.
    """

    name: str
    versions: list[str] = Field(default_factory=list)
    project_count: int = 0
    projects: list[PythonPackageProjectRef] = Field(default_factory=list)


# ── SCM submit schemas (ADR-050) ─────────────────────────────────────


class SubmitRequest(BaseModel):  # type: ignore[misc]
    """Request body for the unified SCM submit endpoint (ADR-050).

    Pushes patched files to a branch and optionally opens a PR.
    All fields are optional — the Gateway generates sensible defaults.

    When ``activity_id`` is provided, patched files are loaded from the
    database (supports historical scans).  When omitted, the endpoint
    uses the live in-memory operation for the project.

    Attributes:
        activity_id: Optional scan/activity ID to submit from DB history.
        branch_name: Name for the new branch (default auto-generated).
        create_pr: Whether to open a PR after pushing (default ``True``).
        title: PR title (default auto-generated from remediation stats).
        body: PR body in Markdown (default auto-generated).
        scm_token: One-time SCM token (overrides project/global token).
        submit_token: Idempotency token from ``POST /approve`` (N19) or
            client-generated ``Idempotency-Key``; double-submit with the
            same token replays the stored result instead of pushing again.
    """

    activity_id: str | None = None
    branch_name: str | None = Field(
        default=None,
        max_length=100,
        description=(
            "Name for the new branch (default auto-generated). 1-100 chars; "
            "letters, digits, '.', '_', '/', '-'; must satisfy git "
            "check-ref-format component rules. Invalid names fail with 422."
        ),
    )
    create_pr: bool = True
    title: str | None = None
    body: str | None = None
    scm_token: str | None = None
    submit_token: str | None = None

    @field_validator("branch_name")  # type: ignore[untyped-decorator]
    @classmethod
    def _validate_branch_name(cls, v: str | None) -> str | None:
        """Ensure an explicit branch name is safe for SCM ref creation.

        Shared rules live in :func:`apme_gateway.scm.urls.validate_branch_name`
        so REST validation and ``clone_repo`` agree; invalid names raise
        ``ValueError`` from that helper and surface as 422.

        Args:
            v: The branch name value to validate (``None`` selects the
                auto-generated default).

        Returns:
            The validated branch name unchanged.
        """
        from apme_gateway.scm.urls import validate_branch_name  # noqa: PLC0415

        return validate_branch_name(v)


class SubmitResponse(BaseModel):  # type: ignore[misc]
    """Response after a successful SCM submit (ADR-050).

    Attributes:
        branch_name: Name of the head branch that was created.
        commit_sha: SHA of the commit pushed to the branch.
        pr_url: PR URL stored on the activity after persist (existing
            or newly recorded), or ``None`` if none is stored.
        provider: SCM provider that was used (e.g. ``github``).
    """

    branch_name: str
    commit_sha: str
    pr_url: str | None = None
    provider: str


# ── Dependency health schemas (ADR-051) ──────────────────────────────


class CollectionHealthSummary(BaseModel):  # type: ignore[misc]
    """Collection health findings count (ADR-051).

    Attributes:
        fqcn: Fully-qualified collection name.
        finding_count: Total findings from collection health scan.
        critical: Critical-severity count.
        error: Error-severity count.
        high: High-severity count.
        medium: Medium-severity count.
        low: Low-severity count.
        info: Info-severity count.
    """

    fqcn: str
    finding_count: int
    critical: int = 0
    error: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    info: int = 0


class PythonCveSummary(BaseModel):  # type: ignore[misc]
    """Python CVE finding summary (ADR-051).

    Attributes:
        rule_id: Rule identifier (e.g. R200).
        level: Severity level string.
        message: Human-readable CVE description.
        occurrence_count: Number of projects affected.
    """

    rule_id: str
    level: str
    message: str
    occurrence_count: int


class DepHealthSummary(BaseModel):  # type: ignore[misc]
    """Aggregated dependency health findings (ADR-051).

    Attributes:
        collection_findings: Per-collection finding counts.
        python_cves: Per-CVE finding summaries.
        suppressed_count: Number of violations excluded by active suppressions.
    """

    collection_findings: list[CollectionHealthSummary] = Field(default_factory=list)
    python_cves: list[PythonCveSummary] = Field(default_factory=list)
    suppressed_count: int = 0


class OperationRequestOptions(BaseModel):  # type: ignore[misc]
    """Per-operation check/remediate options (ADR-037).

    Attributes:
        ansible_version: Target ansible-core version.
        collection_specs: Galaxy collection install specs.
        enable_ai: Enable AI remediation tier.
        ai_model: Specific model identifier.
    """

    ansible_version: str = ""
    collection_specs: list[str] = Field(default_factory=list)
    enable_ai: bool = False
    ai_model: str = ""


# ── Atomic operate schemas (agent operability, additive) ──────────────


class AtomicOperateSubmitOptions(BaseModel):  # type: ignore[misc]
    """Submit options for atomic ``POST /operate`` (additive).

    Attributes:
        create_pr: Whether to open a PR after pushing.
        branch_name: Explicit branch name (auto-generated when omitted).
        submit_token: Optional idempotency token forwarded to the embedded
            submit (``Idempotency-Key`` header is preferred when present).
    """

    create_pr: bool = True
    branch_name: str | None = None
    submit_token: str | None = None


class AtomicOperateOptions(BaseModel):  # type: ignore[misc]
    """Server-side gate options for atomic ``POST /operate`` (additive).

    Attributes:
        auto_approve_tier1: Auto-approve Tier 1 deterministic proposals.
        auto_approve_ai: Auto-escalate AI candidates and auto-approve AI proposals.
        enable_ai: Enable Tier 2 AI tier (implied by auto_approve_ai).
        ai_model: Optional model override.
        ansible_version: Target ansible-core version.
        collection_specs: Galaxy collection install specs.
        submit: Optional submit-after-complete options.
    """

    auto_approve_tier1: bool = False
    auto_approve_ai: bool = False
    enable_ai: bool = False
    ai_model: str = ""
    ansible_version: str = ""
    collection_specs: list[str] = Field(default_factory=list)
    submit: AtomicOperateSubmitOptions | None = None


class AtomicOperateRequest(BaseModel):  # type: ignore[misc]
    """Request body for atomic ``POST /api/v1/projects/{id}/operate``.

    Attributes:
        action: ``check`` or ``remediate``. Defaults to ``check`` (unlike
            ``OperateRequest.action``, which is required): a bare POST with
            no body is read-only by default so agent one-call automation
            cannot accidentally start a remediate.
        options: Atomic gate/submit options.
        abandon_working_set: Allow flush of interactive draft working set.
    """

    action: str = Field(default="check", pattern="^(check|remediate)$")
    options: AtomicOperateOptions = Field(default_factory=AtomicOperateOptions)
    abandon_working_set: bool = False


# ── External scan import schemas (CLI-local -> Gateway, additive) ─────


class ImportViolationSchema(BaseModel):  # type: ignore[misc]
    """One violation row for ``POST /api/v1/scans/import`` (additive).

    Attributes:
        rule_id: Rule identifier (e.g. L001).
        level: Severity level string.
        message: Human-readable description.
        file: Relative file path.
        line: Line number or None.
        path: YAML path within the file.
        remediation_class: Remediation tier label (``auto-fixable``,
            ``ai-candidate``, ``manual-review``) as emitted by
            ``apme check --json``. Empty when unknown — the import
            endpoint counts such rows as ``manual_review`` (pending
            triage) and stores ``0`` (unspecified) on the violation row.
    """

    rule_id: str = Field(default="", max_length=64)
    level: str = Field(default="warning", max_length=16)
    message: str = Field(default="", max_length=4000)
    file: str = Field(default="", max_length=1000)
    line: int | None = None
    path: str = Field(default="", max_length=2000)
    remediation_class: str = Field(default="", max_length=32)


class ImportScanRequest(BaseModel):  # type: ignore[misc]
    """Request body for ``POST /api/v1/scans/import`` (additive).

    Stores CLI-local ``apme check --json`` output as Gateway activity
    without requiring a registered project scan run.

    Per-field ``max_length`` caps plus the 5000-row cap bound the
    worst-case JSON payload; total request-body size itself is bounded
    by the ASGI server / reverse-proxy max-body setting (not by
    Pydantic — FastAPI has no per-route body-size knob).

    Attributes:
        project_id: Optional registered project UUID to link.
        project_path: Local project path label (stored on the scan row).
        scan_type: ``check`` or ``remediate``.
        violations: Violation rows from ``apme check --json`` (max 5000).
        source: Origin label (default ``cli``).
    """

    project_id: str | None = Field(default=None, max_length=100)
    project_path: str = Field(default="external", max_length=1000)
    scan_type: str = Field(default="check", pattern="^(check|remediate)$")
    violations: list[ImportViolationSchema] = Field(default_factory=list, max_length=5000)
    source: str = Field(default="cli", max_length=32)


class ImportScanResponse(BaseModel):  # type: ignore[misc]
    """Response for ``POST /api/v1/scans/import`` (additive).

    Attributes:
        scan_id: UUID of the stored run.
        session_id: Owning session hash.
        violation_count: Number of stored violation rows.
    """

    scan_id: str
    session_id: str
    violation_count: int


# ── Project format schemas (additive) ──────────────────────────────────


class ProjectFormatRequest(BaseModel):  # type: ignore[misc]
    """Request body for ``POST /api/v1/projects/{id}/format`` (additive).

    Attributes:
        branch: Branch override (defaults to the project branch).
    """

    branch: str | None = Field(
        default=None,
        max_length=100,
        description=(
            "Branch override (defaults to the project branch). 1-100 chars; "
            "letters, digits, '.', '_', '/', '-'; must satisfy git "
            "check-ref-format component rules. Invalid names fail with 422."
        ),
    )

    @field_validator("branch")  # type: ignore[untyped-decorator]
    @classmethod
    def _validate_branch(cls, v: str | None) -> str | None:
        """Ensure a branch override is safe before cloning.

        Shared rules live in :func:`apme_gateway.scm.urls.validate_branch_name`
        so REST validation and ``clone_repo`` agree; invalid names raise
        ``ValueError`` from that helper and surface as 422.

        Args:
            v: The branch override (``None`` selects the project branch).

        Returns:
            The validated branch name unchanged.
        """
        from apme_gateway.scm.urls import validate_branch_name  # noqa: PLC0415

        return validate_branch_name(v)


class ProjectFormatFileDiff(BaseModel):  # type: ignore[misc]
    """One formatted file diff (additive).

    Attributes:
        path: Relative file path.
        diff: Unified diff text.
    """

    path: str
    diff: str = ""


class ProjectFormatResponse(BaseModel):  # type: ignore[misc]
    """Response for ``POST /api/v1/projects/{id}/format`` (additive).

    Attributes:
        project_id: Owning project UUID.
        commit: HEAD SHA of the cloned repo.
        diffs: Per-file format diffs.
    """

    project_id: str
    commit: str = ""
    diffs: list[ProjectFormatFileDiff] = Field(default_factory=list)


# ── Session venv schemas (read-only, additive) ─────────────────────────


class SessionVenvInfo(BaseModel):  # type: ignore[misc]
    """Read-only session venv view (additive, Engine-owned venvs).

    Sourced from persisted Gateway session/scan rows only — never writes
    to Engine venv state (ADR-022 single-writer invariant).

    Attributes:
        session_id: Deterministic session hash.
        project_path: Filesystem path of the project.
        requirements_hash: Hash of the requirements set for the session.
            Legacy name — superseded by ``manifest_hash``; both carry the
            same value.
        manifest_hash: Canonical name for the requirements-set hash
            (additive alias of ``requirements_hash``).
        ansible_core_version: ansible-core version from the latest manifest.
        age_seconds: Session dwell in seconds (``last_seen`` minus
            ``first_seen``), not wall-clock age since creation. The field
            name is kept for REST contract stability (ADR-060).
        last_seen: ISO 8601 timestamp of most recent event.
    """

    session_id: str
    project_path: str = ""
    requirements_hash: str = ""
    manifest_hash: str = ""
    ansible_core_version: str = ""
    age_seconds: int = 0
    last_seen: str = ""


# ── Galaxy server schemas (ADR-045) ──────────────────────────────────


class GalaxyServerSchema(BaseModel):  # type: ignore[misc]
    """A globally configured Galaxy/Automation Hub server.

    Attributes:
        id: Auto-increment primary key.
        name: Short label (e.g. ``automation_hub``).
        url: Base URL of the Galaxy / Automation Hub API.
        auth_url: SSO/Keycloak token endpoint (empty if not applicable).
        has_token: Whether a token is configured (token value is never exposed).
        created_at: ISO 8601 creation timestamp.
        updated_at: ISO 8601 last-update timestamp.
    """

    id: int
    name: str
    url: str
    auth_url: str = ""
    has_token: bool = False
    created_at: str
    updated_at: str


def _require_https_galaxy_url(url: str) -> str:
    """Reject Galaxy server URLs the proxy would refuse with 422.

    Mirrors the proxy's scheme/userinfo/host gate (including the
    encoded-literal and IPv4-mapped-IPv6 traps) so one bad row is
    rejected at the row that caused it instead of failing the entire
    pushed server list at sync time. Existing ``http://`` rows should
    be updated to ``https://`` or deleted; they can no longer be created.

    Args:
        url: Galaxy server base API URL.

    Returns:
        The unchanged URL.

    Raises:
        ValueError: If the URL is not ``https://`` with a host, embeds
            userinfo, or targets a local/link-local literal address.
    """
    parsed = urlsplit((url or "").strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"Galaxy server URL must use https with a host: {url!r}")
    if parsed.username or parsed.password:
        raise ValueError(f"Galaxy server URL must not embed userinfo credentials: {url!r}")
    host = parsed.hostname
    bare_host = host.rstrip(".")
    if bare_host == "localhost" or bare_host.endswith(".localhost"):
        raise ValueError(f"Galaxy server URL must not target a local/link-local address: {url!r}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            socket.inet_aton(host)
        except OSError:
            return url  # Hostname: cannot judge without DNS; allowed.
        raise ValueError(f"Galaxy server URL must not target a local/link-local address: {url!r}") from None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        raise ValueError(f"Galaxy server URL must not target a local/link-local address: {url!r}")
    return url


class CreateGalaxyServerRequest(BaseModel):  # type: ignore[misc]
    """Request body for creating a Galaxy server.

    Attributes:
        name: Short label.
        url: Base API URL (``https://`` only — mirrors the proxy's 422 gate).
        token: API token (optional, empty for public Galaxy).
        auth_url: SSO/Keycloak token endpoint (optional).
    """

    name: str
    url: str
    token: str = ""
    auth_url: str = ""

    @field_validator("url")  # type: ignore[untyped-decorator]
    @classmethod
    def _validate_url(cls, url: str) -> str:
        """Reject non-HTTPS Galaxy server URLs before storing.

        Args:
            url: Galaxy server base API URL.

        Returns:
            The unchanged URL.
        """
        return _require_https_galaxy_url(url)


class UpdateGalaxyServerRequest(BaseModel):  # type: ignore[misc]
    """Partial update for Galaxy server fields.

    Attributes:
        name: New display label.
        url: New base API URL (``https://`` only when provided).
        token: New API token (omit or None to leave unchanged).
        auth_url: New SSO endpoint.
    """

    name: str | None = None
    url: str | None = None
    token: str | None = None
    auth_url: str | None = None

    @field_validator("url")  # type: ignore[untyped-decorator]
    @classmethod
    def _validate_url(cls, url: str | None) -> str | None:
        """Reject non-HTTPS Galaxy server URLs before storing.

        Args:
            url: New base API URL, or None to leave unchanged.

        Returns:
            The unchanged URL.
        """
        if url is None:
            return None
        return _require_https_galaxy_url(url)


class DashboardSummary(BaseModel):  # type: ignore[misc]
    """Cross-project aggregate statistics (ADR-037).

    Attributes:
        total_projects: Number of defined projects.
        total_scans: Number of completed runs across all projects (``total_scans`` column).
        total_violations: Cumulative violations across all runs.
        current_violations: Violations from each project's latest run.
        current_fixable: Auto-fixable violations from each project's latest run.
        current_ai_candidates: AI-candidate violations from each project's latest run.
        total_fixed: Sum of remediated violations (``total_fixed`` field name unchanged).
        avg_health_score: Mean health score across projects.
    """

    total_projects: int
    total_scans: int
    total_violations: int
    current_violations: int
    current_fixable: int
    current_ai_candidates: int
    total_fixed: int
    avg_health_score: int


class ProjectRanking(BaseModel):  # type: ignore[misc]
    """Project ranking entry for dashboard tables (ADR-037).

    Attributes:
        id: Project identifier.
        name: Display label.
        health_score: Computed 0-100 score.
        total_violations: Latest run violation count.
        scan_count: Number of completed runs (``scan_count`` column).
        last_scanned_at: ISO timestamp of most recent run (``last_scanned_at`` column).
        days_since_last_scan: Age in days since last run (``days_since_last_scan`` column).
    """

    id: str
    name: str
    health_score: int
    total_violations: int
    scan_count: int
    last_scanned_at: str | None = None
    days_since_last_scan: int | None = None


# ── Suppression schemas (ADR-055) ─────────────────────────────────────


class SuppressionSchema(BaseModel):  # type: ignore[misc]
    """A fingerprint-based violation suppression (ADR-055).

    Attributes:
        id: Auto-increment PK.
        fingerprint_hash: SHA-256 hex digest.
        fingerprint_mode: Granularity — ``full`` or ``rule_only``.
        rule_id: Canonical rule identifier.
        scope: ``global`` or ``project:<uuid>``.
        reason: Human justification.
        created_by: Author of the suppression.
        created_at: ISO 8601 creation timestamp.
    """

    id: int
    fingerprint_hash: str
    fingerprint_mode: str
    rule_id: str
    scope: str
    reason: str
    created_by: str
    created_at: str


class CreateSuppressionRequest(BaseModel):  # type: ignore[misc]
    """Request body for acknowledging/suppressing a violation (ADR-055).

    When ``original_yaml`` is provided the server computes the canonical
    fingerprint (normalized YAML, matching the CLI). The ``fingerprint_hash``
    field is then ignored. When ``original_yaml`` is absent, the server
    trusts the caller-supplied ``fingerprint_hash`` (backward compat).

    Attributes:
        fingerprint_hash: Pre-computed SHA-256 hex digest (used only when
            ``original_yaml`` is not supplied).
        fingerprint_mode: Granularity — ``full`` or ``rule_only``.
        rule_id: Canonical rule identifier.
        original_yaml: Raw YAML source for server-side fingerprint computation.
        module_fqcn: Module FQCN (reserved for future use).
        scope: ``global`` or ``project:<uuid>``.
        reason: Human justification for the acknowledgment.
    """

    fingerprint_hash: str = Field(default="", max_length=64)
    fingerprint_mode: str = "full"
    rule_id: str = Field(max_length=200)
    original_yaml: str | None = Field(default=None, max_length=1_048_576)
    module_fqcn: str = Field(default="", max_length=500)
    scope: str = "global"
    reason: str = Field(default="", max_length=1000)

    @field_validator("scope")  # type: ignore[untyped-decorator]
    @classmethod
    def _validate_scope(cls, v: str) -> str:
        """Ensure scope is 'global' or 'project:<32-hex-uuid>'.

        Args:
            v: The scope value to validate.

        Returns:
            The validated scope string.

        Raises:
            ValueError: If the scope format is invalid.
        """
        import re  # noqa: PLC0415

        if v == "global":
            return v
        if re.fullmatch(r"project:[a-f0-9]{32}", v):
            return v
        msg = "scope must be 'global' or 'project:<32-char-hex-uuid>'"
        raise ValueError(msg)


class NotificationSchema(BaseModel):  # type: ignore[misc]
    """A user-facing notification.

    Attributes:
        id: Notification primary key.
        type: Event category (scan_complete, secrets_detected, health_changed).
        title: Short headline.
        message: Descriptive body text.
        variant: PatternFly alert variant (success, danger, warning, info).
        project_id: Optional project FK.
        scan_id: Optional scan FK.
        link: Client-side route for click-through.
        created_at: ISO 8601 creation timestamp.
        read: Whether the user has marked this as read.
    """

    id: int
    type: str
    title: str
    message: str
    variant: str
    project_id: str | None = None
    scan_id: str | None = None
    link: str = ""
    created_at: str
    read: bool
