"""Unit tests for the project operation lifecycle endpoints (findings #13-15)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from apme_gateway.api.operation_router import operation_events
from apme_gateway.app import create_app
from apme_gateway.operation_registry import get_operation_registry
from apme_gateway.operation_types import (
    ApprovalGate,
    OperationState,
    OperationStatus,
    ProgressEntry,
    Proposal,
    is_must_deliver,
    is_terminal,
)

pytestmark = pytest.mark.usefixtures("gateway_db")


@pytest.fixture  # type: ignore[untyped-decorator]
async def client() -> AsyncIterator[AsyncClient]:
    """Build an async test client for the gateway app.

    Yields:
        AsyncClient: Configured HTTPX client targeting the in-process ASGI app.
    """
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _operation_url(project_id: str, suffix: str) -> str:
    """Build an operation endpoint URL for a project.

    Args:
        project_id: Target project UUID.
        suffix: Endpoint suffix starting with ``/``.

    Returns:
        Full operation endpoint path.
    """
    return f"/api/v1/projects/{project_id}/operation{suffix}"


async def _setup_awaiting_approval(
    *,
    project_id: str = "proj-lifecycle-approve",
    scan_id: str = "scan-lifecycle-approve",
    operation_id: str = "op-lifecycle-approve",
    prompt_generation: int | None = None,
) -> OperationState:
    """Register an operation waiting on proposal approval with two offers.

    Args:
        project_id: Owning project UUID.
        scan_id: Engine scan identifier.
        operation_id: Unique operation identifier.
        prompt_generation: Driver prompt generation for the approval gate.

    Returns:
        The registered operation state in ``AWAITING_APPROVAL``.
    """
    registry = get_operation_registry()
    state = registry.create(
        operation_id=operation_id,
        project_id=project_id,
        scan_id=scan_id,
        scan_type="remediate",
    )
    await registry.set_proposals(
        operation_id,
        [
            Proposal(id="t1-aaa", rule_id="L001", file="a.yml"),
            Proposal(id="t1-bbb", rule_id="L002", file="b.yml"),
        ],
        prompt_generation=prompt_generation,
    )
    assert state.status == OperationStatus.AWAITING_APPROVAL
    return state


async def test_approve_ignores_unknown_ids(client: AsyncClient) -> None:
    """Unknown approve ids are ignored; the future resolves to offered ids only.

    Args:
        client: Async HTTPX test client.
    """
    project_id = "proj-lifecycle-approve"
    state = await _setup_awaiting_approval(project_id=project_id)
    gate = state.approval_gate
    assert gate is not None
    resp = await client.post(
        _operation_url(project_id, "/approve"),
        json={"approved_ids": ["t1-aaa", "zzz-unknown"], "approval_gate_id": gate.gate_id},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "approved"}
    assert state.approval_gate is not None
    assert state.approval_gate.future.done()
    assert state.approval_gate.future.result() == ["t1-aaa"]


async def test_approve_requires_matching_gate_id(client: AsyncClient) -> None:
    """REST approve rejects stale approval_gate_id but accepts omitted gate id (ADR-060).

    Args:
        client: Async HTTPX test client.
    """
    project_id = "proj-lifecycle-gate-id"
    state = await _setup_awaiting_approval(
        project_id=project_id,
        scan_id="scan-gate-id",
        operation_id="op-gate-id",
    )
    gate = state.approval_gate
    assert gate is not None

    omitted = await client.post(
        _operation_url(project_id, "/approve"),
        json={"approved_ids": ["t1-aaa"]},
    )
    assert omitted.status_code == 200

    state2 = await _setup_awaiting_approval(
        project_id="proj-lifecycle-gate-id-2",
        scan_id="scan-gate-id-2",
        operation_id="op-gate-id-2",
    )
    gate2 = state2.approval_gate
    assert gate2 is not None

    stale = await client.post(
        _operation_url("proj-lifecycle-gate-id-2", "/approve"),
        json={"approved_ids": ["t1-aaa"], "approval_gate_id": gate.gate_id},
    )
    assert stale.status_code == 409
    assert not gate2.future.done()


async def test_approve_rejects_second_submit_after_first_succeeds(client: AsyncClient) -> None:
    """Concurrent or repeated /approve calls cannot overwrite the first decision.

    Args:
        client: Async HTTPX test client.
    """
    project_id = "proj-lifecycle-double-approve"
    state = await _setup_awaiting_approval(
        project_id=project_id,
        scan_id="scan-double-approve",
        operation_id="op-double-approve",
    )
    gate = state.approval_gate
    assert gate is not None
    payload = {"approved_ids": ["t1-aaa"], "approval_gate_id": gate.gate_id}

    first = await client.post(_operation_url(project_id, "/approve"), json=payload)
    assert first.status_code == 200

    second = await client.post(
        _operation_url(project_id, "/approve"),
        json={"approved_ids": ["t1-bbb"], "approval_gate_id": gate.gate_id},
    )
    assert second.status_code == 409
    assert second.json()["detail"] == "Approval already submitted"
    assert gate.future.result() == ["t1-aaa"]


async def test_set_proposals_retires_pending_gate() -> None:
    """A superseding proposals event resolves the previous gate with []."""
    registry = get_operation_registry()
    state = await _setup_awaiting_approval(
        project_id="proj-gate-retire",
        scan_id="scan-gate-retire",
        operation_id="op-gate-retire",
    )
    gate1 = state.approval_gate
    assert gate1 is not None
    assert not gate1.future.done()

    await registry.set_proposals(
        state.operation_id,
        [Proposal(id="t1-ccc", rule_id="L003", file="c.yml")],
        prompt_generation=2,
    )

    assert gate1.future.done()
    assert gate1.future.result() == []
    assert state.approval_gate is not gate1
    assert state.approval_gate is not None
    assert not state.approval_gate.future.done()


async def test_approval_bridge_forwards_resolved_gate_after_supersede() -> None:
    """Bridge forwards a completed gate even when a newer gate replaced it."""
    from apme_gateway.api.operation_router import _forward_approval_gate_result
    from apme_gateway.scan.operator_queue import OperatorAnswerQueue

    registry = get_operation_registry()
    queue: OperatorAnswerQueue[list[str]] = OperatorAnswerQueue()
    state = await _setup_awaiting_approval(
        project_id="proj-gate-forward",
        scan_id="scan-gate-forward",
        operation_id="op-gate-forward",
        prompt_generation=1,
    )
    gate1 = state.approval_gate
    assert gate1 is not None

    # Bridge suspends on gate1 before it resolves (production interleaving).
    bridge_task = asyncio.create_task(_forward_approval_gate_result(state, queue))
    await asyncio.sleep(0)
    assert not bridge_task.done()

    # REST /approve resolves gate1; new proposals supersede before the
    # bridge loop is scheduled again (gate1 keeps its ids — already done).
    gate1.future.set_result(["t1-aaa"])
    await registry.set_proposals(
        state.operation_id,
        [Proposal(id="t1-bbb", rule_id="L002", file="b.yml")],
        prompt_generation=2,
    )
    assert state.approval_gate is not gate1

    # Drive the production bridge step (not a reimplementation of it).
    assert await bridge_task is True
    # The superseding gate survives; the driver wait receives gate1's answer.
    assert state.approval_gate is not gate1
    assert state.approval_gate is not None
    answer = await queue.next_answer(
        5.0,
        "Test wait",
        "defaulting",
        expected_generation=gate1.prompt_generation,
    )
    assert answer == ["t1-aaa"]


async def test_cancel_active_operation(client: AsyncClient) -> None:
    """Cancel tears down the grpc task and resolves pending futures.

    Args:
        client: Async HTTPX test client.
    """
    project_id = "proj-lifecycle-cancel"
    registry = get_operation_registry()
    state = registry.create(
        operation_id="op-lifecycle-cancel",
        project_id=project_id,
        scan_id="scan-lifecycle-cancel",
        scan_type="remediate",
    )
    registry.transition(state.operation_id, OperationStatus.SCANNING)
    state.grpc_task = asyncio.create_task(asyncio.sleep(60))
    loop = asyncio.get_running_loop()
    state.approval_gate = ApprovalGate(future=loop.create_future())

    resp = await client.post(_operation_url(project_id, "/cancel"))

    assert resp.status_code == 200
    assert resp.json() == {"status": "cancelled"}
    assert state.status == OperationStatus.CANCELLED
    assert state.grpc_task is not None
    with contextlib.suppress(asyncio.CancelledError):
        await state.grpc_task
    assert state.grpc_task.cancelled()
    assert state.approval_gate is not None
    assert state.approval_gate.future.done()
    assert state.approval_gate.future.result() == []


async def test_cancel_terminal_operation_conflicts(client: AsyncClient) -> None:
    """Cancel on a terminal operation returns 409.

    Args:
        client: Async HTTPX test client.
    """
    project_id = "proj-lifecycle-cancel-terminal"
    registry = get_operation_registry()
    state = registry.create(
        operation_id="op-lifecycle-cancel-terminal",
        project_id=project_id,
        scan_id="scan-lifecycle-cancel-terminal",
        scan_type="check",
    )
    registry.transition(state.operation_id, OperationStatus.COMPLETED)
    resp = await client.post(_operation_url(project_id, "/cancel"))
    assert resp.status_code == 409


async def test_cancel_missing_operation_not_found(client: AsyncClient) -> None:
    """Cancel with no operation returns 404.

    Args:
        client: Async HTTPX test client.
    """
    resp = await client.post(_operation_url("proj-lifecycle-missing", "/cancel"))
    assert resp.status_code == 404


async def test_events_terminal_snapshot_then_close(client: AsyncClient) -> None:
    """A terminal operation yields a snapshot and closes immediately.

    Args:
        client: Async HTTPX test client.
    """
    project_id = "proj-lifecycle-events-terminal"
    registry = get_operation_registry()
    state = registry.create(
        operation_id="op-lifecycle-events-terminal",
        project_id=project_id,
        scan_id="scan-lifecycle-events-terminal",
        scan_type="check",
    )
    registry.transition(state.operation_id, OperationStatus.COMPLETED)

    async with client.stream("GET", _operation_url(project_id, "/events")) as resp:
        assert resp.status_code == 200
        body = await resp.aread()

    text = body.decode()
    assert "event: snapshot" in text
    assert OperationStatus.COMPLETED.value in text


async def test_events_missing_operation_not_found(client: AsyncClient) -> None:
    """Event stream with no operation returns 404.

    Args:
        client: Async HTTPX test client.
    """
    async with client.stream("GET", _operation_url("proj-lifecycle-events-missing", "/events")) as resp:
        assert resp.status_code == 404


def _canned_request() -> Request:
    """Build a Request whose disconnect probe always reports connected.

    Returns:
        Starlette request that never reports a client disconnect.
    """

    async def _receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    scope: dict[str, object] = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "query_string": b"",
        "server": ("test", 80),
        "scheme": "http",
        "client": ("test", 50000),
    }
    return Request(scope, _receive)


def test_is_terminal_predicate() -> None:
    """Only pr_created and terminal status_changed close the live SSE stream."""
    assert not is_terminal({"event": "result", "data": {}})
    assert is_terminal({"event": "pr_created", "data": {}})
    assert not is_terminal({"event": "result", "data": None})
    assert is_terminal({"event": "status_changed", "data": {"status": "completed"}})
    assert is_terminal({"event": "status_changed", "data": {"status": "pr_submitted"}})
    assert not is_terminal({"event": "status_changed", "data": {"status": "scanning"}})
    assert not is_terminal({"event": "progress", "data": {}})
    assert not is_terminal({"event": "message", "data": {}})
    assert not is_terminal({"event": "status_changed", "data": None})
    assert not is_terminal({"event": "status_changed", "data": {"status": None}})
    assert not is_terminal({"event": "status_changed", "data": {"status": 123}})


def test_is_must_deliver_predicate() -> None:
    """Result/pr_created and terminal statuses are must-deliver for queue eviction."""
    assert is_must_deliver({"event": "result", "data": {}})
    assert is_must_deliver({"event": "pr_created", "data": {}})
    assert is_must_deliver({"event": "status_changed", "data": {"status": "completed"}})
    assert not is_must_deliver({"event": "progress", "data": {}})


async def test_events_result_then_completed_status_both_delivered() -> None:
    """Result alone does not close the stream; completed status_changed follows."""
    project_id = "proj-lifecycle-events-result-no-status"
    registry = get_operation_registry()
    state = registry.create(
        operation_id="op-lifecycle-events-result-no-status",
        project_id=project_id,
        scan_id="scan-lifecycle-events-result-no-status",
        scan_type="remediate",
    )
    registry.transition(state.operation_id, OperationStatus.APPLYING)

    resp = await operation_events(project_id, _canned_request())
    stream = resp.body_iterator
    first = await anext(stream)
    first_text = first if isinstance(first, str) else bytes(first).decode()
    assert "event: snapshot" in first_text

    state.sse_subscribers[0].put_nowait(
        {
            "event": "result",
            "data": {"total_violations": 2, "patches": [{"file": "a.yml", "diff": "--- fix"}]},
        }
    )
    state.sse_subscribers[0].put_nowait(
        {
            "event": "status_changed",
            "data": {"status": OperationStatus.COMPLETED.value, "previous": "applying"},
        }
    )
    rest: list[str] = []
    async with asyncio.timeout(10):
        async for chunk in stream:
            rest.append(chunk if isinstance(chunk, str) else bytes(chunk).decode())

    text = "".join(rest)
    assert "event: result" in text
    assert "a.yml" in text
    assert "event: status_changed" in text
    assert OperationStatus.COMPLETED.value in text
    assert text.index("event: result") < text.index("event: status_changed")


async def test_events_snapshot_drain_discards_stale_deltas() -> None:
    """Buffered pre-snapshot deltas are discarded; only the terminal is forwarded."""
    project_id = "proj-lifecycle-events-stale"
    registry = get_operation_registry()
    state = registry.create(
        operation_id="op-lifecycle-events-stale",
        project_id=project_id,
        scan_id="scan-lifecycle-events-stale",
        scan_type="remediate",
    )
    registry.add_progress(
        state.operation_id,
        ProgressEntry(phase="scanning", message="fresh-progress-marker", timestamp="2026-01-01T00:00:00+00:00"),
    )
    registry.transition(state.operation_id, OperationStatus.COMPLETED)

    resp = await operation_events(project_id, _canned_request())
    stream = resp.body_iterator
    first = await anext(stream)
    first_text = first if isinstance(first, str) else bytes(first).decode()
    assert "event: snapshot" in first_text
    assert "fresh-progress-marker" in first_text

    state.sse_subscribers[0].put_nowait(
        {
            "event": "progress",
            "data": {"phase": "scanning", "message": "stale-progress-marker"},
        }
    )
    state.sse_subscribers[0].put_nowait(
        {
            "event": "result",
            "data": {"total_violations": 1, "patches": [{"file": "b.yml", "diff": "--- trailing"}]},
        }
    )
    rest: list[str] = []
    async with asyncio.timeout(10):
        async for chunk in stream:
            rest.append(chunk if isinstance(chunk, str) else bytes(chunk).decode())

    text = "".join(rest)
    assert "stale-progress-marker" not in text
    assert "event: result" in text
    assert "trailing" in text


async def test_events_live_trailing_result_after_terminal_status() -> None:
    """A result queued behind a terminal status_changed is still delivered before close."""
    project_id = "proj-lifecycle-events-trailing"
    registry = get_operation_registry()
    state = registry.create(
        operation_id="op-lifecycle-events-trailing",
        project_id=project_id,
        scan_id="scan-lifecycle-events-trailing",
        scan_type="remediate",
    )
    registry.transition(state.operation_id, OperationStatus.SCANNING)

    resp = await operation_events(project_id, _canned_request())
    stream = resp.body_iterator
    first = await anext(stream)
    first_text = first if isinstance(first, str) else bytes(first).decode()
    assert "event: snapshot" in first_text

    state.sse_subscribers[0].put_nowait(
        {
            "event": "status_changed",
            "data": {"status": OperationStatus.COMPLETED.value, "previous": "scanning"},
        }
    )
    state.sse_subscribers[0].put_nowait(
        {
            "event": "result",
            "data": {"total_violations": 1, "patches": [{"file": "c.yml", "diff": "--- trailing-patch"}]},
        }
    )
    rest: list[str] = []
    async with asyncio.timeout(10):
        async for chunk in stream:
            rest.append(chunk if isinstance(chunk, str) else bytes(chunk).decode())

    text = "".join(rest)
    assert "status_changed" in text
    assert OperationStatus.COMPLETED.value in text
    assert "event: result" in text
    assert "trailing-patch" in text
    assert registry.get(state.operation_id) is not None
    assert registry.get(state.operation_id).sse_subscribers == []  # type: ignore[union-attr]


async def test_events_live_delta_then_terminal_close() -> None:
    """A live stream gets snapshot, delta, terminal close, and unsubscribes."""
    project_id = "proj-lifecycle-events-live"
    registry = get_operation_registry()
    state = registry.create(
        operation_id="op-lifecycle-events-live",
        project_id=project_id,
        scan_id="scan-lifecycle-events-live",
        scan_type="remediate",
    )
    registry.transition(state.operation_id, OperationStatus.SCANNING)

    resp = await operation_events(project_id, _canned_request())

    async def _collect() -> list[str]:
        out: list[str] = []
        async for chunk in resp.body_iterator:
            if isinstance(chunk, str):
                out.append(chunk)
            else:
                out.append(bytes(chunk).decode())
        return out

    collect_task = asyncio.create_task(_collect())
    try:
        async with asyncio.timeout(10):
            while not state.sse_subscribers:
                await asyncio.sleep(0.01)
        # Publish straight into the subscriber queue: flipping status first
        # would race the generator's terminal check right after the snapshot.
        state.sse_subscribers[0].put_nowait(
            {
                "event": "status_changed",
                "data": {"status": OperationStatus.CANCELLED.value},
            }
        )
        async with asyncio.timeout(10):
            chunks = await collect_task
    finally:
        if not collect_task.done():
            collect_task.cancel()

    text = "".join(chunks)
    assert "event: snapshot" in text
    assert "status_changed" in text
    assert OperationStatus.CANCELLED.value in text
    assert registry.get(state.operation_id) is not None
    assert registry.get(state.operation_id).sse_subscribers == []  # type: ignore[union-attr]


async def test_events_snapshot_drain_forwards_all_terminals_in_order() -> None:
    """Snapshot drain forwards queued result-then-status in order with patches intact."""
    project_id = "proj-lifecycle-events-snapshot-all-terminals"
    registry = get_operation_registry()
    state = registry.create(
        operation_id="op-lifecycle-events-snapshot-all-terminals",
        project_id=project_id,
        scan_id="scan-lifecycle-events-snapshot-all-terminals",
        scan_type="remediate",
    )
    registry.transition(state.operation_id, OperationStatus.COMPLETED)

    resp = await operation_events(project_id, _canned_request())
    stream = resp.body_iterator
    first = await anext(stream)
    first_text = first if isinstance(first, str) else bytes(first).decode()
    assert "event: snapshot" in first_text

    # Production order is RESULT-with-patches THEN bare status_changed;
    # last-wins would drop the patches.
    state.sse_subscribers[0].put_nowait(
        {
            "event": "progress",
            "data": {"phase": "scanning", "message": "stale-snapshot-marker"},
        }
    )
    state.sse_subscribers[0].put_nowait(
        {
            "event": "result",
            "data": {"total_violations": 1, "patches": [{"file": "d.yml", "diff": "--- keep-patches"}]},
        }
    )
    state.sse_subscribers[0].put_nowait(
        {
            "event": "status_changed",
            "data": {"status": OperationStatus.COMPLETED.value, "previous": "applying"},
        }
    )
    rest: list[str] = []
    async with asyncio.timeout(10):
        async for chunk in stream:
            rest.append(chunk if isinstance(chunk, str) else bytes(chunk).decode())

    text = "".join(rest)
    assert "stale-snapshot-marker" not in text
    assert "event: result" in text
    assert "keep-patches" in text
    assert "event: status_changed" in text
    assert OperationStatus.COMPLETED.value in text
    assert text.index("event: result") < text.index("event: status_changed")


async def test_broadcast_eviction_preserves_queued_terminal_result() -> None:
    """Must-deliver eviction drops oldest non-terminal first, preserving result."""
    project_id = "proj-lifecycle-events-evict-terminal"
    registry = get_operation_registry()
    state = registry.create(
        operation_id="op-lifecycle-events-evict-terminal",
        project_id=project_id,
        scan_id="scan-lifecycle-events-evict-terminal",
        scan_type="remediate",
    )
    registry.transition(state.operation_id, OperationStatus.SCANNING)
    queue = registry.subscribe(state.operation_id)
    assert queue is not None
    assert queue.empty()

    queue.put_nowait(
        {
            "event": "result",
            "data": {"total_violations": 1, "patches": [{"file": "e.yml", "diff": "--- keep-evicted"}]},
        }
    )
    for index in range(queue.maxsize - 1):
        queue.put_nowait(
            {
                "event": "progress",
                "data": {"phase": "scanning", "message": f"evict-progress-{index}"},
            }
        )
    assert queue.full()

    # Terminal bare status must make room by dropping a progress delta,
    # not the queued result carrying patches.
    registry.transition(state.operation_id, OperationStatus.COMPLETED)

    drained: list[dict[str, object]] = []
    with contextlib.suppress(asyncio.QueueEmpty):
        while True:
            drained.append(queue.get_nowait())

    assert len(drained) == queue.maxsize
    assert drained[0].get("event") == "result"
    results = [item for item in drained if item.get("event") == "result"]
    assert len(results) == 1
    result_data = results[0].get("data")
    assert isinstance(result_data, dict)
    patches = result_data.get("patches")
    assert isinstance(patches, list)
    assert any("keep-evicted" in str(patch) for patch in patches)

    def _is_completed(item: dict[str, object]) -> bool:
        """Return True for a completed status_changed event.

        Args:
            item: Drained queue message.

        Returns:
            True when the message is a completed status change.
        """
        if item.get("event") != "status_changed":
            return False
        data = item.get("data")
        return isinstance(data, dict) and data.get("status") == OperationStatus.COMPLETED.value

    statuses = [item for item in drained if _is_completed(item)]
    assert len(statuses) == 1
    flat = "".join(str(item) for item in drained)
    assert "evict-progress-0" not in flat
    assert "evict-progress-1" in flat
    assert queue in state.sse_subscribers


async def test_events_keepalive_on_queue_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queue timeouts yield keepalives; a terminal message closes the stream.

    Args:
        monkeypatch: Pytest fixture for patching asyncio.wait_for.
    """
    project_id = "proj-lifecycle-events-keepalive"
    registry = get_operation_registry()
    state = registry.create(
        operation_id="op-lifecycle-events-keepalive",
        project_id=project_id,
        scan_id="scan-lifecycle-events-keepalive",
        scan_type="remediate",
    )
    registry.transition(state.operation_id, OperationStatus.SCANNING)

    calls = 0

    async def _fake_wait_for(awaitable: object, timeout: float | None = None) -> object:
        nonlocal calls
        calls += 1
        if asyncio.iscoroutine(awaitable):
            awaitable.close()
        if calls <= 2:
            raise TimeoutError()
        return {
            "event": "status_changed",
            "data": {"status": OperationStatus.CANCELLED.value},
        }

    monkeypatch.setattr(asyncio, "wait_for", _fake_wait_for)
    resp = await operation_events(project_id, _canned_request())
    chunks: list[str] = []
    async for chunk in resp.body_iterator:
        if isinstance(chunk, str):
            chunks.append(chunk)
        else:
            chunks.append(bytes(chunk).decode())

    text = "".join(chunks)
    assert "event: snapshot" in text
    assert text.count(": keepalive") == 2
    assert "status_changed" in text
    assert calls == 3
    assert registry.get(state.operation_id) is not None
    assert registry.get(state.operation_id).sse_subscribers == []  # type: ignore[union-attr]
