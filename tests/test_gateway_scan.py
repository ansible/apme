"""Unit tests for the gateway session WebSocket endpoint (WS /api/v1/ws/session).

Tests the session_client bridge logic by mocking the WebSocket and gRPC layers.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apme_gateway.db import close_db, init_db


@pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
async def _db(tmp_path: Path) -> AsyncIterator[None]:
    """Initialise a fresh DB per test.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Yields:
        None: Test runs between setup and teardown.
    """
    await init_db(str(tmp_path / "test.db"))
    yield
    await close_db()


# ── Mock helpers ────────────────────────────────────────────────────


def _make_session_event(oneof: str, **kwargs: object) -> MagicMock:
    """Build a mock SessionEvent.

    Args:
        oneof: Name of the active oneof field.
        **kwargs: Fields to set on the nested message.

    Returns:
        Mock SessionEvent with the specified oneof active.
    """
    event = MagicMock()
    event.WhichOneof.return_value = oneof
    nested = getattr(event, oneof)
    for k, v in kwargs.items():
        setattr(nested, k, v)
    return event


def _make_created_event(session_id: str = "sess-1", ttl_seconds: int = 1800) -> MagicMock:
    """Build a mock ``created`` SessionEvent.

    Args:
        session_id: Session identifier to set.
        ttl_seconds: TTL value to set.

    Returns:
        Mock SessionEvent with created payload.
    """
    return _make_session_event("created", session_id=session_id, ttl_seconds=ttl_seconds)


def _make_progress_event(phase: str = "engine", message: str = "test", level: int = 2) -> MagicMock:
    """Build a mock ``progress`` SessionEvent.

    Args:
        phase: Progress phase name.
        message: Progress message text.
        level: Log level.

    Returns:
        Mock SessionEvent with progress payload.
    """
    return _make_session_event("progress", phase=phase, message=message, level=level)


def _make_tier1_event() -> MagicMock:
    """Build a mock ``tier1_complete`` SessionEvent.

    Returns:
        Mock SessionEvent with empty tier1 payload.
    """
    event = MagicMock()
    event.WhichOneof.return_value = "tier1_complete"
    event.tier1_complete.idempotency_ok = True
    event.tier1_complete.applied_patches = []
    event.tier1_complete.format_diffs = []
    event.tier1_complete.HasField.return_value = False
    return event


def _make_proposals_event(
    proposals: list[dict[str, object]] | None = None,
) -> MagicMock:
    """Build a mock ``proposals`` SessionEvent.

    Args:
        proposals: List of proposal field dicts.

    Returns:
        Mock SessionEvent with proposals payload.
    """
    event = MagicMock()
    event.WhichOneof.return_value = "proposals"
    event.proposals.tier = 2
    event.proposals.status = 1  # AWAITING_APPROVAL

    mock_proposals = []
    for p in proposals or []:
        mp = MagicMock()
        for k, v in p.items():
            setattr(mp, k, v)
        mock_proposals.append(mp)
    event.proposals.proposals = mock_proposals
    return event


def _make_result_event() -> MagicMock:
    """Build a mock ``result`` SessionEvent.

    Returns:
        Mock SessionEvent with empty result payload.
    """
    event = MagicMock()
    event.WhichOneof.return_value = "result"
    event.result.patches = []
    event.result.HasField.return_value = False
    event.result.remaining_violations = []
    return event


def _make_closed_event() -> MagicMock:
    """Build a mock ``closed`` SessionEvent.

    Returns:
        Mock SessionEvent with closed payload.
    """
    return _make_session_event("closed")


async def _mock_fix_stream(
    *events: MagicMock,
) -> AsyncIterator[MagicMock]:
    """Yield mock SessionEvents as an async iterator.

    Args:
        *events: Mock SessionEvent objects to yield.

    Yields:
        MagicMock: Each mock event in sequence.
    """
    for e in events:
        yield e
        await asyncio.sleep(0)


class MockWebSocket:
    """Minimal WebSocket mock that records sent messages."""

    def __init__(self, messages: list[dict[str, object]]) -> None:
        """Initialise with pre-loaded incoming messages.

        Args:
            messages: Messages to serve from ``receive_json``.
        """
        self._incoming = list(reversed(messages))
        self.sent: list[dict[str, object]] = []

    async def receive_json(self) -> dict[str, object]:
        """Pop and return the next incoming message.

        Returns:
            Next message dict from the pre-loaded queue.

        Raises:
            Exception: When no messages remain.
        """
        if not self._incoming:
            raise Exception("No more messages")  # noqa: TRY002
        return self._incoming.pop()

    async def send_json(self, data: dict[str, object]) -> None:
        """Record a message sent to the client.

        Args:
            data: JSON-serializable payload.
        """
        self.sent.append(data)


# ── Tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_session_streams_progress_and_result() -> None:
    """WS session receives progress and result events from FixSession."""
    created = _make_created_event("sess-ws", 1800)
    progress = _make_progress_event("engine", "Scan: start", 2)
    result = _make_result_event()
    closed = _make_closed_event()
    mock_stream = _mock_fix_stream(created, progress, result, closed)

    file_content = base64.b64encode(b"---\n- hosts: all\n").decode()
    ws = MockWebSocket(
        [
            {"type": "start", "options": {"enable_ai": False}},
            {"type": "file", "path": "playbook.yml", "content": file_content},
            {"type": "files_done"},
        ]
    )

    with (
        patch("apme_gateway.session_client.grpc.aio.insecure_channel") as mock_ch_fn,
        patch("apme_gateway.session_client.engine_pb2_grpc.EngineStub") as mock_stub_cls,
    ):
        mock_ch = AsyncMock()
        mock_ch_fn.return_value = mock_ch
        mock_stub = MagicMock()
        mock_stub.FixSession.return_value = mock_stream
        mock_stub_cls.return_value = mock_stub

        from apme_gateway.session_client import handle_session

        await handle_session(ws, "localhost:50051")

    types = [m["type"] for m in ws.sent]
    assert "session_created" in types
    assert "progress" in types
    assert "result" in types


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_session_grpc_error_yields_error_event() -> None:
    """When gRPC FixSession raises, the WS receives an error event."""
    import grpc.aio

    file_content = base64.b64encode(b"---\n").decode()
    ws = MockWebSocket(
        [
            {"type": "start", "options": {}},
            {"type": "file", "path": "test.yml", "content": file_content},
            {"type": "files_done"},
        ]
    )

    rpc_error = grpc.aio.AioRpcError(
        code=grpc.StatusCode.UNAVAILABLE,
        initial_metadata=grpc.aio.Metadata(),
        trailing_metadata=grpc.aio.Metadata(),
        details="Connection refused",
        debug_error_string=None,
    )

    async def _raise_stream(*_a: object, **_kw: object) -> AsyncIterator[MagicMock]:
        raise rpc_error
        yield  # unreachable but needed for async generator

    with (
        patch("apme_gateway.session_client.grpc.aio.insecure_channel") as mock_ch_fn,
        patch("apme_gateway.session_client.engine_pb2_grpc.EngineStub") as mock_stub_cls,
    ):
        mock_ch = AsyncMock()
        mock_ch_fn.return_value = mock_ch
        mock_stub = MagicMock()
        mock_stub.FixSession.return_value = _raise_stream()
        mock_stub_cls.return_value = mock_stub

        from apme_gateway.session_client import handle_session

        await handle_session(ws, "localhost:50051")

    error_msgs = [m for m in ws.sent if m["type"] == "error"]
    assert len(error_msgs) >= 1
    assert "Connection refused" in str(error_msgs[0]["message"])


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_session_no_files_yields_error() -> None:
    """Sending files_done with no files produces an error."""
    ws = MockWebSocket(
        [
            {"type": "start", "options": {}},
            {"type": "files_done"},
        ]
    )

    from apme_gateway.session_client import handle_session

    await handle_session(ws, "localhost:50051")

    error_msgs = [m for m in ws.sent if m["type"] == "error"]
    assert len(error_msgs) >= 1
    assert "No files" in str(error_msgs[0]["message"])


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_session_temp_dir_cleaned_up() -> None:
    """Temp directory is removed after session completes."""
    result = _make_result_event()
    closed = _make_closed_event()
    mock_stream = _mock_fix_stream(result, closed)

    file_content = base64.b64encode(b"---\n").decode()
    ws = MockWebSocket(
        [
            {"type": "start", "options": {}},
            {"type": "file", "path": "t.yml", "content": file_content},
            {"type": "files_done"},
        ]
    )

    import tempfile as _tempfile

    created_dirs: list[Path] = []
    original_mkdtemp = _tempfile.mkdtemp

    def tracking_mkdtemp(**kwargs: str) -> str:
        """Track temp dirs created during the test.

        Args:
            **kwargs: Forwarded to ``tempfile.mkdtemp``.

        Returns:
            Path string of the created directory.
        """
        d: str = original_mkdtemp(**kwargs)
        created_dirs.append(Path(d))
        return d

    with (
        patch("apme_gateway.session_client.grpc.aio.insecure_channel") as mock_ch_fn,
        patch("apme_gateway.session_client.engine_pb2_grpc.EngineStub") as mock_stub_cls,
        patch("apme_gateway.session_client.tempfile.mkdtemp", side_effect=tracking_mkdtemp),
    ):
        mock_ch = AsyncMock()
        mock_ch_fn.return_value = mock_ch
        mock_stub = MagicMock()
        mock_stub.FixSession.return_value = mock_stream
        mock_stub_cls.return_value = mock_stub

        from apme_gateway.session_client import handle_session

        await handle_session(ws, "localhost:50051")

    assert len(created_dirs) == 1
    assert not created_dirs[0].exists(), "Temp dir should be cleaned up"


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_session_proposals_forwarded() -> None:
    """AI proposals are forwarded to the WebSocket client."""
    created = _make_created_event()
    proposals = _make_proposals_event(
        [
            {
                "id": "p1",
                "file": "tasks/main.yml",
                "rule_id": "L042",
                "line_start": 10,
                "line_end": 15,
                "before_text": "old",
                "after_text": "new",
                "diff_hunk": "- old\n+ new",
                "confidence": 0.85,
                "explanation": "Use FQCN",
                "tier": 2,
            },
        ]
    )
    result = _make_result_event()
    closed = _make_closed_event()
    mock_stream = _mock_fix_stream(created, proposals, result, closed)

    file_content = base64.b64encode(b"---\n- hosts: all\n").decode()
    ws = MockWebSocket(
        [
            {"type": "start", "options": {"enable_ai": True}},
            {"type": "file", "path": "tasks/main.yml", "content": file_content},
            {"type": "files_done"},
        ]
    )

    with (
        patch("apme_gateway.session_client.grpc.aio.insecure_channel") as mock_ch_fn,
        patch("apme_gateway.session_client.engine_pb2_grpc.EngineStub") as mock_stub_cls,
    ):
        mock_ch = AsyncMock()
        mock_ch_fn.return_value = mock_ch
        mock_stub = MagicMock()
        mock_stub.FixSession.return_value = mock_stream
        mock_stub_cls.return_value = mock_stub

        from apme_gateway.session_client import handle_session

        await handle_session(ws, "localhost:50051")

    proposal_msgs = [m for m in ws.sent if m["type"] == "proposals"]
    assert len(proposal_msgs) == 1
    raw = proposal_msgs[0]["proposals"]
    assert isinstance(raw, list)
    proposals_list = raw
    assert len(proposals_list) == 1
    assert proposals_list[0]["rule_id"] == "L042"
    assert proposals_list[0]["confidence"] == 0.85


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_path_traversal_rejected() -> None:
    """Files with ``..`` in the path are rejected."""
    from apme_gateway.session_client import _sanitize_path

    with pytest.raises(ValueError, match="traversal"):
        _sanitize_path("../../etc/passwd")

    assert _sanitize_path("roles/tasks/main.yml") == "roles/tasks/main.yml"
    assert _sanitize_path("/absolute/path.yml") == "absolute/path.yml"


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_dot_only_path_rejected() -> None:
    """Paths resolving to '.' (the temp dir itself) are rejected."""
    from apme_gateway.session_client import _sanitize_path

    with pytest.raises(ValueError, match="Invalid file path"):
        _sanitize_path(".")

    with pytest.raises(ValueError, match="Invalid file path"):
        _sanitize_path("./")

    assert _sanitize_path("a/./b.yml") == "a/b.yml"
