"""Notification generator and SSE broadcast hub.

When the Gateway persists a ``FixCompletedEvent`` it calls
``generate_notifications`` to create user-facing notification rows.
The caller commits the transaction and then calls
``broadcast_notifications`` to fan out payloads to connected SSE clients.

The SSE hub uses an in-memory fan-out pattern: each connected browser
gets its own ``asyncio.Queue`` and the hub pushes every new notification
to all queues.  Disconnected clients are cleaned up automatically.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from apme_gateway.db.models import Notification, Project, Scan, Violation
from apme_gateway.db.queries import insert_notification

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SSE broadcast hub
# ---------------------------------------------------------------------------

_subscribers: list[asyncio.Queue[dict[str, Any]]] = []


def _broadcast(payload: dict[str, Any]) -> None:
    """Push a notification payload to every connected SSE client.

    When a subscriber's queue is full (slow consumer), the oldest item is
    dropped so the client stays connected and receives newer events rather
    than becoming permanently stuck on stale keep-alives.

    Args:
        payload: JSON-serialisable notification dict.
    """
    for q in tuple(_subscribers):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                q.get_nowait()
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                logger.warning("Dropping notification for persistently full subscriber queue")


def subscribe() -> asyncio.Queue[dict[str, Any]]:
    """Register a new SSE client and return its queue.

    Returns:
        An asyncio.Queue that will receive notification payloads.
    """
    q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
    _subscribers.append(q)
    return q


def unsubscribe(q: asyncio.Queue[dict[str, Any]]) -> None:
    """Remove an SSE client queue.

    Args:
        q: The queue previously returned by ``subscribe()``.
    """
    with contextlib.suppress(ValueError):
        _subscribers.remove(q)


async def sse_event_stream(q: asyncio.Queue[dict[str, Any]]) -> AsyncGenerator[str, None]:
    """Yield SSE-formatted events from a subscriber queue.

    This is an async generator consumed by FastAPI's ``StreamingResponse``.

    Args:
        q: Subscriber queue from ``subscribe()``.

    Yields:
        str: SSE ``data:`` lines terminated by double newlines.
    """
    try:
        while True:
            payload = await q.get()
            yield f"data: {json.dumps(payload)}\n\n"
    except asyncio.CancelledError:
        return


# ---------------------------------------------------------------------------
# Notification payload builder
# ---------------------------------------------------------------------------


def _notif_to_payload(n: Notification) -> dict[str, Any]:
    """Convert a Notification ORM row to the JSON payload sent over SSE and REST.

    Args:
        n: Notification ORM instance.

    Returns:
        Dict suitable for JSON serialization.
    """
    return {
        "id": n.id,
        "type": n.type,
        "title": n.title,
        "message": n.message,
        "variant": n.variant,
        "project_id": n.project_id,
        "scan_id": n.scan_id,
        "link": n.link,
        "created_at": n.created_at,
        "read": n.read,
    }


# ---------------------------------------------------------------------------
# Notification generator
# ---------------------------------------------------------------------------

_HEALTH_DROP_THRESHOLD = 10


async def _scan_display_name(db: AsyncSession, scan: Scan) -> str:
    """Return a user-facing name without lazy-loading ``Scan.project``.

    ``AsyncSession`` cannot implicit-IO a relationship (asyncpg raises
    ``MissingGreenlet``). Look the project up by primary key instead.

    Args:
        db: Active async session.
        scan: Persisted scan row.

    Returns:
        Project name when ``project_id`` resolves, otherwise ``project_path``.
    """
    path = scan.project_path
    fallback = path if isinstance(path, str) else ""
    if not scan.project_id:
        return fallback
    project = await db.get(Project, scan.project_id)
    if project is None:
        return fallback
    name = project.name
    return name if isinstance(name, str) and name else fallback


async def _should_write_notification(
    db: AsyncSession,
    existing: Notification | None,
    scan: Scan,
) -> bool:
    """Return True when a notification of this type should be inserted.

    An unattributed row (no ``project_id``) is deleted and replaced when
    the scan has since been linked to a project.

    Args:
        db: Active async session.
        existing: Prior notification of the same type, if any.
        scan: Scan being notified.

    Returns:
        True if the caller should insert a new row.
    """
    if existing is None:
        return True
    if scan.project_id and not existing.project_id:
        await db.delete(existing)
        return True
    return False


async def generate_notifications(
    db: AsyncSession,
    scan: Scan,
    violations: list[Violation],
    *,
    old_health_score: int | None = None,
    new_health_score: int | None = None,
) -> list[dict[str, Any]]:
    """Create notification rows from a completed scan event.

    The caller is responsible for committing the transaction and then
    calling :func:`broadcast_notifications` with the returned payloads.

    Display names use ``Project`` loaded by primary key, never the
    ``Scan.project`` relationship (async lazy-load is unsafe). Types
    already stored for this ``scan_id`` are skipped, except an
    unattributed ``scan_complete`` (no ``project_id``) is replaced once
    the scan is linked to a project so the operate path can correct
    title and display name.

    Args:
        db: Active async database session (caller commits).
        scan: The persisted Scan ORM row.
        violations: All violations from the scan (remaining + fixed).
        old_health_score: Project health score before this scan (None if unknown).
        new_health_score: Project health score after this scan (None if unknown).

    Returns:
        List of notification payloads (caller broadcasts after commit).
    """
    payloads: list[dict[str, Any]] = []

    display_name = await _scan_display_name(db, scan)
    existing_by_type: dict[str, Notification] = {}
    if scan.scan_id:
        existing_rows = (
            (await db.execute(sa_select(Notification).where(Notification.scan_id == scan.scan_id))).scalars().all()
        )
        existing_by_type = {row.type: row for row in existing_rows}

    # -- Scan complete notification -----------------------------------------

    if await _should_write_notification(db, existing_by_type.get("scan_complete"), scan):
        if scan.scan_type == "remediate":
            remaining = max(scan.total_violations - scan.fixed_count, 0)
            title = "Remediation Complete"
            msg = f"{display_name}: {scan.fixed_count} findings resolved, {remaining} remaining"
            variant = "success" if scan.fixed_count > 0 else "info"
        else:
            title = "Check Complete"
            msg = f"{display_name}: {scan.total_violations} violations found"
            variant = "success" if scan.total_violations == 0 else "info"

        notif = await insert_notification(
            db,
            type="scan_complete",
            title=title,
            message=msg,
            variant=variant,
            project_id=scan.project_id,
            scan_id=scan.scan_id,
            link=f"/activity/{scan.scan_id}",
        )
        payloads.append(_notif_to_payload(notif))

    # -- Secrets detected (Gitleaks SEC:* violations) -----------------------

    sec_violations = [v for v in violations if v.rule_id.startswith("SEC:")]
    if sec_violations and await _should_write_notification(db, existing_by_type.get("secrets_detected"), scan):
        sec_files = sorted({v.file for v in sec_violations if v.file})
        if sec_files:
            file_list = ", ".join(sec_files[:5])
            if len(sec_files) > 5:
                file_list += f" (+{len(sec_files) - 5} more)"
            sec_message = f"{display_name}: {len(sec_violations)} secret(s) found in {file_list}"
        else:
            sec_message = f"{display_name}: {len(sec_violations)} secret(s) found"

        notif_sec = await insert_notification(
            db,
            type="secrets_detected",
            title="Secrets Detected",
            message=sec_message,
            variant="danger",
            project_id=scan.project_id,
            scan_id=scan.scan_id,
            link=f"/activity/{scan.scan_id}",
        )
        payloads.append(_notif_to_payload(notif_sec))

    # -- Health score drop --------------------------------------------------

    if (
        existing_by_type.get("health_changed") is None
        and old_health_score is not None
        and new_health_score is not None
        and old_health_score - new_health_score >= _HEALTH_DROP_THRESHOLD
    ):
        notif_health = await insert_notification(
            db,
            type="health_changed",
            title="Health Score Declined",
            message=f"{display_name}: health score dropped from {old_health_score} to {new_health_score}",
            variant="warning",
            project_id=scan.project_id,
            scan_id=scan.scan_id,
            link=f"/projects/{scan.project_id}" if scan.project_id else f"/activity/{scan.scan_id}",
        )
        payloads.append(_notif_to_payload(notif_health))

    return payloads


def broadcast_notifications(payloads: list[dict[str, Any]]) -> None:
    """Push pre-built notification payloads to all connected SSE clients.

    Must be called **after** the database transaction has been committed so
    that SSE consumers can fetch the notification via REST if needed.

    Args:
        payloads: Notification dicts as returned by ``generate_notifications``.
    """
    for p in payloads:
        _broadcast(p)
