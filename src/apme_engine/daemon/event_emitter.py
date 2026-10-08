"""Pluggable event sink fan-out for fix events (ADR-020).

The engine emits events to all registered sinks.  Each sink is best-effort:
failures are logged and never block the fix path.  Sinks are loaded
from environment variables at startup.

Rule catalog registration includes a background retry: if the initial
``emit_register_rules`` push fails (Gateway not yet available), a task
retries with exponential back-off until the catalog is accepted.

Single-slot pending limitation: only the newest failed catalog is
retained (``_pending_catalog`` is one slot, newest wins). Rapid
successive emits collapse to a single retry of the latest catalog —
intermediate catalogs are never delivered. This is intentional (the
Gateway reconciles the full catalog on every registration, so only the
latest matters), but callers must not assume each emit is retried.
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
# Latest pending catalog for background retry (N16). Every failed emit
# overwrites this slot; the retry loop re-reads it each attempt so a newer
# catalog always wins over the stale request captured at task creation.
# Ordering uses a monotonic sequence: each emit takes the next
# ``_register_seq`` value and the slot records the sequence of the catalog
# it holds (``_pending_seq``). A failed emit stores its catalog only when
# its sequence is at least the stored one, and a success clears the slot
# only when the stored sequence is at most the succeeded one — so a slow
# older emit can never overwrite or discard a newer catalog. Assumes emit
# order equals call order on the single event-loop thread.
_pending_catalog: reporting_pb2.RegisterRulesRequest | None = None
_pending_seq: int = 0
_register_seq: int = 0
# Newest successfully delivered sequence (#6). A slow older failure must
# not overwrite the slot after a newer direct success.
_last_success_seq: int = 0


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


async def _rule_registration_retry_loop() -> None:
    """Background retry loop for rule catalog registration.

    Uses exponential back-off starting at ``_RULE_RETRY_INITIAL_DELAY_S``
    and capping at ``_RULE_RETRY_MAX_DELAY_S``. Re-reads
    ``_pending_catalog`` each attempt so a newer emit always supersedes
    the stale request captured when the task was created (N16). The loop
    only exits when the catalog is registered **and** no newer catalog
    is pending — a previously registered flag must not discard a fresh
    ``_pending_catalog`` (the early-exit bug).
    """
    global _rule_catalog_registered, _rule_retry_task, _pending_catalog, _pending_seq, _last_success_seq  # noqa: PLW0603

    current_task = asyncio.current_task()
    delay = _RULE_RETRY_INITIAL_DELAY_S
    try:
        while True:
            if _rule_catalog_registered and _pending_catalog is None:
                return
            # A failed emit resets the flag (see emit_register_rules), so
            # reaching here with a pending catalog always means work remains.
            await asyncio.sleep(delay)
            if not _sinks:
                logger.debug("No sinks available for rule registration retry")
                delay = min(delay * _RULE_RETRY_BACKOFF_FACTOR, _RULE_RETRY_MAX_DELAY_S)
                continue
            request = _pending_catalog
            attempt_seq = _pending_seq
            if request is None:
                if _rule_catalog_registered:
                    return
                logger.debug("No pending rule catalog for retry; waiting")
                delay = min(delay * _RULE_RETRY_BACKOFF_FACTOR, _RULE_RETRY_MAX_DELAY_S)
                continue
            if await _attempt_register_rules(request):
                _rule_catalog_registered = True
                _last_success_seq = max(_last_success_seq, attempt_seq)
                # Sequence check: a newer emit may have replaced the pending
                # slot while this attempt was in flight — only clear when the
                # stored sequence is at most this attempt's, so a newer
                # catalog is never dropped by an older success.
                if _pending_seq <= attempt_seq:
                    _pending_catalog = None
                if _pending_catalog is not None:
                    # A newer catalog arrived mid-attempt; keep looping so
                    # the fresh pending catalog is pushed instead of
                    # returning early on the stale success.
                    logger.info("Rule catalog registration succeeded; newer catalog pending — continuing retry")
                    delay = _RULE_RETRY_INITIAL_DELAY_S
                    continue
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
    catalog is eventually pushed once the Gateway becomes available. The
    latest failed request is stored in ``_pending_catalog``; the retry
    loop re-reads it each attempt so newer emits supersede stale ones
    without spawning duplicate tasks (N16). Only the newest catalog is
    retained — intermediate catalogs from rapid emits are collapsed and
    never delivered (single-slot pending, newest wins).

    A failed emit resets ``_rule_catalog_registered`` to ``False`` so a
    catalog that was registered before but has since changed is retried
    instead of being dropped by the loop's registered early-exit.

    Args:
        request: Registration payload.
    """
    global _rule_retry_task, _rule_catalog_registered, _pending_catalog, _pending_seq, _register_seq, _last_success_seq  # noqa: PLW0603

    _register_seq += 1
    my_seq = _register_seq
    if _sinks and await _attempt_register_rules(request):
        _rule_catalog_registered = True
        _last_success_seq = max(_last_success_seq, my_seq)
        # Sequence check (same staleness guard as the retry loop): only
        # clear the slot when the stored sequence is at most this emit's.
        # A newer failed emit may have overwritten it while this attempt
        # was in flight.
        if _pending_seq <= my_seq:
            _pending_catalog = None
        if _pending_catalog is None:
            if _rule_retry_task is not None and not _rule_retry_task.done():
                _rule_retry_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await _rule_retry_task
                _rule_retry_task = None
            return
        # A newer catalog is pending — leave it for the retry loop and
        # make sure the loop is running to deliver it.
        if _rule_retry_task is None or _rule_retry_task.done():
            _rule_retry_task = asyncio.create_task(
                _rule_registration_retry_loop(),
                name="rule-catalog-retry",
            )
        return

    if _sinks:
        logger.warning("No sink accepted rule registration; scheduling background retry")
    else:
        logger.info("No sinks configured; skipping rule registration (no Gateway)")
        return

    # Newest catalog always wins; a running retry loop picks it up on its
    # next attempt (cancel-and-replace would churn tasks on rapid emits).
    # Reset the registered flag: the catalog changed since the last success
    # and must be pushed again — otherwise the retry loop's early-exit on
    # ``_rule_catalog_registered`` would drop the fresh pending catalog.
    # Sequence-guarded: a slow older emit must not overwrite a newer
    # catalog already in the slot (nor reset the registered flag after a
    # newer success).
    if my_seq >= _pending_seq and my_seq > _last_success_seq:
        _rule_catalog_registered = False
        _pending_catalog = request
        _pending_seq = my_seq
    if _rule_retry_task is None or _rule_retry_task.done():
        _rule_retry_task = asyncio.create_task(
            _rule_registration_retry_loop(),
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
    global _rule_retry_task, _rule_catalog_registered, _pending_catalog, _pending_seq, _register_seq, _last_success_seq  # noqa: PLW0603

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
    _pending_catalog = None
    _pending_seq = 0
    _register_seq = 0
    _last_success_seq = 0
