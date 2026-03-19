"""Pydantic schemas for API request/response bodies."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


# --- Scans ---


class ScanCreate(BaseModel):
    """Request body for POST /api/v1/scans."""

    project_name: str
    files: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of relative path to file content (text).",
    )
    ansible_core_version: str = ""
    collection_specs: list[str] = Field(default_factory=list)


class ViolationIngest(BaseModel):
    """A single violation in an ingest payload."""

    rule_id: str
    level: str
    message: str
    file: str
    line: int | None = None
    line_end: int | None = None
    path: str | None = None


class ScanIngest(BaseModel):
    """Request body for POST /api/v1/scans/ingest — accepts pre-computed CLI results."""

    project_name: str
    scan_type: str = "scan"
    status: str = "completed"
    violations: list[ViolationIngest] = Field(default_factory=list)
    fixed_count: int = 0
    diagnostics: dict[str, Any] | None = None
    request_id: str | None = Field(
        default=None,
        description="Idempotency key to prevent duplicate ingestion.",
    )


class DiagnosticsOut(BaseModel):
    engine_parse_ms: float = 0.0
    engine_annotate_ms: float = 0.0
    engine_total_ms: float = 0.0
    files_scanned: int = 0
    trees_built: int = 0
    total_violations: int = 0
    validators: list[ValidatorDiagnosticsOut] = Field(default_factory=list)
    fan_out_ms: float = 0.0
    total_ms: float = 0.0


class ValidatorDiagnosticsOut(BaseModel):
    validator_name: str = ""
    request_id: str = ""
    total_ms: float = 0.0
    files_received: int = 0
    violations_found: int = 0
    rule_timings: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)


class ViolationOut(BaseModel):
    id: str
    rule_id: str
    level: str
    message: str
    file: str
    line: int | None = None
    line_end: int | None = None
    path: str | None = None


class ScanSummary(BaseModel):
    """Returned in scan list responses."""

    id: str
    project_name: str
    created_at: datetime
    status: str
    scan_type: str = "scan"
    total_violations: int
    error_count: int = 0
    warning_count: int = 0
    hint_count: int = 0
    fixed_count: int = 0


class ScanDetail(BaseModel):
    """Returned for single-scan detail responses."""

    id: str
    project_name: str
    created_at: datetime
    status: str
    scan_type: str = "scan"
    total_violations: int
    error_count: int = 0
    warning_count: int = 0
    hint_count: int = 0
    fixed_count: int = 0
    violations: list[ViolationOut] = Field(default_factory=list)
    diagnostics: DiagnosticsOut | None = None
    options: dict[str, Any] | None = None


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


# --- Rules ---


class RuleOut(BaseModel):
    rule_id: str
    validator: str
    description: str
    has_fixer: bool = False


class RuleDetail(RuleOut):
    examples: dict[str, str] = Field(default_factory=dict, description="violation/pass example YAML snippets")


# --- Format ---


class FormatRequest(BaseModel):
    files: dict[str, str] = Field(description="Mapping of relative path to file content (text).")


class FormatDiffOut(BaseModel):
    path: str
    diff: str


# --- Fix ---


class FixCreate(BaseModel):
    project_name: str
    files: dict[str, str] = Field(default_factory=dict)
    ansible_core_version: str = ""
    collection_specs: list[str] = Field(default_factory=list)
    apply: bool = False
    ai: bool = False
    max_passes: int = 3


class FixJobStatus(BaseModel):
    job_id: str
    status: str
    progress: list[dict[str, Any]] = Field(default_factory=list)
    result: dict[str, Any] | None = None


# --- Remediation ---


class RemediationOut(BaseModel):
    id: str
    scan_id: str
    violation_id: str
    tier: int
    status: str
    diff: str | None = None
    proposed_by: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None


class RemediationAction(BaseModel):
    reviewed_by: str = ""


# --- Health ---


class ServiceHealth(BaseModel):
    name: str
    status: str
    latency_ms: float | None = None


class HealthOut(BaseModel):
    status: str
    services: list[ServiceHealth] = Field(default_factory=list)
