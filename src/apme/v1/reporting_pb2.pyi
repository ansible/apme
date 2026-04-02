"""Stub for generated reporting_pb2 (proto types)."""

from collections.abc import Iterable

from apme.v1.common_pb2 import (
    ProgressUpdate,
    ProjectManifest,
    ScanSummary,
    Violation,
)
from apme.v1.primary_pb2 import (
    FilePatch,
    FixReport,
    ScanDiagnostics,
)

class FixCompletedEvent:
    scan_id: str
    session_id: str
    project_path: str
    source: str
    remaining_violations: list[Violation]
    diagnostics: ScanDiagnostics
    summary: ScanSummary
    report: FixReport
    proposals: list[ProposalOutcome]
    logs: list[ProgressUpdate]
    fixed_violations: list[Violation]
    patches: list[FilePatch]
    manifest: ProjectManifest
    content_graph_json: str
    def __init__(
        self,
        *,
        scan_id: str = ...,
        session_id: str = ...,
        project_path: str = ...,
        source: str = ...,
        remaining_violations: Iterable[Violation] | None = ...,
        diagnostics: ScanDiagnostics | None = ...,
        summary: ScanSummary | None = ...,
        report: FixReport | None = ...,
        proposals: Iterable[ProposalOutcome] | None = ...,
        logs: Iterable[ProgressUpdate] | None = ...,
        fixed_violations: Iterable[Violation] | None = ...,
        patches: Iterable[FilePatch] | None = ...,
        manifest: ProjectManifest | None = ...,
        content_graph_json: str = ...,
    ) -> None: ...

class ProposalOutcome:
    proposal_id: str
    rule_id: str
    file: str
    tier: int
    confidence: float
    status: str
    def __init__(
        self,
        *,
        proposal_id: str = ...,
        rule_id: str = ...,
        file: str = ...,
        tier: int = ...,
        confidence: float = ...,
        status: str = ...,
    ) -> None: ...

class ReportAck:
    def __init__(self) -> None: ...

class RuleDefinition:
    rule_id: str
    default_severity: int
    category: str
    source: str
    description: str
    scope: int
    enabled: bool
    def __init__(
        self,
        *,
        rule_id: str = ...,
        default_severity: int = ...,
        category: str = ...,
        source: str = ...,
        description: str = ...,
        scope: int = ...,
        enabled: bool = ...,
    ) -> None: ...

class RegisterRulesRequest:
    pod_id: str
    is_authority: bool
    rules: list[RuleDefinition]
    def __init__(
        self,
        *,
        pod_id: str = ...,
        is_authority: bool = ...,
        rules: Iterable[RuleDefinition] | None = ...,
    ) -> None: ...

class RegisterRulesResponse:
    accepted: bool
    message: str
    rules_added: int
    rules_removed: int
    rules_unchanged: int
    def __init__(
        self,
        *,
        accepted: bool = ...,
        message: str = ...,
        rules_added: int = ...,
        rules_removed: int = ...,
        rules_unchanged: int = ...,
    ) -> None: ...
