"""Stub for generated validate_pb2 (proto types)."""

from collections.abc import Iterable

from apme.v1.common_pb2 import ProgressUpdate

class ValidateRequest:
    request_id: str
    project_root: str
    hierarchy_payload: bytes
    scandata: bytes
    ansible_core_version: str
    collection_specs: list[str]
    files: list[object]
    session_id: str
    venv_path: str
    content_graph_data: bytes
    dirty_node_ids: list[str]
    graph_rule_opt_in: list[str]
    def __init__(
        self,
        *,
        session_id: str = "",
        venv_path: str = "",
        content_graph_data: bytes = b"",
        dirty_node_ids: Iterable[str] | None = ...,
        graph_rule_opt_in: Iterable[str] | None = ...,
        **kwargs: object,
    ) -> None: ...
    def HasField(self, field_name: str) -> bool: ...

class ValidateResponse:
    logs: list[ProgressUpdate]
    def __init__(self, **kwargs: object) -> None: ...
