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


def test_set_findings_ignores_after_assess_pause() -> None:
    """FindingsReady must not regress AWAITING_APPROVAL back to ASSESSED.

    Returns:
        None.
    """
    import asyncio

    from apme_gateway.operation_registry import OperationRegistry

    async def _run() -> None:
        registry = OperationRegistry()
        op_id = "op-findings-no-regress"
        registry.create(
            operation_id=op_id,
            project_id="proj-2",
            scan_id="scan-2",
            scan_type="remediate",
        )
        registry.set_findings(op_id, [{"rule_id": "L001", "message": "assess"}])
        registry.transition(op_id, OperationStatus.AWAITING_APPROVAL)
        registry.set_findings(op_id, [{"rule_id": "L001", "message": "late-replay"}])
        op = registry.get(op_id)
        assert op is not None
        assert op.status == OperationStatus.AWAITING_APPROVAL
        assert op.findings is not None
        assert op.findings[0]["message"] == "assess"

    asyncio.run(_run())


def test_begin_remediate_idempotent_after_bridge_clears_future() -> None:
    """Retry after bridge clears the future must not raise session_expired.

    Returns:
        None.
    """
    import asyncio

    from apme_gateway.api.operation_router import begin_remediate
    from apme_gateway.operation_registry import get_operation_registry
    from apme_gateway.operation_types import OperationStatus

    async def _run() -> None:
        registry = get_operation_registry()
        await registry.shutdown()
        project_id = "proj-begin-idempotent"
        op_id = "op-begin-idempotent"
        registry.create(
            operation_id=op_id,
            project_id=project_id,
            scan_id="scan-1",
            scan_type="check",
        )
        registry.set_findings(op_id, [{"rule_id": "L001", "message": "a"}])
        first = await begin_remediate(project_id)
        assert first == {"status": "begin_remediate"}
        op = registry.get(op_id)
        assert op is not None
        assert op.scan_type == "remediate"
        # Mimic _approval_bridge clearing the future while still ASSESSED.
        op.begin_remediate_future = None
        assert op.status == OperationStatus.ASSESSED
        retry = await begin_remediate(project_id)
        assert retry == {"status": "begin_remediate"}

    asyncio.run(_run())
