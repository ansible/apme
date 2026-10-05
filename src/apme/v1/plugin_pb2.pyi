"""Stub for generated plugin_pb2 (proto types)."""

from collections.abc import Iterable

from apme.v1.common_pb2 import File, Violation

class TransformRequest:
    request_id: str
    file: File
    violation: Violation
    hierarchy_payload: bytes
    def __init__(self, **kwargs: object) -> None: ...

class TransformResponse:
    request_id: str
    file: File
    applied: bool
    error: str
    def __init__(self, **kwargs: object) -> None: ...

class DescribeRequest:
    def __init__(self, **kwargs: object) -> None: ...

class DescribeResponse:
    name: str
    version: str
    rule_id_prefix: str
    transform_rule_ids: list[str]
    metadata: dict[str, str]
    def __init__(
        self,
        *,
        name: str = "",
        version: str = "",
        rule_id_prefix: str = "",
        transform_rule_ids: Iterable[str] | None = ...,
        **kwargs: object,
    ) -> None: ...
