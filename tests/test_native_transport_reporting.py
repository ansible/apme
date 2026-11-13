"""Regression coverage for Native transport and applied-file reporting."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import grpc
import grpc.aio
import pytest

from apme.v1 import validate_pb2_grpc
from apme.v1.common_pb2 import File
from apme.v1.engine_pb2 import FixReport
from apme.v1.validate_pb2 import ValidateRequest
from apme_engine.daemon import engine_server, native_validator_server
from apme_engine.daemon.engine_server import (
    GraphTransportError,
    RequiredValidatorDependencyError,
    _bounded_transport_json,
    _call_validator,
    _refresh_session_file_violations,
    _sync_session_file_report,
)
from apme_engine.daemon.session import SessionState
from apme_engine.engine.models import ViolationDict


def test_transport_rejects_colliding_normalized_keys() -> None:
    """Numeric and string YAML keys must not silently overwrite one another."""
    with pytest.raises(GraphTransportError, match="colliding mapping keys"):
        _bounded_transport_json({1: "numeric", "1": "string"})


async def test_native_stream_and_infrastructure_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real Native endpoint accepts chunks and reports assessment failures to Engine.

    Args:
        monkeypatch: Reduce transport limits for a compact streamed request.
    """
    monkeypatch.setattr(engine_server, "_GRPC_MAX_MSG", 1024)
    monkeypatch.setattr(engine_server, "_GRPC_STREAM_CHUNK_BYTES", 512)
    server = grpc.aio.server(options=[("grpc.max_receive_message_length", 1024)])
    validate_pb2_grpc.add_ValidatorServicer_to_server(  # type: ignore[no-untyped-call]
        native_validator_server.NativeValidatorServicer(), server
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    request = ValidateRequest(request_id="large", files=[File(path="source", content=b"x" * 2048)])
    try:
        with patch.object(
            native_validator_server, "_run_native", return_value=native_validator_server._GraphRunResult()
        ) as run:
            result = await _call_validator(f"127.0.0.1:{port}", request, stream_large_request=True, fail_on_infra=True)
        assert not result.error
        assert run.call_args.args[0] == request

        with patch.object(native_validator_server, "_run_native", side_effect=RuntimeError("private detail")):
            result = await _call_validator(f"127.0.0.1:{port}", request, stream_large_request=True, fail_on_infra=True)
            optional_result = await _call_validator(f"127.0.0.1:{port}", request, stream_large_request=True)
        assert result.error
        assert "private detail" not in result.error
        assert not result.violations
        assert not optional_result.error
        assert [v["rule_id"] for v in optional_result.violations] == ["R902"]
    finally:
        await server.stop(None)


async def test_file_refresh_replaces_report_from_applied_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """A later file scan removes stale findings and adds newly introduced ones.

    Args:
        monkeypatch: Configure a synthetic Native endpoint.
    """
    monkeypatch.setenv("NATIVE_GRPC_ADDRESS", "native")
    old: ViolationDict = {
        "rule_id": "L115",
        "file": "molecule/default/molecule.yml",
        "line": 1,
        "path": "$.old",
        "message": "old",
    }
    new: ViolationDict = {
        "rule_id": "L116",
        "file": "molecule/default/molecule.yml",
        "line": 1,
        "path": "$.new",
        "message": "new",
        "scope": "collection",
        "remediation_class": "manual-review",
    }
    session = SessionState(session_id="s", working_files={"molecule/default/molecule.yml": b"new: value\n"})
    session.file_violations = [old]
    session.remaining_manual = [old]
    session.report = FixReport(remaining_manual=1)
    dispatch = AsyncMock(return_value=engine_server._ValidatorResult(violations=[new]))
    with patch.object(engine_server, "_call_validator", dispatch):
        await _refresh_session_file_violations(session)
    awaited = dispatch.await_args
    assert awaited is not None
    request = awaited.args[1]
    assert request.files[0].content == b"new: value\n"
    assert awaited.kwargs["stream_large_request"] is True
    _sync_session_file_report(session)
    assert [v["message"] for v in session.file_violations] == ["new"]
    assert [v.message for v in session.report.remaining_violations] == ["new"]
    assert session.report.remaining_manual == 1


async def test_file_refresh_failure_preserves_previous_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed reassessment cannot turn stale findings into a clean report.

    Args:
        monkeypatch: Configure a synthetic Native endpoint.
    """
    monkeypatch.setenv("NATIVE_GRPC_ADDRESS", "native")
    old: ViolationDict = {"rule_id": "L115", "path": "$.old"}
    session = SessionState(session_id="s", working_files={"molecule/default/molecule.yml": b"---"})
    session.file_violations = [old]
    with (
        patch.object(
            engine_server, "_call_validator", AsyncMock(return_value=engine_server._ValidatorResult(error="failed"))
        ),
        pytest.raises(RequiredValidatorDependencyError, match="file rescan failed"),
    ):
        await _refresh_session_file_violations(session)
    assert session.file_violations == [old]
