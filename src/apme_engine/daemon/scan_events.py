"""In-process async broadcast for ScanCompletedEvent (ADR-020).

Subscribers (e.g. gateway via SubscribeScanEvents RPC) register an asyncio.Queue.
When a scan completes, the Scan handler publishes the event and all connected
subscribers receive it. If no subscribers are connected, events are silently
discarded — zero overhead.
"""

from __future__ import annotations

import asyncio
import logging

from apme.v1.primary_pb2 import ScanCompletedEvent  # type: ignore[attr-defined]

logger = logging.getLogger(__name__)

_subscribers: set[asyncio.Queue[ScanCompletedEvent]] = set()


def subscribe() -> asyncio.Queue[ScanCompletedEvent]:
    """Register a new subscriber queue.

    Returns:
        An asyncio.Queue that will receive ScanCompletedEvent messages.
    """
    q: asyncio.Queue[ScanCompletedEvent] = asyncio.Queue(maxsize=256)
    _subscribers.add(q)
    logger.info("Scan event subscriber added (total: %d)", len(_subscribers))
    return q


def unsubscribe(q: asyncio.Queue[ScanCompletedEvent]) -> None:
    """Remove a subscriber queue.

    Args:
        q: The queue previously returned by subscribe().
    """
    _subscribers.discard(q)
    logger.info("Scan event subscriber removed (total: %d)", len(_subscribers))


def publish(event: ScanCompletedEvent) -> None:
    """Broadcast a ScanCompletedEvent to all subscribers.

    Best-effort: if a subscriber's queue is full the event is dropped
    for that subscriber (back-pressure, not blocking the scan path).

    Args:
        event: The completed scan event to broadcast.
    """
    if not _subscribers:
        return

    for q in _subscribers:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(
                "Subscriber queue full — dropping event scan_id=%s",
                event.scan_id,
            )
