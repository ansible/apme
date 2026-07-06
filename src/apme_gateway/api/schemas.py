"""Pydantic response models for the REST API."""

from __future__ import annotations

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
    """

    scan_id: str
    session_id: str
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
        ai_reason: Why the AI could not fix this violation (ai_abstained only).
        ai_suggestion: Manual remediation guidance from the AI (ai_abstained only).
        suppressed: True if this violation matches an active suppression (ADR-055).
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
    co_fixes: list[str] = []
    node_line_start: int = 0
    ai_reason: str = ""
    ai_suggestion: str = ""
    suppressed: bool = False


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
    """

    name: str
    status: str
    address: str


class HealthStatus(BaseModel):  # type: ignore[misc]
    """Gateway health response.

    Attributes:
        status: Overall health (ok or degraded).
        database: Database connectivity status.
        components: Health status of each upstream service.
    """

    status: str
    database: str
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
        branch: Branch to clone (default main).
        scm_token: Per-project SCM token for PR creation (ADR-050).
        scm_provider: Explicit SCM provider type (ADR-050). Auto-detected if omitted.
    """

    name: str
    repo_url: str
    branch: str = "main"
    scm_token: str | None = None
    scm_provider: str | None = None


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
    branch: str | None = None
    scm_token: str | None = None
    scm_provider: str | None = None


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
    """

    activity_id: str | None = None
    branch_name: str | None = None
    create_pr: bool = True
    title: str | None = None
    body: str | None = None
    scm_token: str | None = None


class SubmitResponse(BaseModel):  # type: ignore[misc]
    """Response after a successful SCM submit (ADR-050).

    Attributes:
        branch_name: Name of the head branch that was created.
        commit_sha: SHA of the commit pushed to the branch.
        pr_url: Web URL of the PR, or ``None`` if ``create_pr`` was false.
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


class CreateGalaxyServerRequest(BaseModel):  # type: ignore[misc]
    """Request body for creating a Galaxy server.

    Attributes:
        name: Short label.
        url: Base API URL.
        token: API token (optional, empty for public Galaxy).
        auth_url: SSO/Keycloak token endpoint (optional).
    """

    name: str
    url: str
    token: str = ""
    auth_url: str = ""


class UpdateGalaxyServerRequest(BaseModel):  # type: ignore[misc]
    """Partial update for Galaxy server fields.

    Attributes:
        name: New display label.
        url: New base API URL.
        token: New API token (omit or None to leave unchanged).
        auth_url: New SSO endpoint.
    """

    name: str | None = None
    url: str | None = None
    token: str | None = None
    auth_url: str | None = None


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
