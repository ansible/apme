"""Complete validator inputs must fit the transport without losing coverage."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import grpc
import pytest
import pytest_asyncio  # type: ignore[import-not-found]

from apme.v1 import validate_pb2_grpc
from apme.v1.common_pb2 import File, ValidatorDiagnostics
from apme.v1.validate_pb2 import ValidateRequest, ValidateRequestChunk, ValidateResponse
from apme_engine.daemon import engine_server
from apme_engine.daemon.engine_server import (
    EngineServicer,
    GraphTransportError,
    _call_validator,
    _validator_request,
)


class _RecordingValidator(validate_pb2_grpc.ValidatorServicer):
    """Record complete requests accepted by a real size-limited gRPC server."""

    def __init__(self, name: str) -> None:
        """Initialize request and stream records.

        Args:
            name: Validator label used for response diagnostics.
        """
        self.name = name
        self.requests: list[ValidateRequest] = []
        self.chunks: list[ValidateRequestChunk] = []

    async def Validate(
        self,
        request: ValidateRequest,
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> ValidateResponse:
        """Accept a request and return correlated diagnostics.

        Args:
            request: Complete validation input received over gRPC.
            context: Server context, unused by this recorder.

        Returns:
            A successful, correlated validation response.
        """
        self.requests.append(request)
        return ValidateResponse(
            request_id=request.request_id,
            diagnostics=ValidatorDiagnostics(validator_name=self.name, request_id=request.request_id),
        )

    async def ValidateStream(
        self,
        request_iterator: AsyncIterator[ValidateRequestChunk],
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> ValidateResponse:
        """Reassemble the same complete request delivered by streaming.

        Args:
            request_iterator: Size-bounded protobuf envelopes.
            context: Server context forwarded to unary handling.

        Returns:
            The same response as unary handling.
        """
        async for chunk in request_iterator:
            self.chunks.append(chunk)
        request = ValidateRequest.FromString(b"".join(chunk.serialized_request_chunk for chunk in self.chunks))
        return await self.Validate(request, context)


@pytest_asyncio.fixture  # type: ignore[untyped-decorator]
async def validators(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, _RecordingValidator]]:
    """Run all validator endpoints with a compact real message-size limit.

    Args:
        monkeypatch: Isolates endpoint addresses and transport limits.

    Yields:
        dict[str, _RecordingValidator]: Recorders for each built-in validator.
    """
    monkeypatch.setattr(engine_server, "_GRPC_MAX_MSG", 1024)
    monkeypatch.setattr(engine_server, "_GRPC_STREAM_CHUNK_BYTES", 512)
    monkeypatch.setattr(engine_server, "_MAX_STREAMED_VALIDATE_REQUEST_BYTES", 16_384)
    servers: list[grpc.aio.Server] = []
    recorders: dict[str, _RecordingValidator] = {}
    try:
        for name, env_var in engine_server.VALIDATOR_ENV_VARS.items():
            recorder = _RecordingValidator(name)
            server = grpc.aio.server(options=[("grpc.max_receive_message_length", 1024)])
            validate_pb2_grpc.add_ValidatorServicer_to_server(recorder, server)  # type: ignore[no-untyped-call]
            port = server.add_insecure_port("127.0.0.1:0")
            await server.start()
            servers.append(server)
            recorders[name] = recorder
            monkeypatch.setenv(env_var, f"127.0.0.1:{port}")
        yield recorders
    finally:
        for server in servers:
            await server.stop(None)


@pytest.fixture  # type: ignore[untyped-decorator]
def pipeline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> EngineServicer:
    """Use real transport while stubbing parsing and session installation.

    Args:
        monkeypatch: Replaces only local parsing and venv preparation.
        tmp_path: Session venv path for request metadata.

    Returns:
        A pipeline ready to send real validator RPCs.
    """
    context = SimpleNamespace(
        hierarchy_payload={"metadata": {}, "collection_set": [], "value": "h" * 600},
        scandata=SimpleNamespace(content_graph=SimpleNamespace(to_dict=lambda **_kwargs: {"value": "g" * 240})),
        engine_diagnostics=SimpleNamespace(
            parse_ms=0.0, annotate_ms=0.0, total_ms=0.0, files_scanned=1, graph_nodes_built=1
        ),
    )
    manager = MagicMock()
    manager.peek_warm.return_value = SimpleNamespace(
        venv_root=tmp_path, failed_collections=[], installed_collections=[]
    )
    monkeypatch.setattr(engine_server, "run_scan", MagicMock(return_value=context))
    monkeypatch.setattr(EngineServicer, "_get_venv_manager", MagicMock(return_value=manager))
    return EngineServicer()


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("name", "fields"),
    [
        ("native", {"files", "content_graph_data"}),
        ("opa", {"hierarchy_payload"}),
        ("ansible", {"files", "hierarchy_payload", "content_graph_data"}),
        ("gitleaks", {"files", "content_graph_data"}),
        ("collection_health", set()),
        ("dep_audit", set()),
    ],
)
def test_validator_inputs_preserve_complete_consumed_fields(name: str, fields: set[str]) -> None:
    """Only unused payloads are omitted; metadata and consumed bytes survive.

    Args:
        name: Built-in backend whose request is selected.
        fields: Input fields consumed by the actual backend implementation.
    """
    request = ValidateRequest(
        request_id="scan-1",
        project_root="project",
        session_id="session-1",
        venv_path="/sessions/venv",
        ansible_core_version="2.20",
        collection_specs=["acme.demo:1.0"],
        dirty_node_ids=["task-1"],
        graph_rule_opt_in=["L104"],
        files=[File(path="extensionless", content=b"entire source\x00")],
        hierarchy_payload=b"entire hierarchy",
        content_graph_data=b"entire graph",
        scandata=b"unused legacy data",
    )
    original = request.SerializeToString()
    selected = _validator_request(name, request)
    assert selected.files == (request.files if "files" in fields else [])
    for field_name in ("hierarchy_payload", "content_graph_data", "scandata"):
        assert getattr(selected, field_name) == (getattr(request, field_name) if field_name in fields else b"")
    for field_name in (
        "request_id",
        "project_root",
        "session_id",
        "venv_path",
        "ansible_core_version",
        "collection_specs",
        "dirty_node_ids",
        "graph_rule_opt_in",
    ):
        assert getattr(selected, field_name) == getattr(request, field_name)
    assert request.SerializeToString() == original


async def test_full_scan_dispatches_complete_backend_inputs_with_real_grpc_limit(
    validators: dict[str, _RecordingValidator], pipeline: EngineServicer, tmp_path: Path
) -> None:
    """An oversized combined context succeeds via projection and Ansible streaming.

    Args:
        validators: Real, size-limited validator endpoints.
        pipeline: Pipeline with local parser and venv preparation stubbed.
        tmp_path: Scan workspace and session venv path.
    """
    files = [File(path="extensionless", content=b"f" * 300)]
    config = File(path="ansible.cfg", content=b"[defaults]\n")
    outcome = await pipeline._execute_scan_pipeline(tmp_path, files, "wire", ansible_context_files=[config])
    diagnostics = outcome[1]
    assert diagnostics is not None
    assert {entry.validator_name for entry in diagnostics.validators} == set(validators)
    requests = {name: recorder.requests[0] for name, recorder in validators.items()}
    assert all(len(recorder.requests) == 1 for recorder in validators.values())
    native, opa, ansible = (requests[name] for name in ("native", "opa", "ansible"))
    combined = ValidateRequest(
        hierarchy_payload=opa.hierarchy_payload, content_graph_data=native.content_graph_data, files=files
    )
    assert combined.ByteSize() > 1024
    assert native.files == files and not native.hierarchy_payload
    assert opa.hierarchy_payload == ansible.hierarchy_payload
    assert not opa.files and not opa.content_graph_data
    assert native.content_graph_data == ansible.content_graph_data
    assert list(ansible.files) == [*files, config]
    assert requests["gitleaks"].files == files
    assert requests["gitleaks"].content_graph_data == native.content_graph_data
    assert not requests["gitleaks"].hierarchy_payload
    for name in ("collection_health", "dep_audit"):
        assert requests[name].venv_path == str(tmp_path)
        assert (
            not requests[name].files and not requests[name].content_graph_data and not requests[name].hierarchy_payload
        )
    for name, recorder in validators.items():
        assert bool(recorder.chunks) == (name == "ansible")
        assert all(len(chunk.serialized_request_chunk) <= 512 for chunk in recorder.chunks)
        assert recorder.requests[0].request_id == "wire"
        if name != "ansible":
            assert recorder.requests[0].ByteSize() <= 1024


@pytest.mark.parametrize("rescan", [False, True])  # type: ignore[untyped-decorator]
async def test_streaming_preserves_complete_request_in_scan_and_rescan(
    validators: dict[str, _RecordingValidator], rescan: bool
) -> None:
    """Streaming keeps all file, task, and dirty-node context in either mode.

    Args:
        validators: Real size-limited validator endpoints.
        rescan: Whether to send a dirty-node hint for incremental validation.
    """
    import os

    request = ValidateRequest(
        request_id="scan-rescan" if rescan else "scan",
        files=[File(path="library/helper.py", content=b"p" * 700)],
        hierarchy_payload=b"h" * 700,
        dirty_node_ids=["task-1"] if rescan else [],
        session_id="session",
        venv_path="/sessions/venv",
    )
    result = await _call_validator(os.environ["ANSIBLE_GRPC_ADDRESS"], request, stream_large_request=True)
    assert not result.error
    assert validators["ansible"].requests == [request]
    assert validators["ansible"].chunks


@pytest.mark.parametrize("stream", [False, True])  # type: ignore[untyped-decorator]
async def test_complete_request_overflow_is_rejected_before_rpc(monkeypatch: pytest.MonkeyPatch, stream: bool) -> None:
    """Files and protobuf metadata count toward the complete request allowance.

    Args:
        monkeypatch: Reduces independent unary and streaming request limits.
        stream: Whether the caller supports the streaming transport.
    """
    request = ValidateRequest(
        request_id="metadata-also-counts",
        hierarchy_payload=b"h" * 40,
        content_graph_data=b"g" * 15,
        files=[File(path="source", content=b"f" * 15)],
    )
    monkeypatch.setattr(engine_server, "_GRPC_MAX_MSG", request.ByteSize() - 1)
    monkeypatch.setattr(engine_server, "_MAX_STREAMED_VALIDATE_REQUEST_BYTES", request.ByteSize() - 1)
    with (
        patch("apme_engine.daemon.engine_server.grpc.aio.insecure_channel") as channel,
        pytest.raises(GraphTransportError, match="request limit.*validation is incomplete"),
    ):
        await _call_validator("unused", request, stream_large_request=stream)
    channel.assert_not_called()


async def test_exact_unary_request_limit_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The complete serialized protobuf may exactly fill the unary allowance.

    Args:
        monkeypatch: Sets the unary allowance to the exact protobuf size.
    """
    request = ValidateRequest(request_id="exact", files=[File(path="source", content=b"f" * 40)])
    monkeypatch.setattr(engine_server, "_GRPC_MAX_MSG", request.ByteSize())
    channel = MagicMock()
    channel.close = AsyncMock()
    stub = MagicMock()
    stub.Validate = AsyncMock(return_value=ValidateResponse())
    with (
        patch("apme_engine.daemon.engine_server.grpc.aio.insecure_channel", return_value=channel),
        patch("apme_engine.daemon.engine_server.validate_pb2_grpc.ValidatorStub", return_value=stub),
    ):
        result = await _call_validator("unused", request)
    assert not result.error
    stub.Validate.assert_awaited_once_with(request, timeout=300)
    channel.close.assert_awaited_once()


@pytest.mark.parametrize("backend", ["native", "gitleaks"])  # type: ignore[untyped-decorator]
async def test_untransportable_backend_input_fails_scan_including_optional_validators(
    pipeline: EngineServicer, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, backend: str
) -> None:
    """Neither required nor optional oversized validation can become a clean scan.

    Args:
        pipeline: Pipeline with local parser and venv preparation stubbed.
        monkeypatch: Isolates configured validator endpoints and injects overflow.
        tmp_path: Scan workspace.
        backend: Required or optional backend reporting unsupported transport.
    """
    for name, env_var in engine_server.VALIDATOR_ENV_VARS.items():
        monkeypatch.setenv(env_var, name)

    async def dispatch(address: str, request: ValidateRequest, **kwargs: object) -> engine_server._ValidatorResult:
        """Represent a pre-dispatch transport error from one backend.

        Args:
            address: Built-in validator name used as a synthetic address.
            request: Complete validation request.
            **kwargs: Transport selection options.

        Returns:
            Success from unaffected backends.

        Raises:
            GraphTransportError: The chosen validator cannot accept its input.
        """
        if address == backend:
            raise GraphTransportError("Complete request exceeds transport; validation is incomplete")
        return engine_server._ValidatorResult()

    monkeypatch.setattr(engine_server, "_call_validator", dispatch)
    with pytest.raises(GraphTransportError, match="validation is incomplete"):
        await pipeline._execute_scan_pipeline(tmp_path, [File(path="main.yml", content=b"---")], "overflow")
