"""Pydantic response models for the REST API."""

from __future__ import annotations

from pydantic import BaseModel, Field


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
        validator_source: Validator that produced this (native, opa, ansible, gitleaks).
        snippet: Source lines around the violation with line numbers.
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
    validator_source: str = ""
    snippet: str = ""


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
        diagnostics_json: Raw diagnostics JSON string.
        violations: List of violation rows.
        proposals: List of proposal rows.
        logs: List of log entries.
        patches: Per-file diffs.
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
    items: list[SessionSummary] | list[ActivitySummary] | list[TopViolation] | list[ProjectSummary]


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


# ── Project schemas (ADR-037) ────────────────────────────────────────


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
    """

    name: str
    repo_url: str
    branch: str = "main"


class UpdateProjectRequest(BaseModel):  # type: ignore[misc]
    """Partial update for project fields.

    Attributes:
        name: New display label.
        repo_url: New clone URL.
        branch: New branch.
    """

    name: str | None = None
    repo_url: str | None = None
    branch: str | None = None


# ── Dependency manifest schemas (ADR-040) ────────────────────────────


class CollectionRefSchema(BaseModel):  # type: ignore[misc]
    """A collection discovered in a project's session venv.

    Attributes:
        fqcn: Fully-qualified collection name.
        version: Installed version string.
        source: Origin — galaxy, local, or git.
    """

    fqcn: str
    version: str
    source: str


class PythonPackageRefSchema(BaseModel):  # type: ignore[misc]
    """A Python package discovered in a project's session venv.

    Attributes:
        name: PyPI package name.
        version: Installed version string.
    """

    name: str
    version: str


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
    """

    id: str
    name: str
    health_score: int
    collection_version: str


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
    """

    id: str
    name: str
    health_score: int = 0
    package_version: str = ""


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
