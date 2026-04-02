"""Pluggable event sink fan-out for fix events (ADR-020).

The engine emits events to all registered sinks.  Each sink is best-effort:
failures are logged and never block the fix path.  Sinks are loaded
from environment variables at startup.

Rule catalog registration includes a background retry: if the initial
``emit_register_rules`` push fails (Gateway not yet available), a task
retries with exponential back-off until the catalog is accepted.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Protocol

from apme.v1 import reporting_pb2

logger = logging.getLogger("apme.events")

_RULE_RETRY_INITIAL_DELAY_S = 10.0
_RULE_RETRY_MAX_DELAY_S = 300.0
_RULE_RETRY_BACKOFF_FACTOR = 2.0


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
_rule_retry_task: asyncio.Task[None] | None = None
_rule_catalog_registered: bool = False


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


async def _attempt_register_rules(
    request: reporting_pb2.RegisterRulesRequest,
) -> bool:
    """Try each sink in order; return True on first success.

    Args:
        request: Registration payload.

    Returns:
        True if any sink accepted, False otherwise.
    """
    for sink in list(_sinks):
        try:
            resp = await sink.register_rules(request)
            if resp is not None:
                if not resp.accepted:
                    logger.warning(
                        "Sink %s rejected rule catalog: %s",
                        type(sink).__name__,
                        resp.message,
                    )
                    continue
                logger.info(
                    "Rule catalog registered: added=%d removed=%d unchanged=%d",
                    resp.rules_added,
                    resp.rules_removed,
                    resp.rules_unchanged,
                )
                return True
        except Exception:
            logger.warning("Sink %s failed to register rules", type(sink).__name__, exc_info=True)
    return False


async def _rule_registration_retry_loop(
    request: reporting_pb2.RegisterRulesRequest,
) -> None:
    """Background retry loop for rule catalog registration.

    Uses exponential back-off starting at ``_RULE_RETRY_INITIAL_DELAY_S``
    and capping at ``_RULE_RETRY_MAX_DELAY_S``.

    Args:
        request: Registration payload to retry.
    """
    global _rule_catalog_registered, _rule_retry_task  # noqa: PLW0603

    current_task = asyncio.current_task()
    delay = _RULE_RETRY_INITIAL_DELAY_S
    try:
        while not _rule_catalog_registered:
            await asyncio.sleep(delay)
            if not _sinks:
                logger.debug("No sinks available for rule registration retry")
                delay = min(delay * _RULE_RETRY_BACKOFF_FACTOR, _RULE_RETRY_MAX_DELAY_S)
                continue
            if await _attempt_register_rules(request):
                _rule_catalog_registered = True
                logger.info("Rule catalog registration succeeded on retry")
                return
            logger.info(
                "Rule registration retry failed, next attempt in %.0fs",
                min(delay * _RULE_RETRY_BACKOFF_FACTOR, _RULE_RETRY_MAX_DELAY_S),
            )
            delay = min(delay * _RULE_RETRY_BACKOFF_FACTOR, _RULE_RETRY_MAX_DELAY_S)
    finally:
        if _rule_retry_task is current_task:
            _rule_retry_task = None


async def emit_register_rules(request: reporting_pb2.RegisterRulesRequest) -> None:
    """Push rule catalog to the first available sink (ADR-041).

    Unlike fix events (fan-out to all sinks), registration targets a single
    Gateway.  We try each sink in order and stop on the first success.

    If registration fails, a background retry task is launched so the
    catalog is eventually pushed once the Gateway becomes available.

    Args:
        request: Registration payload.
    """
    global _rule_retry_task, _rule_catalog_registered  # noqa: PLW0603

    if _sinks and await _attempt_register_rules(request):
        _rule_catalog_registered = True
        if _rule_retry_task is not None and not _rule_retry_task.done():
            _rule_retry_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await _rule_retry_task
            _rule_retry_task = None
        return

    if _sinks:
        logger.warning("No sink accepted rule registration; scheduling background retry")
    else:
        logger.info("No sinks configured; skipping rule registration (no Gateway)")
        return

    if _rule_retry_task is None or _rule_retry_task.done():
        _rule_retry_task = asyncio.create_task(
            _rule_registration_retry_loop(request),
            name="rule-catalog-retry",
        )


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
    """Stop all registered sinks and cancel any pending retry task."""
    global _rule_retry_task, _rule_catalog_registered  # noqa: PLW0603

    if _rule_retry_task is not None:
        _rule_retry_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _rule_retry_task
        _rule_retry_task = None

    for sink in _sinks:
        try:
            await sink.stop()
        except Exception:
            logger.warning("Failed to stop sink %s", type(sink).__name__)
    _sinks.clear()
    _rule_catalog_registered = False
