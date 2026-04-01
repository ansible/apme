"""Stub for generated primary_pb2 (proto types)."""

from collections.abc import Iterable

from google.protobuf.struct_pb2 import Struct

from apme.v1.common_pb2 import GalaxyServerDef, ProgressUpdate, ValidatorDiagnostics, Violation

class ScanOptions:
    include_scandata: bool
    ansible_core_version: str
    collection_specs: list[str]
    session_id: str
    galaxy_servers: list[GalaxyServerDef]
    def __init__(
        self, *, session_id: str = "", galaxy_servers: Iterable[GalaxyServerDef] | None = ..., **kwargs: object
    ) -> None: ...

class FixOptions:
    max_passes: int
    ansible_core_version: str
    collection_specs: list[str]
    exclude_patterns: list[str]
    enable_ai: bool
    enable_agentic: bool
    ai_model: str
    session_id: str
    galaxy_servers: list[GalaxyServerDef]
    def __init__(
        self, *, session_id: str = "", galaxy_servers: Iterable[GalaxyServerDef] | None = ..., **kwargs: object
    ) -> None: ...

class ScanChunk:
    scan_id: str
    project_root: str
    options: ScanOptions | None
    files: list[object]
    last: bool
    fix_options: FixOptions | None
    def __init__(self, **kwargs: object) -> None: ...
    def HasField(self, field_name: str) -> bool: ...

class ScanDiagnostics:
    engine_parse_ms: float
    engine_annotate_ms: float
    engine_total_ms: float
    files_scanned: int
    graph_nodes_built: int
    total_violations: int
    validators: list[ValidatorDiagnostics]
    fan_out_ms: float
    total_ms: float
    def __init__(self, **kwargs: object) -> None: ...

class FormatRequest:
    files: list[object]
    def __init__(self, **kwargs: object) -> None: ...

class FormatResponse:
    logs: list[ProgressUpdate]
    def __init__(self, **kwargs: object) -> None: ...

class FileDiff:
    path: str
    original: bytes
    formatted: bytes
    diff: str
    def __init__(self, **kwargs: object) -> None: ...

class FilePatch:
    path: str
    original: bytes
    patched: bytes
    diff: str
    applied_rules: list[str]
    def __init__(self, **kwargs: object) -> None: ...

class FixReport:
    passes: int
    fixed: int
    remaining_ai: int
    remaining_manual: int
    oscillation_detected: bool
    remaining_violations: list[Violation]
    fixed_violations: list[Violation]
    def __init__(self, **kwargs: object) -> None: ...

# ---------------------------------------------------------------------------
# FixSession: bidirectional streaming (ADR-028)
# ---------------------------------------------------------------------------

class SessionCommand:
    upload: ScanChunk
    approve: ApprovalRequest
    extend: ExtendRequest
    close: CloseRequest
    resume: ResumeRequest
    def __init__(self, **kwargs: object) -> None: ...
    def HasField(self, field_name: str) -> bool: ...
    def WhichOneof(self, oneof_group: str) -> str | None: ...

class ApprovalRequest:
    approved_ids: list[str]
    def __init__(self, **kwargs: object) -> None: ...

class ExtendRequest:
    def __init__(self, **kwargs: object) -> None: ...

class CloseRequest:
    def __init__(self, **kwargs: object) -> None: ...

class ResumeRequest:
    session_id: str
    def __init__(self, **kwargs: object) -> None: ...

class SessionEvent:
    created: SessionCreated
    progress: ProgressUpdate
    tier1_complete: Tier1Summary
    proposals: ProposalsReady
    approval_ack: ApprovalAck
    result: SessionResult
    expiring: ExpirationWarning
    closed: SessionClosed
    data: DataPayload
    def __init__(self, **kwargs: object) -> None: ...
    def HasField(self, field_name: str) -> bool: ...
    def WhichOneof(self, oneof_group: str) -> str | None: ...

class SessionCreated:
    session_id: str
    ttl_seconds: int
    def __init__(self, **kwargs: object) -> None: ...

# ProgressUpdate is defined in common_pb2 (ADR-033) and re-exported here
# for backward compatibility. Import from common_pb2 for new code.

class Tier1Summary:
    applied_patches: list[FilePatch]
    format_diffs: list[FileDiff]
    idempotency_ok: bool
    report: FixReport | None
    def __init__(self, **kwargs: object) -> None: ...

class Proposal:
    id: str
    file: str
    rule_id: str
    line_start: int
    line_end: int
    before_text: str
    after_text: str
    diff_hunk: str
    confidence: float
    explanation: str
    tier: int
    def __init__(self, **kwargs: object) -> None: ...

class ProposalsReady:
    proposals: list[Proposal]
    tier: int
    status: int
    def __init__(self, **kwargs: object) -> None: ...

class ApprovalAck:
    applied_count: int
    status: int
    ttl_seconds: int
    def __init__(self, **kwargs: object) -> None: ...

class SessionResult:
    patches: list[FilePatch]
    report: FixReport | None
    remaining_violations: list[Violation]
    fixed_violations: list[Violation]
    def __init__(self, **kwargs: object) -> None: ...

class ExpirationWarning:
    ttl_seconds: int
    def __init__(self, **kwargs: object) -> None: ...

class SessionClosed:
    def __init__(self, **kwargs: object) -> None: ...

class DataPayload:
    kind: str
    data: Struct
    def __init__(self, **kwargs: object) -> None: ...

# LogLevel enum constants moved to common_pb2 (ADR-033).
# Re-exported here for backward compatibility.

class ListAIModelsRequest:
    def __init__(self, **kwargs: object) -> None: ...

class AIModelInfo:
    id: str
    provider: str
    name: str
    def __init__(self, **kwargs: object) -> None: ...

class ListAIModelsResponse:
    models: list[AIModelInfo]
    def __init__(self, **kwargs: object) -> None: ...

SESSION_STATUS_UNSPECIFIED: int
AWAITING_APPROVAL: int
PROCESSING: int
COMPLETE: int
