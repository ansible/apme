"""Tests for the pluggable event emitter and GrpcReportingSink (ADR-020)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest

from apme.v1.reporting_pb2 import (
    FixCompletedEvent,
    ProposalOutcome,
    ReportAck,
)
from apme_engine.daemon import event_emitter
from apme_engine.daemon.sinks.grpc_reporting import GrpcReportingSink

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
def _clear_sinks() -> Iterator[None]:
    """Ensure sink list is empty before and after each test.

    Yields:
        None: Test runs between setup and teardown.
    """
    event_emitter._sinks.clear()
    yield
    event_emitter._sinks.clear()


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
