"""Pluggable event sink fan-out for fix events (ADR-020).

The engine emits events to all registered sinks.  Each sink is best-effort:
failures are logged and never block the fix path.  Sinks are loaded
from environment variables at startup.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from apme.v1 import reporting_pb2

logger = logging.getLogger("apme.events")


class EventSink(Protocol):
    """Interface for fix event destinations."""

    async def start(self) -> None:
        """Initialize the sink (open connections, start background tasks)."""
        ...

    async def stop(self) -> None:
        """Shut down the sink (close connections, cancel tasks)."""
        ...

    async def on_fix_completed(self, event: reporting_pb2.FixCompletedEvent) -> None:
        """Deliver a fix-completed event.

        Args:
            event: Completed fix event to deliver.
        """
        ...

    async def register_rules(
        self, request: reporting_pb2.RegisterRulesRequest
    ) -> reporting_pb2.RegisterRulesResponse | None:
        """Push rule catalog to the reporting service (ADR-041).

        Args:
            request: Registration payload with the full rule set.

        Returns:
            Response from the reporting service, or None on failure.
        """
        ...


_sinks: list[EventSink] = []


async def _emit_fix_to_sink(
    sink: EventSink,
    event: reporting_pb2.FixCompletedEvent,
) -> None:
    try:
        await sink.on_fix_completed(event)
    except Exception:
        logger.warning("Sink %s failed for scan_id=%s", type(sink).__name__, event.scan_id, exc_info=True)


async def emit_fix_completed(event: reporting_pb2.FixCompletedEvent) -> None:
    """Fan-out FixCompletedEvent to all registered sinks concurrently.

    Args:
        event: Completed fix event to broadcast.
    """
    if not _sinks:
        return
    await asyncio.gather(
        *(_emit_fix_to_sink(sink, event) for sink in list(_sinks)),
        return_exceptions=True,
    )


async def emit_register_rules(request: reporting_pb2.RegisterRulesRequest) -> None:
    """Push rule catalog to the first available sink (ADR-041).

    Unlike fix events (fan-out to all sinks), registration targets a single
    Gateway.  We try each sink in order and stop on the first success.

    Args:
        request: Registration payload.
    """
    for sink in list(_sinks):
        try:
            resp = await sink.register_rules(request)
            if resp is not None:
                logger.info(
                    "Rule catalog registered: added=%d removed=%d unchanged=%d",
                    resp.rules_added,
                    resp.rules_removed,
                    resp.rules_unchanged,
                )
                return
        except Exception:
            logger.warning("Sink %s failed to register rules", type(sink).__name__, exc_info=True)
    if _sinks:
        logger.warning("No sink accepted rule registration")


async def start_sinks() -> None:
    """Load sinks from env vars and start them.  Call once at server startup."""
    import os

    endpoint = os.environ.get("APME_REPORTING_ENDPOINT", "").strip()
    if endpoint:
        from apme_engine.daemon.sinks.grpc_reporting import GrpcReportingSink

        sink = GrpcReportingSink(endpoint)
        _sinks.append(sink)
        await sink.start()

    if _sinks:
        logger.info("Event sinks active: %s", [type(s).__name__ for s in _sinks])


async def stop_sinks() -> None:
    """Stop all registered sinks.  Call at server shutdown."""
    for sink in _sinks:
        try:
            await sink.stop()
        except Exception:
            logger.warning("Failed to stop sink %s", type(sink).__name__)
    _sinks.clear()
