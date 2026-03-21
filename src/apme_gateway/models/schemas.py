"""Pydantic models for REST API request/response bodies."""

from __future__ import annotations

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


class ScanCreate(BaseModel):
    """Request body for POST /api/v1/scans."""

    project_path: str = Field(..., description="Filesystem path or repository URL to scan.")
    ansible_core_version: str | None = Field(None, description="Pin an ansible-core version.")
    collection_specs: list[str] = Field(default_factory=list, description="Galaxy collection specs.")


class ViolationOut(BaseModel):
    """Single violation in API responses."""

    id: str
    rule_id: str
    level: str
    message: str
    file: str
    line: int | None = None
    path: str = ""


class ScanSummaryOut(BaseModel):
    """Pre-computed violation counts."""

    total: int = 0
    auto_fixable: int = 0
    ai_candidate: int = 0
    manual_review: int = 0


class ScanOut(BaseModel):
    """Response body for GET /api/v1/scans/{scan_id}."""

    id: str
    project_path: str
    created_at: str
    status: str
    total_violations: int
    source: str = "engine"
    violations: list[ViolationOut] = Field(default_factory=list)
    diagnostics: dict[str, object] | None = None
    summary: ScanSummaryOut | None = None


class ScanListItem(BaseModel):
    """Abbreviated scan record for list views."""

    id: str
    project_path: str
    created_at: str
    status: str
    total_violations: int
    source: str = "engine"
    secrets: int = 0
    errors: int = 0
    very_high: int = 0
    high: int = 0
    medium: int = 0
    warnings: int = 0
    low: int = 0
    very_low: int = 0
    hints: int = 0
    fixed: int = 0
    fixable: int = 0


class ScanListOut(BaseModel):
    """Paginated scan list response."""

    items: list[ScanListItem]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Format
# ---------------------------------------------------------------------------


class FormatRequest(BaseModel):
    """Request body for POST /api/v1/format."""

    project_path: str


class FileDiffOut(BaseModel):
    """A single file diff from format."""

    path: str
    diff: str


class FormatOut(BaseModel):
    """Response for POST /api/v1/format."""

    diffs: list[FileDiffOut]
    total: int


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class ServiceHealthOut(BaseModel):
    """Health of one downstream service."""

    name: str
    status: str
    address: str = ""


class HealthOut(BaseModel):
    """Aggregate health response."""

    gateway: str = "ok"
    primary: ServiceHealthOut | None = None
    downstream: list[ServiceHealthOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


class RuleOut(BaseModel):
    """A single rule from the rule catalog."""

    rule_id: str
    description: str
    level: str = "warning"
    validator: str = ""
    fixable: bool = False
    scope: str = ""
    tags: list[str] = Field(default_factory=list)


class RuleListOut(BaseModel):
    """Full rule catalog response."""

    rules: list[RuleOut]
    total: int


# ---------------------------------------------------------------------------
# Remediation
# ---------------------------------------------------------------------------


class ProposalOut(BaseModel):
    """A single remediation proposal."""

    id: str
    scan_id: str
    violation_id: str | None = None
    tier: int
    status: str
    diff: str | None = None
    proposed_by: str | None = None


class ProposalListOut(BaseModel):
    """List of remediation proposals."""

    items: list[ProposalOut]
    total: int


class ProposalAction(BaseModel):
    """Accept or reject body (empty — action is in the URL path)."""


# ---------------------------------------------------------------------------
# WebSocket messages
# ---------------------------------------------------------------------------


class WSCommand(BaseModel):
    """Inbound WebSocket JSON command from the browser."""

    type: str
    ids: list[str] = Field(default_factory=list)
    session_id: str | None = None
    project_path: str | None = None
    ansible_core_version: str | None = None
    collection_specs: list[str] = Field(default_factory=list)
    enable_ai: bool = False
