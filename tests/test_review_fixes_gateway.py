"""Gateway review-fix regression tests for operator waits, SCM encoding, and session skips.

Covers driver operator timeouts and stale-queue draining, text-blob
encoding selection for GitHub/GitLab push paths, and session-client
validator-skip forwarding with gRPC channel limits.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace
from typing import NoReturn
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import apme_gateway.session_client as session_client
from apme.v1 import engine_pb2, engine_pb2_grpc
from apme.v1.common_pb2 import Violation
from apme_engine.config_env import get_env_float
from apme_gateway.operation_types import ApprovalGate
from apme_gateway.scan.driver import (
    _OP_APPROVE_TIMEOUT_DEFAULT_S,
    _OP_BEGIN_TIMEOUT_DEFAULT_S,
    _OP_ESCALATE_TIMEOUT_DEFAULT_S,
    OperatorAnswerQueue,
    _drain_queue,
    _op_timeout,
    run_project_operation,
)
from apme_gateway.scm.github import GitHubProvider
from apme_gateway.scm.gitlab import GitLabProvider
from apme_gateway.scm.text import is_text_blob

_GRPC_MAX_MSG = 50 * 1024 * 1024


def test_approval_gate_ids_are_opaque_and_unique() -> None:
    """Gate IDs are opaque hex strings that do not collide within a process."""
    loop = asyncio.new_event_loop()
    try:
        gate_a = ApprovalGate(future=loop.create_future())
        gate_b = ApprovalGate(future=loop.create_future())
    finally:
        loop.close()
    assert len(gate_a.gate_id) == 32
    assert all(c in "0123456789abcdef" for c in gate_a.gate_id)
    assert gate_a.gate_id != gate_b.gate_id


# ── _op_timeout ───────────────────────────────────────────────────────


def test_op_timeout_unset_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset env returns the caller-provided default.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.delenv("APME_TEST_OP_TIMEOUT_UNSET_S", raising=False)
    assert _op_timeout("APME_TEST_OP_TIMEOUT_UNSET_S", 600.0) == 600.0


def test_op_timeout_invalid_returns_default_and_warns(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unparsable env falls back to the default with a warning.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        caplog: Pytest log-capture fixture.
    """
    monkeypatch.setenv("APME_TEST_OP_TIMEOUT_BAD_S", "not-a-number")
    with caplog.at_level(logging.WARNING):
        assert _op_timeout("APME_TEST_OP_TIMEOUT_BAD_S", 600.0) == 600.0
    assert "APME_TEST_OP_TIMEOUT_BAD_S" in caplog.text


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("raw", "expected"),
    [
        ("inf", 600.0),
        ("-inf", 600.0),
        ("nan", 600.0),
        ("0", 600.0),
        ("-1", 600.0),
        ("-0.5", 600.0),
        ("bogus", 600.0),
        ("", 600.0),
        ("   ", 600.0),
        ("30", 30.0),
        ("30.5", 30.5),
    ],
)
def test_op_timeout_edge_table(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
    expected: float,
) -> None:
    """Edge values degrade to the default; positive finite values pass through.

    Args:
        monkeypatch: Pytest env-patching fixture.
        raw: Raw env value under test.
        expected: Expected timeout seconds.
    """
    monkeypatch.setenv("APME_TEST_OP_TIMEOUT_EDGE_S", raw)
    assert _op_timeout("APME_TEST_OP_TIMEOUT_EDGE_S", 600.0) == expected


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "raw",
    ["inf", "-inf", "nan", "0", "-5", "bogus", "", "45", "0.25"],
)
def test_op_timeout_matches_get_env_float_positive_only(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    """_op_timeout stays a thin wrapper over get_env_float(positive_only=True).

    Args:
        monkeypatch: Pytest env-patching fixture.
        raw: Raw env value under test.
    """
    name = "APME_TEST_OP_TIMEOUT_UNIFORM_S"
    monkeypatch.setenv(name, raw)
    assert _op_timeout(name, 600.0) == get_env_float(name, 600.0, positive_only=True)


# ── _drain_queue ──────────────────────────────────────────────────────


async def test_drain_queue_empty_returns_zero() -> None:
    """Draining an empty queue discards nothing."""
    queue: asyncio.Queue[str] = asyncio.Queue()
    assert _drain_queue(queue) == 0


async def test_drain_queue_discards_stale_items() -> None:
    """Two queued items are discarded; a second drain finds nothing."""
    queue: asyncio.Queue[str] = asyncio.Queue()
    queue.put_nowait("first")
    queue.put_nowait("second")
    assert _drain_queue(queue) == 2
    assert queue.empty()
    assert _drain_queue(queue) == 0


# ── Stale-queue freshness (#19) ───────────────────────────────────────


async def test_post_timeout_drain_discards_late_arrival() -> None:
    """A late arrival after a timed-out wait is discarded by a post-timeout drain."""
    queue: asyncio.Queue[str] = asyncio.Queue()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(queue.get(), timeout=0.01)
    queue.put_nowait("late-answer")
    assert _drain_queue(queue) == 1
    assert queue.empty()


async def test_next_wait_blocks_after_drain_proving_freshness() -> None:
    """Drain-before-wait forces the next event to block for a fresh answer."""
    queue: asyncio.Queue[str] = asyncio.Queue()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(queue.get(), timeout=0.01)
    # Late arrival for the previous (already timed-out) prompt lands now.
    queue.put_nowait("stale-answer")
    # Driver pattern: drain stale answers before waiting for the next event.
    assert _drain_queue(queue) == 1
    # The next wait must block — the stale item was not consumed.
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(queue.get(), timeout=0.01)


async def test_without_drain_stale_is_consumed_immediately() -> None:
    """Contrast check: without a drain the stale item would be consumed at once."""
    queue: asyncio.Queue[str] = asyncio.Queue()
    queue.put_nowait("stale-answer")
    got = await asyncio.wait_for(queue.get(), timeout=0.01)
    assert got == "stale-answer"


# ── Operator-wait fallback defaults ───────────────────────────────────


def test_operator_timeout_default_constants() -> None:
    """Per-event timeout defaults stay positive with approve longest.

    Current values: 600s begin/escalate, 1800s approve (see driver.py).
    """
    assert _OP_BEGIN_TIMEOUT_DEFAULT_S > 0
    assert _OP_ESCALATE_TIMEOUT_DEFAULT_S > 0
    assert _OP_APPROVE_TIMEOUT_DEFAULT_S > 0
    assert _OP_APPROVE_TIMEOUT_DEFAULT_S >= _OP_BEGIN_TIMEOUT_DEFAULT_S
    assert _OP_APPROVE_TIMEOUT_DEFAULT_S >= _OP_ESCALATE_TIMEOUT_DEFAULT_S


def test_op_timeout_per_event_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset per-event env vars resolve to positive documented fallbacks.

    Current values: 600s begin/escalate, 1800s approve (see driver.py).

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.delenv("APME_OP_BEGIN_TIMEOUT_S", raising=False)
    monkeypatch.delenv("APME_OP_ESCALATE_TIMEOUT_S", raising=False)
    monkeypatch.delenv("APME_OP_APPROVE_TIMEOUT_S", raising=False)
    begin = _op_timeout("APME_OP_BEGIN_TIMEOUT_S", _OP_BEGIN_TIMEOUT_DEFAULT_S)
    escalate = _op_timeout("APME_OP_ESCALATE_TIMEOUT_S", _OP_ESCALATE_TIMEOUT_DEFAULT_S)
    approve = _op_timeout("APME_OP_APPROVE_TIMEOUT_S", _OP_APPROVE_TIMEOUT_DEFAULT_S)
    assert begin > 0
    assert escalate > 0
    assert approve > 0
    assert approve >= begin
    assert approve >= escalate


# ── is_text_blob ──────────────────────────────────────────────────────


def test_is_text_blob_utf8() -> None:
    """Valid UTF-8 bytes are treated as text."""
    assert is_text_blob(b"hello: world\n") is True


def test_is_text_blob_empty() -> None:
    """Empty content decodes as UTF-8, so it counts as text (read from code)."""
    assert is_text_blob(b"") is True


def test_is_text_blob_invalid_utf8() -> None:
    """Bytes that fail UTF-8 decoding are treated as binary."""
    assert is_text_blob(b"\xff\xfe\x00\x01binary") is False


def test_is_text_blob_null_bytes_are_text_under_current_heuristic() -> None:
    """NUL bytes decode as valid UTF-8, so the current heuristic returns True."""
    assert is_text_blob(b"\x00") is True
    assert is_text_blob(b"a\x00b") is True


# ── Push-branch encoding selection ────────────────────────────────────


def _httpx_like_response(payload: dict[str, object], status: int = 200) -> MagicMock:
    """Build a mock with the httpx surface used by the paced GitHub helpers.

    Args:
        payload: JSON body returned by ``resp.json()``.
        status: HTTP status code to expose.

    Returns:
        Configured MagicMock response.
    """
    resp = MagicMock()
    resp.status_code = status
    resp.is_success = status < 400
    resp.headers = {}
    resp.request = MagicMock()
    resp.aclose = AsyncMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


async def test_github_push_files_text_vs_binary_encoding() -> None:
    """GitHub blobs use utf-8 for text and base64 for binary content."""
    provider = GitHubProvider()
    blob_payloads: list[dict[str, object]] = []

    async def _fake_get(*args: object, **kwargs: object) -> MagicMock:
        """Serve the commit-detail lookup with a canned base tree.

        Args:
            *args: Positional request args (URL first).
            **kwargs: Request keyword args.

        Returns:
            Mock response carrying the base tree SHA.
        """
        url = str(args[0]) if args else str(kwargs.get("url", ""))
        if "/git/commits/" in url:
            return _httpx_like_response({"tree": {"sha": "basetree"}})
        return _httpx_like_response({"object": {"sha": "a" * 40}})

    async def _fake_request(*args: object, **kwargs: object) -> MagicMock:
        """Capture blob payloads and serve canned tree/commit responses.

        Args:
            *args: Positional request args (method, URL).
            **kwargs: Request keyword args including ``json``.

        Returns:
            Mock response for the requested Git object URL.
        """
        url = str(args[1]) if len(args) > 1 else str(kwargs.get("url", ""))
        body = kwargs.get("json")
        assert isinstance(body, dict)
        if url.endswith("/git/blobs"):
            blob_payloads.append(body)
            return _httpx_like_response({"sha": f"blob{len(blob_payloads)}"})
        if url.endswith("/git/trees"):
            return _httpx_like_response({"sha": "treesha"})
        if url.endswith("/git/commits"):
            return _httpx_like_response({"sha": "commitsha"})
        return _httpx_like_response({})

    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=_fake_get)
    client.request = AsyncMock(side_effect=_fake_request)

    binary = b"\xff\xfe\x00\x01binary"
    with (
        patch("apme_gateway.scm.github._BLOB_MIN_INTERVAL_S", 0.0),
        patch.object(GitHubProvider, "_client", return_value=client),
    ):
        sha = await provider.push_files(
            "https://github.com/o/r.git",
            "apme/fix",
            {"hello.txt": b"hello", "blob.bin": binary},
            "msg",
            "tok",
            parent_commit_sha="a" * 40,
        )

    assert sha == "commitsha"
    assert len(blob_payloads) == 2
    assert blob_payloads[0]["encoding"] == "utf-8"
    assert blob_payloads[0]["content"] == "hello"
    assert blob_payloads[1]["encoding"] == "base64"
    assert blob_payloads[1]["content"] == base64.b64encode(binary).decode()


async def test_gitlab_push_files_text_vs_base64_encoding() -> None:
    """GitLab actions use text for UTF-8 and base64 for binary content."""
    provider = GitLabProvider()
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=_httpx_like_response({"id": "newsha"}))

    binary = b"\xff\xfe\x00\x01binary"
    with patch.object(GitLabProvider, "_client", return_value=client):
        sha = await provider.push_files(
            "https://gitlab.com/g/r.git",
            "apme/fix",
            {"hello.txt": b"hello", "blob.bin": binary},
            "msg",
            "tok",
        )

    assert sha == "newsha"
    actions = client.post.call_args.kwargs["json"]["actions"]
    assert actions[0]["encoding"] == "text"
    assert actions[0]["content"] == "hello"
    assert actions[1]["encoding"] == "base64"
    assert actions[1]["content"] == base64.b64encode(binary).decode()


# ── session_client skip forwarding + channel limits ───────────────────


class _FakeSessionWS:
    """Minimal WebSocket double recording sent messages."""

    def __init__(self, messages: list[dict[str, object]]) -> None:
        """Store incoming messages in reverse for pop-order delivery.

        Args:
            messages: Messages to serve from ``receive_json``.
        """
        self._incoming = list(reversed(messages))
        self.sent: list[dict[str, object]] = []

    async def receive_json(self) -> dict[str, object]:
        """Pop the next incoming message.

        Returns:
            Next queued message dict.

        Raises:
            Exception: When no messages remain (ends the WS reader).
        """
        if not self._incoming:
            raise Exception("No more messages")  # noqa: TRY002
        item = self._incoming.pop()
        assert isinstance(item, dict)
        return item

    async def send_json(self, data: dict[str, object]) -> None:
        """Record a message sent to the client.

        Args:
            data: JSON-serializable payload.
        """
        self.sent.append(data)


def _make_result_event() -> MagicMock:
    """Build a mock ``result`` SessionEvent.

    Returns:
        Mock SessionEvent with an empty result payload.
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
        Mock SessionEvent with the closed oneof active.
    """
    event = MagicMock()
    event.WhichOneof.return_value = "closed"
    return event


async def _mock_fix_stream(*events: MagicMock) -> AsyncIterator[MagicMock]:
    """Yield mock SessionEvents as an async iterator.

    Args:
        *events: Mock SessionEvent objects to yield.

    Yields:
        MagicMock: Each mock event in sequence.
    """
    for event in events:
        yield event
        await asyncio.sleep(0)


async def _run_session_with_options(
    monkeypatch: pytest.MonkeyPatch,
    options: dict[str, object],
) -> tuple[list[dict[str, object]], dict[str, object], dict[str, object]]:
    """Drive handle_session with stubbed gRPC/DB layers and capture wiring.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        options: WS ``start`` options dict.

    Returns:
        Tuple of (sent WS messages, yield_scan_chunks kwargs, channel call info).
    """
    import apme_gateway.session_client as session_client

    captured_yield: dict[str, object] = {}
    captured_channel: dict[str, object] = {}

    def _fake_yield_scan_chunks(target: object, **kwargs: object) -> Iterator[engine_pb2.ScanChunk]:
        """Capture skip kwargs and yield one real ScanChunk.

        Args:
            target: Upload directory (ignored by the stub).
            **kwargs: Forwarded scan options including skip flags.

        Yields:
            engine_pb2.ScanChunk: Single last chunk with skip flags applied.
        """
        captured_yield.update(kwargs)
        opts = engine_pb2.ScanOptions(
            skip_collection_health=bool(kwargs.get("skip_collection_health", False)),
            skip_dep_audit=bool(kwargs.get("skip_dep_audit", False)),
        )
        yield engine_pb2.ScanChunk(
            scan_id="test-scan",
            project_root="upload",
            options=opts,
            files=[],
            last=True,
        )

    async def _no_rule_configs() -> list[object]:
        """Stub DB rule-config loading with an empty catalog.

        Returns:
            Empty list.
        """
        return []

    async def _no_galaxy_servers() -> list[object]:
        """Stub Galaxy server injection with no servers.

        Returns:
            Empty list.
        """
        return []

    class _FakeChannel:
        """Stub gRPC channel recording close calls."""

        async def close(self, grace: object = None) -> None:
            """Record a channel close.

            Args:
                grace: Grace period (ignored).
            """

    def _fake_insecure_channel(*args: object, **kwargs: object) -> _FakeChannel:
        """Capture channel address and options without dialing.

        Args:
            *args: Positional channel args (address first).
            **kwargs: Channel keyword args including ``options``.

        Returns:
            Fake channel instance.
        """
        captured_channel["args"] = args
        captured_channel["kwargs"] = kwargs
        return _FakeChannel()

    mock_stub = MagicMock()
    result_event = _make_result_event()
    closed_event = _make_closed_event()

    def _fake_fix_session(request_stream: AsyncIterator[object]) -> AsyncIterator[MagicMock]:
        """Drain the request stream then replay canned result/closed events.

        Draining forces the gateway ``_chunks_with_fix_options`` generator to
        run, which is what captures the skip flags into ``captured_yield``.

        Args:
            request_stream: Gateway command stream (uploads then WS commands).

        Returns:
            Async iterator replaying result then closed events.
        """

        async def _response() -> AsyncIterator[MagicMock]:
            """Drain requests before replaying the canned response.

            Yields:
                MagicMock: Canned result then closed SessionEvents.
            """
            async for _cmd in request_stream:
                pass
            async for event in _mock_fix_stream(result_event, closed_event):
                yield event

        return _response()

    mock_stub.FixSession = _fake_fix_session

    def _fake_stub_factory(channel: object) -> MagicMock:
        """Return the canned stub regardless of channel.

        Args:
            channel: gRPC channel (ignored).

        Returns:
            Mock Engine stub.
        """
        return mock_stub

    monkeypatch.setattr(session_client, "yield_scan_chunks", _fake_yield_scan_chunks)
    monkeypatch.setattr(session_client, "_load_scan_rule_configs", _no_rule_configs)
    monkeypatch.setattr(
        "apme_gateway._galaxy_inject.load_galaxy_server_defs",
        _no_galaxy_servers,
    )
    monkeypatch.setattr(
        "apme_gateway.session_client.grpc.aio.insecure_channel",
        _fake_insecure_channel,
    )
    monkeypatch.setattr(
        "apme_gateway.session_client.engine_pb2_grpc.EngineStub",
        _fake_stub_factory,
    )

    file_content = base64.b64encode(b"---\n").decode()
    ws = _FakeSessionWS(
        [
            {"type": "start", "options": options},
            {"type": "file", "path": "a.yml", "content": file_content},
            {"type": "files_done"},
        ]
    )
    await session_client.handle_session(ws, "localhost:50051")
    return ws.sent, captured_yield, captured_channel


async def test_session_client_forwards_skip_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit true skip options reach yield_scan_chunks as True.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    sent, captured, _ = await _run_session_with_options(
        monkeypatch,
        {"skip_collection_health": True, "skip_dep_audit": True},
    )
    assert captured.get("skip_collection_health") is True
    assert captured.get("skip_dep_audit") is True
    assert "result" in [m["type"] for m in sent]


async def test_session_client_absent_skips_default_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Absent skip options preserve the False default.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    sent, captured, _ = await _run_session_with_options(monkeypatch, {})
    assert captured.get("skip_collection_health") is False
    assert captured.get("skip_dep_audit") is False
    assert "result" in [m["type"] for m in sent]


async def test_session_client_explicit_false_stays_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit false skip options are forwarded as False.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    _, captured, _ = await _run_session_with_options(
        monkeypatch,
        {"skip_collection_health": False, "skip_dep_audit": False},
    )
    assert captured.get("skip_collection_health") is False
    assert captured.get("skip_dep_audit") is False


async def test_session_client_unknown_skip_warns_and_ignored(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unknown skip_* options log a warning and do not affect known flags.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        caplog: Pytest log-capture fixture.
    """
    with caplog.at_level(logging.WARNING):
        sent, captured, _ = await _run_session_with_options(
            monkeypatch,
            {"skip_foo": True},
        )
    assert "skip_foo" in caplog.text
    assert "Unknown validator-skip" in caplog.text
    assert captured.get("skip_collection_health") is False
    assert captured.get("skip_dep_audit") is False
    assert "result" in [m["type"] for m in sent]


async def test_session_client_channel_uses_50mib_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    """The FixSession channel sets 50MiB send/receive message limits.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    _, _, captured_channel = await _run_session_with_options(monkeypatch, {})
    kwargs = captured_channel.get("kwargs")
    assert isinstance(kwargs, dict)
    options = kwargs.get("options")
    assert options == [
        ("grpc.max_send_message_length", _GRPC_MAX_MSG),
        ("grpc.max_receive_message_length", _GRPC_MAX_MSG),
    ]
    assert _GRPC_MAX_MSG == 50 * 1024 * 1024


def test_session_client_grpc_max_matches_driver() -> None:
    """Session-client and scan-driver 50MiB limits stay in sync."""
    import apme_gateway.scan.driver as driver
    import apme_gateway.session_client as session_client

    assert session_client._GRPC_MAX_MSG == driver._GRPC_MAX_MSG == 50 * 1024 * 1024


# ── next_answer ───────────────────────────────────────────────


async def test_next_answer_forwards_answer() -> None:
    """An answer arriving during the wait is returned verbatim."""
    queue: OperatorAnswerQueue[str] = OperatorAnswerQueue()

    async def _answer_soon() -> None:
        """Deliver the answer mid-wait so it counts as fresh, not stale."""
        await asyncio.sleep(0.01)
        await queue.put("yes")

    answer_task = asyncio.ensure_future(_answer_soon())
    try:
        assert await queue.next_answer(5.0, "Test wait", "defaulting") == "yes"
    finally:
        await answer_task


async def test_next_answer_timeout_returns_none_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An empty queue times out to None with a warning naming the wait.

    Args:
        caplog: Pytest log-capture fixture.
    """
    queue: OperatorAnswerQueue[str] = OperatorAnswerQueue()
    with caplog.at_level(logging.WARNING):
        assert await queue.next_answer(0.01, "Test wait", "defaulting") is None
    assert "Test wait timed out" in caplog.text


async def test_next_answer_preserves_current_prompt_answer() -> None:
    """An answer queued after the prompt generation is claimed is returned."""
    queue: OperatorAnswerQueue[str] = OperatorAnswerQueue()
    queue.begin_prompt()
    await queue.put("fresh-answer")
    assert await queue.next_answer(0.05, "Test wait", "defaulting") == "fresh-answer"


async def test_next_answer_discards_stale_generation() -> None:
    """A late answer for a timed-out prompt does not satisfy the next wait."""
    queue: OperatorAnswerQueue[str] = OperatorAnswerQueue()
    queue.begin_prompt()
    await queue.put("late-for-prompt-1")
    queue.begin_prompt()
    assert await queue.next_answer(0.01, "Prompt 2 wait", "defaulting") is None
    assert queue.empty()


async def test_next_answer_rejects_late_answer_tagged_at_next_generation() -> None:
    """A late Gate 1 answer keeps its prompt generation and does not satisfy Gate 2."""
    queue: OperatorAnswerQueue[list[str]] = OperatorAnswerQueue()
    queue.begin_prompt()  # Gate 1 generation = 1
    gate1_generation = queue.current_generation
    queue.begin_prompt()  # Gate 2 generation = 2
    await queue.put(["t1-001"], for_generation=gate1_generation)
    assert await queue.next_answer(0.01, "Gate 2 wait", "defaulting") is None


async def test_next_answer_preserves_newer_generation_answer() -> None:
    """A newer-generation answer is re-queued for the next wait."""
    queue: OperatorAnswerQueue[str] = OperatorAnswerQueue()
    queue.begin_prompt()  # generation = 1
    queue.begin_prompt()  # generation = 2
    await queue.put("future-answer", for_generation=3)

    assert await queue.next_answer(0.01, "Gate 2 wait", "defaulting") is None

    queue.begin_prompt()  # generation = 3
    assert await queue.next_answer(0.05, "Gate 3 wait", "defaulting") == "future-answer"


async def test_next_answer_accepts_gate_generation_after_callback() -> None:
    """REST bridge answers tagged with gate.prompt_generation satisfy the driver wait."""
    queue: OperatorAnswerQueue[list[str]] = OperatorAnswerQueue()
    gate_generation = queue.begin_prompt()

    async def _bridge_put() -> None:
        await asyncio.sleep(0.05)
        await queue.put(["p1"], for_generation=gate_generation)

    bridge_task = asyncio.ensure_future(_bridge_put())
    try:
        result = await queue.next_answer(
            1.0,
            "Approval operator wait",
            "declining all proposals",
            expected_generation=gate_generation,
        )
    finally:
        await bridge_task
    assert result == ["p1"]


# ── run_project_operation operator waits ──────────────────────────


class _FakeDriverChannel:
    """Fake grpc.aio channel that never dials."""

    async def close(self, grace: object = None) -> None:
        """No-op close.

        Args:
            grace: Grace period (ignored).
        """


class _FakeEngineStub:
    """Scripted FixSession stub that records driver commands."""

    def __init__(self, events: list[engine_pb2.SessionEvent]) -> None:
        """Store scripted events and an empty command log.

        Args:
            events: SessionEvents yielded in order as commands arrive.
        """
        self._events = list(events)
        self.commands: list[engine_pb2.SessionCommand] = []

    def FixSession(
        self, request_iter: AsyncIterator[engine_pb2.SessionCommand]
    ) -> AsyncIterator[engine_pb2.SessionEvent]:
        """Return the scripted event stream bound to *request_iter*.

        Args:
            request_iter: Driver command stream to drain.

        Returns:
            Async iterator of scripted SessionEvents.
        """
        return self._run(request_iter)

    async def _run(
        self, request_iter: AsyncIterator[engine_pb2.SessionCommand]
    ) -> AsyncIterator[engine_pb2.SessionEvent]:
        """Yield one event per received command until the stream closes.

        Args:
            request_iter: Driver command stream to drain.

        Yields:
            engine_pb2.SessionEvent: Next scripted event in order.
        """
        pending = list(self._events)
        yield pending.pop(0)
        async for cmd in request_iter:
            self.commands.append(cmd)
            if pending:
                yield pending.pop(0)


async def _fill_soon[T](queue: OperatorAnswerQueue[T], value: T, delay: float = 0.1) -> None:
    """Deliver a queue item mid-wait so it counts as a fresh answer.

    Args:
        queue: Queue to fill.
        value: Item to enqueue after the delay.
        delay: Seconds to wait before enqueueing.
    """
    await asyncio.sleep(delay)
    await queue.put(value)


def _install_fake_engine(
    monkeypatch: pytest.MonkeyPatch,
    events: list[engine_pb2.SessionEvent],
    stub_box: list[_FakeEngineStub],
) -> None:
    """Replace clone/chunk/channel/stub plumbing with a scripted fake engine.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        events: Scripted SessionEvents for the fake stub to emit.
        stub_box: Single-element outbox receiving the installed stub.
    """

    async def _fake_clone(*args: object, **kwargs: object) -> None:
        """Skip cloning; the temp dir stays empty.

        Args:
            *args: Positional args (ignored).
            **kwargs: Keyword args (ignored).
        """

    def _fake_head(*args: object, **kwargs: object) -> str:
        """Report an empty clone HEAD.

        Args:
            *args: Positional args (ignored).
            **kwargs: Keyword args (ignored).

        Returns:
            Empty commit SHA string.
        """
        return ""

    def _fake_chunks(*args: object, **kwargs: object) -> list[object]:
        """Report no upload chunks so only prompted commands flow.

        Args:
            *args: Positional args (ignored).
            **kwargs: Keyword args (ignored).

        Returns:
            Empty chunk list.
        """
        return []

    monkeypatch.setattr("apme_gateway.scan.driver.clone_repo", _fake_clone)
    monkeypatch.setattr("apme_gateway.scan.driver.get_clone_head", _fake_head)
    monkeypatch.setattr("apme_gateway.scan.driver.yield_scan_chunks", _fake_chunks)
    monkeypatch.setattr("grpc.aio.insecure_channel", lambda *args, **kwargs: _FakeDriverChannel())
    stub = _FakeEngineStub(events)
    stub_box.append(stub)
    monkeypatch.setattr(engine_pb2_grpc, "EngineStub", lambda channel: stub)


def _result_event() -> engine_pb2.SessionEvent:
    """Build a terminal result event.

    Returns:
        SessionEvent carrying an empty SessionResult.
    """
    return engine_pb2.SessionEvent(result=engine_pb2.SessionResult())


async def _run_operation(
    monkeypatch: pytest.MonkeyPatch,
    events: list[engine_pb2.SessionEvent],
    stub_box: list[_FakeEngineStub],
    **queues: object,
) -> tuple[str, engine_pb2.SessionResult | None, str]:
    """Run a project operation against the scripted fake engine.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        events: Scripted SessionEvents for the fake stub to emit.
        stub_box: Single-element outbox receiving the installed stub.
        **queues: Operator queues forwarded to run_project_operation.

    Returns:
        Tuple of (scan_id, SessionResult or None, clone SHA).
    """
    _install_fake_engine(monkeypatch, events, stub_box)
    return await run_project_operation(
        project_id="00000000-0000-0000-0000-000000000000",
        repo_url="https://example.com/r.git",
        branch="main",
        engine_address="localhost:1",
        **queues,  # type: ignore[arg-type]
    )


async def test_run_begin_timeout_auto_begins(monkeypatch: pytest.MonkeyPatch) -> None:
    """A silent operator on FindingsReady auto-begins instead of hanging.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("APME_OP_BEGIN_TIMEOUT_S", "0.05")
    stubs: list[_FakeEngineStub] = []
    findings = engine_pb2.SessionEvent(findings=engine_pb2.FindingsReady())
    _, result, _ = await _run_operation(
        monkeypatch,
        [findings, _result_event()],
        stubs,
        assess_pause=True,
        begin_remediate_queue=OperatorAnswerQueue(),
    )
    assert result is not None
    kinds = [cmd.WhichOneof("command") for cmd in stubs[0].commands]
    assert "begin_remediate" in kinds


async def test_run_escalate_timeout_allows_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """A silent operator on AiTriageReady escalates every candidate path.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("APME_OP_ESCALATE_TIMEOUT_S", "0.05")
    stubs: list[_FakeEngineStub] = []
    triage = engine_pb2.SessionEvent(
        ai_triage=engine_pb2.AiTriageReady(candidates=[Violation(path="a.yml"), Violation(path="b.yml")])
    )
    await _run_operation(
        monkeypatch,
        [triage, _result_event()],
        stubs,
        escalate_ai_queue=OperatorAnswerQueue(),
    )
    escalates = [cmd for cmd in stubs[0].commands if cmd.WhichOneof("command") == "ai_escalate"]
    assert len(escalates) == 1
    assert sorted(t.path for t in escalates[0].ai_escalate.targets) == ["a.yml", "b.yml"]


async def test_run_escalate_answer_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator answer scopes escalation to the chosen targets.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    queue: OperatorAnswerQueue[list[dict[str, object]]] = OperatorAnswerQueue()
    # Deliver mid-wait: a pre-queued item would be (correctly) drained as stale.
    answer: list[dict[str, object]] = [{"path": "a.yml", "rule_ids": []}]
    fill_task = asyncio.ensure_future(_fill_soon(queue, answer))
    try:
        stubs: list[_FakeEngineStub] = []
        triage = engine_pb2.SessionEvent(
            ai_triage=engine_pb2.AiTriageReady(candidates=[Violation(path="a.yml"), Violation(path="b.yml")])
        )
        await _run_operation(
            monkeypatch,
            [triage, _result_event()],
            stubs,
            escalate_ai_queue=queue,
        )
    finally:
        await fill_task
    escalates = [cmd for cmd in stubs[0].commands if cmd.WhichOneof("command") == "ai_escalate"]
    assert len(escalates) == 1
    assert [t.path for t in escalates[0].ai_escalate.targets] == ["a.yml"]


async def test_run_approve_timeout_declines_all(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A silent operator on ProposalsReady declines every proposal.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        caplog: Pytest log-capture fixture.
    """
    monkeypatch.setenv("APME_OP_APPROVE_TIMEOUT_S", "0.05")
    stubs: list[_FakeEngineStub] = []
    proposals = engine_pb2.SessionEvent(proposals=engine_pb2.ProposalsReady())
    with caplog.at_level(logging.WARNING):
        await _run_operation(
            monkeypatch,
            [proposals, _result_event()],
            stubs,
            approval_queue=OperatorAnswerQueue(),
        )
    approves = [cmd for cmd in stubs[0].commands if cmd.WhichOneof("command") == "approve"]
    assert len(approves) == 1
    assert list(approves[0].approve.approved_ids) == []
    # A lone approve timeout is not the paired degraded case.
    assert "Operator timeouts paired" not in caplog.text


async def test_run_approve_answer_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator approval list is forwarded verbatim.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    queue: OperatorAnswerQueue[list[str]] = OperatorAnswerQueue()
    # Deliver mid-wait: a pre-queued item would be (correctly) drained as stale.
    fill_task = asyncio.ensure_future(_fill_soon(queue, ["p1"]))
    try:
        stubs: list[_FakeEngineStub] = []
        proposals = engine_pb2.SessionEvent(proposals=engine_pb2.ProposalsReady())
        await _run_operation(
            monkeypatch,
            [proposals, _result_event()],
            stubs,
            approval_queue=queue,
        )
    finally:
        await fill_task
    approves = [cmd for cmd in stubs[0].commands if cmd.WhichOneof("command") == "approve"]
    assert len(approves) == 1
    assert list(approves[0].approve.approved_ids) == ["p1"]


async def test_run_paired_timeouts_emit_degraded_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Escalate-then-approve timeouts pair into one degraded warning.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        caplog: Pytest log-capture fixture.
    """
    monkeypatch.setenv("APME_OP_ESCALATE_TIMEOUT_S", "0.05")
    monkeypatch.setenv("APME_OP_APPROVE_TIMEOUT_S", "0.05")
    stubs: list[_FakeEngineStub] = []
    triage = engine_pb2.SessionEvent(
        ai_triage=engine_pb2.AiTriageReady(candidates=[Violation(path="a.yml"), Violation(path="b.yml")])
    )
    proposals = engine_pb2.SessionEvent(proposals=engine_pb2.ProposalsReady())
    with caplog.at_level(logging.WARNING):
        _, result, _ = await _run_operation(
            monkeypatch,
            [triage, proposals, _result_event()],
            stubs,
            escalate_ai_queue=OperatorAnswerQueue(),
            approval_queue=OperatorAnswerQueue(),
        )
    assert result is not None
    assert "Operator timeouts paired" in caplog.text
    escalates = [cmd for cmd in stubs[0].commands if cmd.WhichOneof("command") == "ai_escalate"]
    assert sorted(t.path for t in escalates[0].ai_escalate.targets) == ["a.yml", "b.yml"]
    approves = [cmd for cmd in stubs[0].commands if cmd.WhichOneof("command") == "approve"]
    assert list(approves[0].approve.approved_ids) == []


# ── _load_scan_rule_configs canonicalization ──────────────────────


class _FakeDBSession:
    """Minimal async context manager standing in for the DB session."""

    async def __aenter__(self) -> object:
        """Return a dummy DB handle.

        Returns:
            Dummy database handle.
        """
        return object()

    async def __aexit__(self, *exc: object) -> bool:
        """Exit without suppressing.

        Args:
            *exc: Exception info (ignored).

        Returns:
            False (never suppress).
        """
        return False


def _stub_rule_rows(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, object]]) -> None:
    """Serve canned rule rows from the gateway DB query.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        rows: Rule rows with rule_id, severity, enabled, enforced.
    """

    async def _fake_list(db: object) -> list[dict[str, object]]:
        """Return the canned rows regardless of session.

        Args:
            db: Database handle (ignored).

        Returns:
            Canned rule rows.
        """
        return rows

    monkeypatch.setattr(session_client, "get_session", lambda: _FakeDBSession())
    monkeypatch.setattr(session_client, "list_rules_with_resolved_config", _fake_list)


async def test_load_scan_rule_configs_canonicalizes_prefixed_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prefixed gateway rows reach the Engine as bare rule IDs.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    _stub_rule_rows(
        monkeypatch,
        [
            {"rule_id": "native:L001", "severity": 1, "enabled": True, "enforced": False},
            {"rule_id": "L002", "severity": 2, "enabled": False, "enforced": True},
        ],
    )
    configs = await session_client._load_scan_rule_configs()
    assert [c.rule_id for c in configs] == ["L001", "L002"]


async def test_load_scan_rule_configs_divergent_omits_conflicting_rule(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Conflicting rows for one bare ID fail the scan with the IDs named.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        caplog: Pytest log-capture fixture.
    """
    _stub_rule_rows(
        monkeypatch,
        [
            {"rule_id": "native:L001", "severity": 1, "enabled": False, "enforced": False},
            {"rule_id": "L001", "severity": 1, "enabled": True, "enforced": False},
        ],
    )
    with caplog.at_level(logging.ERROR), pytest.raises(ValueError, match="conflicting rows for"):
        await session_client._load_scan_rule_configs()
    assert "Conflicting gateway rule rows" in caplog.text


async def test_load_scan_rule_configs_partial_conflict_fails_with_ids_named(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Any conflicting bare ID fails fast naming it, even when others are valid.

    Omitting the conflicted ID while sending ``rule_configs_complete=True``
    would trip the Engine's bidirectional audit with a misleading
    "catalog out of sync" error, so the loader refuses instead.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        caplog: Pytest log-capture fixture.
    """
    _stub_rule_rows(
        monkeypatch,
        [
            {"rule_id": "native:L001", "severity": 1, "enabled": False, "enforced": False},
            {"rule_id": "L001", "severity": 1, "enabled": True, "enforced": False},
            {"rule_id": "L002", "severity": 2, "enabled": True, "enforced": True},
        ],
    )
    with caplog.at_level(logging.ERROR), pytest.raises(ValueError, match="conflicting rows for.*L001"):
        await session_client._load_scan_rule_configs()
    assert "Conflicting gateway rule rows" in caplog.text


async def test_load_scan_rule_configs_db_failure_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unavailable DB yields no overrides instead of failing the scan.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """

    def _boom_session() -> NoReturn:
        """Raise like a downed database.

        Raises:
            ConnectionError: Always, simulating DB outage.
        """
        raise ConnectionError("db down")

    monkeypatch.setattr(session_client, "get_session", _boom_session)
    assert await session_client._load_scan_rule_configs() == []


# ── Galaxy proxy sync freshness ───────────────────────────────────


async def _fake_session_count(*args: object, **kwargs: object) -> int:
    """Report one session row for the health DB probe.

    Args:
        *args: Positional args (ignored).
        **kwargs: Keyword args (ignored).

    Returns:
        Session row count.
    """
    return 1


def _stub_health_plumbing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace health DB access and component probes with healthy fakes.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    import apme_gateway.api.router as router_mod
    from apme_gateway.api.schemas import ComponentHealth

    async def _fake_check(name: str, env_var: str, default: str) -> ComponentHealth:
        """Report every component reachable.

        Args:
            name: Component display name.
            env_var: Address env var (ignored).
            default: Default address.

        Returns:
            Healthy ComponentHealth.
        """
        return ComponentHealth(name=name, status="ok", address=default)

    monkeypatch.setattr(router_mod, "get_session", lambda: _FakeDBSession())
    monkeypatch.setattr(
        router_mod,
        "q",
        SimpleNamespace(session_count=_fake_session_count),
    )
    monkeypatch.setattr(router_mod, "_check_component", _fake_check)


def test_proxy_sync_status_initial_not_attempted(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no push attempted, the sync status says so explicitly.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    import apme_gateway._galaxy_proxy_sync as proxy_sync

    monkeypatch.setattr(proxy_sync, "_last_push_ok", None)
    monkeypatch.setattr(proxy_sync, "_last_push_at_mono", None)
    monkeypatch.setattr(proxy_sync, "_last_push_error", None)
    assert proxy_sync.get_sync_status() == {
        "attempted": False,
        "ok": None,
        "age_s": None,
        "error": None,
    }


def test_proxy_sync_status_records_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed push is exposed with its age and error.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    import apme_gateway._galaxy_proxy_sync as proxy_sync

    monkeypatch.setattr(proxy_sync, "_last_push_ok", False)
    monkeypatch.setattr(proxy_sync, "_last_push_at_mono", time.monotonic() - 95.0)
    monkeypatch.setattr(proxy_sync, "_last_push_error", "push failed: HTTPStatusError: 403")
    status = proxy_sync.get_sync_status()
    assert status["attempted"] is True
    assert status["ok"] is False
    assert isinstance(status["age_s"], float)
    assert status["age_s"] >= 95.0
    assert status["error"] == "push failed: HTTPStatusError: 403"


async def test_health_marks_galaxy_proxy_degraded_on_stale_push(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed config push degrades the Galaxy Proxy health component.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    import apme_gateway.api.router as router_mod

    _stub_health_plumbing(monkeypatch)
    monkeypatch.setattr(
        router_mod,
        "get_sync_status",
        lambda: {"attempted": True, "ok": False, "age_s": 95.0, "error": "push failed: 403"},
    )
    health = await router_mod.health()
    assert health.status == "degraded"
    galaxy = next(c for c in health.components if c.name == "Galaxy Proxy")
    assert galaxy.status == "degraded"
    assert galaxy.detail is not None
    assert "failed" in galaxy.detail
    assert "403" in galaxy.detail


async def test_health_galaxy_proxy_ok_detail_on_fresh_push(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful push keeps the component ok with a freshness detail.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    import apme_gateway.api.router as router_mod

    _stub_health_plumbing(monkeypatch)
    monkeypatch.setattr(
        router_mod,
        "get_sync_status",
        lambda: {"attempted": True, "ok": True, "age_s": 12.0, "error": None},
    )
    health = await router_mod.health()
    assert health.status == "ok"
    galaxy = next(c for c in health.components if c.name == "Galaxy Proxy")
    assert galaxy.status == "ok"
    assert galaxy.detail is not None
    assert "ok" in galaxy.detail
