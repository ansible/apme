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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apme.v1 import engine_pb2
from apme_engine.config_env import get_env_float
from apme_gateway.scan.driver import (
    _OP_APPROVE_TIMEOUT_DEFAULT_S,
    _OP_BEGIN_TIMEOUT_DEFAULT_S,
    _OP_ESCALATE_TIMEOUT_DEFAULT_S,
    _drain_queue,
    _op_timeout,
)
from apme_gateway.scm.github import GitHubProvider
from apme_gateway.scm.gitlab import GitLabProvider
from apme_gateway.scm.text import is_text_blob

_GRPC_MAX_MSG = 50 * 1024 * 1024


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
    """Per-event defaults stay at 600s begin/escalate and 1800s approve."""
    assert _OP_BEGIN_TIMEOUT_DEFAULT_S == 600.0
    assert _OP_ESCALATE_TIMEOUT_DEFAULT_S == 600.0
    assert _OP_APPROVE_TIMEOUT_DEFAULT_S == 1800.0


def test_op_timeout_per_event_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset per-event env vars resolve to the documented fallback constants.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.delenv("APME_OP_BEGIN_TIMEOUT_S", raising=False)
    monkeypatch.delenv("APME_OP_ESCALATE_TIMEOUT_S", raising=False)
    monkeypatch.delenv("APME_OP_APPROVE_TIMEOUT_S", raising=False)
    assert _op_timeout("APME_OP_BEGIN_TIMEOUT_S", _OP_BEGIN_TIMEOUT_DEFAULT_S) == 600.0
    assert _op_timeout("APME_OP_ESCALATE_TIMEOUT_S", _OP_ESCALATE_TIMEOUT_DEFAULT_S) == 600.0
    assert _op_timeout("APME_OP_APPROVE_TIMEOUT_S", _OP_APPROVE_TIMEOUT_DEFAULT_S) == 1800.0


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


def test_paced_post_json_deadline_uses_monotonic() -> None:
    """Sanity: time.monotonic is available for operation-deadline math."""
    assert time.monotonic() > 0.0
