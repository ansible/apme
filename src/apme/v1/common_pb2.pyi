"""Stub for generated common_pb2 (proto types)."""

from collections.abc import Iterable

class Violation:
    rule_id: str
    level: str
    message: str
    file: str
    path: str
    line: int
    line_range: LineRange
    remediation_class: int
    remediation_resolution: int
    metadata: dict[str, str]
    scope: int
    source: str
    snippet: str
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

class GalaxyServerDef:
    name: str
    url: str
    token: str
    auth_url: str
    def __init__(
        self,
        *,
        name: str = "",
        url: str = "",
        token: str = "",
        auth_url: str = "",
        **kwargs: object,
    ) -> None: ...
    def SerializeToString(self) -> bytes: ...
    def ParseFromString(self, data: bytes) -> int: ...

# Log level enum constants (ADR-033)
LOG_LEVEL_UNSPECIFIED: int
DEBUG: int
INFO: int
WARNING: int
ERROR: int

class ProgressUpdate:
    message: str
    phase: str
    progress: float
    level: int
    def __init__(self, **kwargs: object) -> None: ...

class CollectionRef:
    fqcn: str
    version: str
    source: str
    def __init__(self, *, fqcn: str = "", version: str = "", source: str = "", **kwargs: object) -> None: ...

class PythonPackageRef:
    name: str
    version: str
    def __init__(self, *, name: str = "", version: str = "", **kwargs: object) -> None: ...

class ProjectManifest:
    ansible_core_version: str
    collections: list[CollectionRef]
    python_packages: list[PythonPackageRef]
    requirements_files: list[str]
    dependency_tree: str
    def __init__(
        self,
        *,
        ansible_core_version: str = "",
        collections: Iterable[CollectionRef] | None = ...,
        python_packages: Iterable[PythonPackageRef] | None = ...,
        requirements_files: Iterable[str] | None = ...,
        dependency_tree: str = "",
        **kwargs: object,
    ) -> None: ...
    def ByteSize(self) -> int: ...
