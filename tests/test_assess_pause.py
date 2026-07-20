"""ADR-064 assess-pause helpers and Gateway status mapping."""

from __future__ import annotations

from apme_gateway.operation_types import OperationStatus, SSEEventType


def test_assessed_status_is_non_terminal() -> None:
    """ASSESSED must not be treated as a terminal operation status.

    Returns:
        None.
    """
    from apme_gateway.operation_types import TERMINAL_STATUSES

    assert OperationStatus.ASSESSED not in TERMINAL_STATUSES
    assert OperationStatus.ASSESSED.value == "assessed"


def test_findings_sse_event_type() -> None:
    """FINDINGS event type is registered for SSE clients.

    Returns:
        None.
    """
    assert SSEEventType.FINDINGS.value == "findings"


def test_fix_options_assess_pause_default_false() -> None:
    """Proto FixOptions.assess_pause defaults to false (non-breaking).

    Returns:
        None.
    """
    from apme.v1 import primary_pb2

    assert primary_pb2.FixOptions().assess_pause is False


def test_set_findings_reuses_begin_remediate_future() -> None:
    """Replayed FindingsReady must not replace the bridge's awaitable future.

    Returns:
        None.
    """
    import asyncio

    from apme_gateway.operation_registry import OperationRegistry

    async def _run() -> None:
        registry = OperationRegistry()
        op_id = "op-assess-replay"
        registry.create(
            operation_id=op_id,
            project_id="proj-1",
            scan_id="scan-1",
            scan_type="check",
        )
        registry.set_findings(op_id, [{"rule_id": "L001", "message": "a"}])
        first = registry.get(op_id)
        assert first is not None
        fut = first.begin_remediate_future
        assert fut is not None
        assert not fut.done()

        registry.set_findings(op_id, [{"rule_id": "L001", "message": "b"}])
        second = registry.get(op_id)
        assert second is not None
        assert second.begin_remediate_future is fut
        assert second.findings is not None
        assert second.findings[0]["message"] == "b"

        registry.transition(op_id, OperationStatus.CANCELLED)
        registry.set_findings(op_id, [{"rule_id": "L001", "message": "late"}])
        terminal = registry.get(op_id)
        assert terminal is not None
        assert terminal.status == OperationStatus.CANCELLED
        assert terminal.findings is not None
        assert terminal.findings[0]["message"] == "b"

    asyncio.run(_run())
