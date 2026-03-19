"""Stub for generated common_pb2 (proto types)."""

class Violation:
    rule_id: str
    level: str
    message: str
    file: str
    path: str
    line: int
    line_range: LineRange
    def __init__(self, **kwargs: object) -> None: ...
    def HasField(self, name: str) -> bool: ...
    def CopyFrom(self, other: Violation) -> None: ...

class LineRange:
    start: int
    end: int
    def __init__(self, **kwargs: object) -> None: ...
    def CopyFrom(self, other: LineRange) -> None: ...

class File:
    path: str
    content: bytes
    def __init__(self, *, path: str = "", content: bytes = b"", **kwargs: object) -> None: ...

class HealthRequest:
    def __init__(self) -> None: ...

class HealthResponse:
    status: str
    downstream: list[ServiceHealth]
    def __init__(self, *, status: str = "", **kwargs: object) -> None: ...

class ServiceHealth:
    name: str
    status: str
    address: str
    def __init__(self, *, name: str = "", status: str = "", address: str = "", **kwargs: object) -> None: ...

class ScanSummary:
    total: int
    auto_fixable: int
    ai_candidate: int
    manual_review: int
    by_resolution: dict[str, int]
    def __init__(self, **kwargs: object) -> None: ...

class RuleTiming:
    rule_id: str
    elapsed_ms: float
    violations: int
    def __init__(
        self, *, rule_id: str = "", elapsed_ms: float = 0.0, violations: int = 0, **kwargs: object
    ) -> None: ...

class ValidatorDiagnostics:
    validator_name: str
    request_id: str
    total_ms: float
    files_received: int
    violations_found: int
    rule_timings: list[RuleTiming]
    metadata: dict[str, str]
    def __init__(self, **kwargs: object) -> None: ...
