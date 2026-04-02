"""Tests for the pluggable event emitter and GrpcReportingSink (ADR-020)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest

from apme.v1.reporting_pb2 import (
    FixCompletedEvent,
    ProposalOutcome,
    RegisterRulesRequest,
    RegisterRulesResponse,
    ReportAck,
    RuleDefinition,
)
from apme_engine.daemon import event_emitter
from apme_engine.daemon.sinks.grpc_reporting import GrpcReportingSink

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rules_request() -> RegisterRulesRequest:
    """Build a minimal RegisterRulesRequest for testing.

    Returns:
        RegisterRulesRequest with one dummy rule.
    """
    return RegisterRulesRequest(
        pod_id="test-pod",
        is_authority=True,
        rules=[RuleDefinition(rule_id="L001", source="native", description="test rule")],
    )


def _fix_event(**overrides: str) -> FixCompletedEvent:
    """Build a FixCompletedEvent with sensible defaults.

    Args:
        **overrides: Field values to override.

    Returns:
        FixCompletedEvent: Event with defaults merged with overrides.
    """
    return FixCompletedEvent(
        scan_id=overrides.get("scan_id", "test-scan-001"),
        session_id=overrides.get("session_id", "abcdef123456"),
        project_path=overrides.get("project_path", "/tmp/project"),
        source=overrides.get("source", "cli"),
    )


# ---------------------------------------------------------------------------
# EventSink fan-out
# ---------------------------------------------------------------------------


class FakeSink:
    """In-memory sink that records calls."""

    def __init__(self) -> None:
        """Initialize empty event lists."""
        self.fix_events: list[FixCompletedEvent] = []
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        """Mark started."""
        self.started = True

    async def stop(self) -> None:
        """Mark stopped."""
        self.stopped = True

    async def on_fix_completed(self, event: FixCompletedEvent) -> None:
        """Record fix event.

        Args:
            event: Fix event to record.
        """
        self.fix_events.append(event)

    async def register_rules(self, request: object) -> None:
        """No-op rule registration.

        Args:
            request: Registration payload (unused).

        Returns:
            None.
        """
        return None


class AcceptingSink:
    """Sink that accepts rule registration with a configurable response."""

    def __init__(self, response: RegisterRulesResponse | None = None) -> None:
        """Initialize with optional response.

        Args:
            response: Response to return from register_rules.
        """
        self.response = response or RegisterRulesResponse(
            accepted=True,
            rules_added=5,
            rules_removed=0,
            rules_unchanged=0,
        )
        self.register_calls: list[RegisterRulesRequest] = []

    async def start(self) -> None:
        """No-op start."""

    async def stop(self) -> None:
        """No-op stop."""

    async def on_fix_completed(self, event: FixCompletedEvent) -> None:
        """No-op fix event.

        Args:
            event: Fix event (unused).
        """

    async def register_rules(
        self,
        request: RegisterRulesRequest,
    ) -> RegisterRulesResponse:
        """Accept rule registration.

        Args:
            request: Registration payload to record.

        Returns:
            Pre-configured response.
        """
        self.register_calls.append(request)
        return self.response


class FailingSink:
    """Sink that always raises on emission."""

    async def start(self) -> None:
        """No-op start."""

    async def stop(self) -> None:
        """No-op stop."""

    async def on_fix_completed(self, event: FixCompletedEvent) -> None:
        """Raise on fix event.

        Args:
            event: Fix event (unused, raises immediately).

        Raises:
            RuntimeError: Always raised.
        """
        raise RuntimeError("boom")

    async def register_rules(self, request: object) -> None:
        """Raise on rule registration.

        Args:
            request: Registration payload (unused, raises immediately).

        Raises:
            RuntimeError: Always raised.
        """
        raise RuntimeError("boom")


@pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
async def _clear_sinks() -> AsyncIterator[None]:
    """Ensure sink list and retry state are reset before and after each test.

    Yields:
        None: Test runs between setup and teardown.
    """
    event_emitter._sinks.clear()
    event_emitter._rule_catalog_registered = False
    if event_emitter._rule_retry_task is not None:
        event_emitter._rule_retry_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await event_emitter._rule_retry_task
    event_emitter._rule_retry_task = None
    yield
    event_emitter._sinks.clear()
    event_emitter._rule_catalog_registered = False
    if event_emitter._rule_retry_task is not None:
        event_emitter._rule_retry_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await event_emitter._rule_retry_task
    event_emitter._rule_retry_task = None


async def test_emit_fix_completed_fans_out() -> None:
    """Verify fix event reaches a registered sink."""
    sink = FakeSink()
    event_emitter._sinks.append(sink)

    ev = _fix_event()
    await event_emitter.emit_fix_completed(ev)

    assert len(sink.fix_events) == 1
    assert sink.fix_events[0].scan_id == "test-scan-001"


async def test_emit_fix_completed_no_sinks() -> None:
    """Emitting with no sinks is a no-op."""
    await event_emitter.emit_fix_completed(_fix_event())


async def test_sink_failure_does_not_propagate() -> None:
    """A failing sink must not break the fan-out or raise."""
    good = FakeSink()
    bad = FailingSink()
    event_emitter._sinks.extend([bad, good])

    await event_emitter.emit_fix_completed(_fix_event())
    assert len(good.fix_events) == 1


async def test_multiple_sinks_receive_same_event() -> None:
    """All registered sinks receive the same event concurrently."""
    sinks = [FakeSink(), FakeSink()]
    event_emitter._sinks.extend(sinks)

    await event_emitter.emit_fix_completed(_fix_event())
    for s in sinks:
        assert len(s.fix_events) == 1


async def test_start_sinks_loads_grpc_when_env_set() -> None:
    """GrpcReportingSink is created and started when env var is set."""
    with (
        patch.dict("os.environ", {"APME_REPORTING_ENDPOINT": "localhost:50060"}),
        patch("apme_engine.daemon.sinks.grpc_reporting.GrpcReportingSink") as mock_cls,
    ):
        mock_instance = AsyncMock()
        mock_cls.return_value = mock_instance

        await event_emitter.start_sinks()

        mock_cls.assert_called_once_with("localhost:50060")
        mock_instance.start.assert_awaited_once()
        assert len(event_emitter._sinks) == 1


async def test_start_sinks_skips_when_env_unset() -> None:
    """No sinks are loaded when APME_REPORTING_ENDPOINT is unset."""
    with patch.dict("os.environ", {}, clear=True):
        await event_emitter.start_sinks()
        assert len(event_emitter._sinks) == 0


async def test_stop_sinks_clears_list() -> None:
    """Stopping sinks clears the registry and calls stop on each."""
    sink = FakeSink()
    event_emitter._sinks.append(sink)
    await event_emitter.stop_sinks()

    assert len(event_emitter._sinks) == 0
    assert sink.stopped


# ---------------------------------------------------------------------------
# GrpcReportingSink
# ---------------------------------------------------------------------------


async def test_grpc_sink_uses_fast_fail_when_unavailable() -> None:
    """Delivery uses a short fast-fail timeout when endpoint is known-down."""
    from apme_engine.daemon.sinks.grpc_reporting import _FAST_FAIL_TIMEOUT_S

    sink = GrpcReportingSink("localhost:99999")
    sink._available = False

    mock_stub = AsyncMock()
    mock_stub.ReportFixCompleted.return_value = ReportAck()
    sink._stub = mock_stub

    await sink.on_fix_completed(_fix_event())
    mock_stub.ReportFixCompleted.assert_awaited_once()
    assert mock_stub.ReportFixCompleted.call_args.kwargs.get("timeout") == _FAST_FAIL_TIMEOUT_S
    assert sink._available is True


async def test_grpc_sink_skips_when_stub_is_none() -> None:
    """Events are silently dropped when stub has not been initialized."""
    sink = GrpcReportingSink("localhost:99999")
    sink._stub = None

    await sink.on_fix_completed(_fix_event())


async def test_grpc_sink_sends_when_available() -> None:
    """Events are sent with the full timeout when endpoint is available."""
    from apme_engine.daemon.sinks.grpc_reporting import _TIMEOUT_S

    sink = GrpcReportingSink("localhost:50060")
    sink._available = True

    mock_stub = AsyncMock()
    mock_stub.ReportFixCompleted.return_value = ReportAck()
    sink._stub = mock_stub

    await sink.on_fix_completed(_fix_event())
    mock_stub.ReportFixCompleted.assert_awaited_once()
    assert mock_stub.ReportFixCompleted.call_args.kwargs.get("timeout") == _TIMEOUT_S


async def test_grpc_sink_flips_unavailable_on_send_failure() -> None:
    """A failed send should flip _available to False."""
    sink = GrpcReportingSink("localhost:50060")
    sink._available = True

    mock_stub = AsyncMock()
    mock_stub.ReportFixCompleted.side_effect = Exception("connection refused")
    sink._stub = mock_stub

    await sink.on_fix_completed(_fix_event())
    assert sink._available is False


async def test_grpc_sink_stop_cancels_health_task() -> None:
    """Stopping the sink cancels the background health-check task."""
    sink = GrpcReportingSink("localhost:50060")
    sink._health_task = asyncio.create_task(asyncio.sleep(3600))
    sink._channel = AsyncMock()

    await sink.stop()
    assert sink._health_task.cancelled()


async def test_grpc_sink_start_sets_channel_message_limits() -> None:
    """Channel is created with 50 MiB send/receive limits."""
    from apme_engine.daemon.sinks.grpc_reporting import _GRPC_MAX_MSG

    sink = GrpcReportingSink("localhost:99999")

    with patch("apme_engine.daemon.sinks.grpc_reporting.grpc.aio") as mock_aio:
        mock_channel = AsyncMock()
        mock_aio.insecure_channel.return_value = mock_channel

        with patch.object(sink, "_probe", new_callable=AsyncMock):
            await sink.start()

        mock_aio.insecure_channel.assert_called_once_with(
            "localhost:99999",
            options=[
                ("grpc.max_send_message_length", _GRPC_MAX_MSG),
                ("grpc.max_receive_message_length", _GRPC_MAX_MSG),
            ],
        )

    if sink._health_task:
        sink._health_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sink._health_task


# ---------------------------------------------------------------------------
# Rule catalog registration & retry
# ---------------------------------------------------------------------------


async def test_emit_register_rules_immediate_success() -> None:
    """Registration succeeds on first attempt and sets the registered flag."""
    sink = AcceptingSink()
    event_emitter._sinks.append(sink)

    await event_emitter.emit_register_rules(_rules_request())

    assert event_emitter._rule_catalog_registered is True
    assert len(sink.register_calls) == 1
    assert event_emitter._rule_retry_task is None


async def test_emit_register_rules_no_sinks_skips() -> None:
    """With no sinks, registration is skipped and no retry is scheduled."""
    await event_emitter.emit_register_rules(_rules_request())

    assert event_emitter._rule_catalog_registered is False
    assert event_emitter._rule_retry_task is None


async def test_emit_register_rules_failure_schedules_retry() -> None:
    """When all sinks fail, a background retry task is launched."""
    event_emitter._sinks.append(FailingSink())

    await event_emitter.emit_register_rules(_rules_request())

    assert event_emitter._rule_catalog_registered is False
    assert event_emitter._rule_retry_task is not None
    assert not event_emitter._rule_retry_task.done()

    event_emitter._rule_retry_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await event_emitter._rule_retry_task


async def test_retry_loop_succeeds_on_second_attempt() -> None:
    """Retry loop calls sinks again and stops when registration succeeds."""
    call_count = 0

    class EventuallySink:
        """Sink that fails once then succeeds."""

        async def start(self) -> None:
            """No-op start."""

        async def stop(self) -> None:
            """No-op stop."""

        async def on_fix_completed(self, event: FixCompletedEvent) -> None:
            """No-op.

            Args:
                event: Unused.
            """

        async def register_rules(
            self,
            request: RegisterRulesRequest,
        ) -> RegisterRulesResponse | None:
            """Fail first, succeed second.

            Args:
                request: Registration payload.

            Returns:
                None on first call, response on second.
            """
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                return None
            return RegisterRulesResponse(
                accepted=True,
                rules_added=1,
                rules_removed=0,
                rules_unchanged=0,
            )

    event_emitter._sinks.append(EventuallySink())

    with patch.object(event_emitter, "_RULE_RETRY_INITIAL_DELAY_S", 0.01):
        await event_emitter.emit_register_rules(_rules_request())

        assert event_emitter._rule_retry_task is not None
        await asyncio.wait_for(event_emitter._rule_retry_task, timeout=2.0)

    assert event_emitter._rule_catalog_registered is True
    assert call_count == 2


async def test_stop_sinks_cancels_retry_task() -> None:
    """Stopping sinks cancels the background retry task and resets state."""
    event_emitter._sinks.append(FailingSink())
    await event_emitter.emit_register_rules(_rules_request())

    assert event_emitter._rule_retry_task is not None

    await event_emitter.stop_sinks()

    assert event_emitter._rule_retry_task is None
    assert event_emitter._rule_catalog_registered is False
    assert len(event_emitter._sinks) == 0


async def test_retry_not_duplicated_on_second_emit() -> None:
    """Calling emit_register_rules twice doesn't create duplicate retry tasks."""
    event_emitter._sinks.append(FailingSink())

    await event_emitter.emit_register_rules(_rules_request())
    first_task = event_emitter._rule_retry_task

    await event_emitter.emit_register_rules(_rules_request())
    second_task = event_emitter._rule_retry_task

    assert first_task is second_task

    assert first_task is not None
    first_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await first_task


async def test_emit_register_rules_accepted_false_schedules_retry() -> None:
    """A response with accepted=False is treated as failure and triggers retry."""

    class RejectingSink:
        """Sink that returns accepted=False."""

        async def start(self) -> None:
            """No-op start."""

        async def stop(self) -> None:
            """No-op stop."""

        async def on_fix_completed(self, event: FixCompletedEvent) -> None:
            """No-op.

            Args:
                event: Unused.
            """

        async def register_rules(
            self,
            request: RegisterRulesRequest,
        ) -> RegisterRulesResponse:
            """Return a rejection response.

            Args:
                request: Registration payload.

            Returns:
                Response with accepted=False.
            """
            return RegisterRulesResponse(accepted=False, message="not authority")

    event_emitter._sinks.append(RejectingSink())

    await event_emitter.emit_register_rules(_rules_request())

    assert event_emitter._rule_catalog_registered is False
    assert event_emitter._rule_retry_task is not None
    assert not event_emitter._rule_retry_task.done()

    event_emitter._rule_retry_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await event_emitter._rule_retry_task


# ---------------------------------------------------------------------------
# ProposalOutcome construction
# ---------------------------------------------------------------------------


def test_fix_completed_event_with_proposals() -> None:
    """Verify FixCompletedEvent carries ProposalOutcome entries."""
    outcomes = [
        ProposalOutcome(proposal_id="t2-0001", status="approved", rule_id="L001"),
        ProposalOutcome(proposal_id="t2-0002", status="rejected", rule_id="L002"),
    ]
    ev = FixCompletedEvent(
        scan_id="test-scan-001",
        session_id="abcdef123456",
        project_path="/tmp/project",
        source="cli",
        proposals=outcomes,
    )
    assert len(ev.proposals) == 2
    assert ev.proposals[0].status == "approved"
    assert ev.proposals[1].status == "rejected"
