"""Stub for generated validate_pb2 (proto types)."""

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
    def __init__(
        self,
        *,
        session_id: str = "",
        venv_path: str = "",
        **kwargs: object,
    ) -> None: ...
    def HasField(self, field_name: str) -> bool: ...

class ValidateResponse:
    def __init__(self, **kwargs: object) -> None: ...
