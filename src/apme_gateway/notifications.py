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
from collections.abc import AsyncGenerator, Sequence
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


async def _should_write_scan_complete(
    db: AsyncSession,
    existing: Notification | None,
    scan: Scan,
) -> bool:
    """Return True when a ``scan_complete`` notification should be inserted.

    An unattributed row (no ``project_id``) is deleted and replaced when
    the scan has since been linked to a project so title/display name can
    be corrected. Other notification types must not use this path — they
    only insert when no row of that type exists yet.

    Args:
        db: Active async session.
        existing: Prior ``scan_complete`` notification, if any.
        scan: Scan being notified.

    Returns:
        True if the caller should insert a new ``scan_complete`` row.
    """
    if existing is None:
        return True
    if scan.project_id and not existing.project_id:
        await db.delete(existing)
        # Flush so a future (scan_id, type) uniqueness constraint cannot see
        # both the deleted row and the replacement insert in one flush.
        await db.flush()
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
    title and display name. ``secrets_detected`` is never replaced after
    insert (existence-only) so linking a project does not reset ``read``
    or rebroadcast.

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

    if await _should_write_scan_complete(db, existing_by_type.get("scan_complete"), scan):
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
    if sec_violations and existing_by_type.get("secrets_detected") is None:
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


# ---------------------------------------------------------------------------
# Persistent notification outbox (N18)
# ---------------------------------------------------------------------------
# Owns the outbox retry lifecycle: intent persistence before ACK, attempt
# accounting, sent stamping, violation-set hashing, and startup
# reconciliation. Moved here from ``grpc_reporting.servicer`` so the
# servicer stays under 1k lines; the servicer re-exports these names for
# backward compatibility (tests patch the servicer namespace).

# Strong refs so fire-and-forget notification tasks are not GC'd mid-flight.
_pending_notification_tasks: set[asyncio.Task[None]] = set()

# Startup reconciliation bounds (N18): oldest-first page size per call and
# max concurrent inline generations (no unbounded task fan-out).
_RECONCILE_OUTBOX_PAGE_SIZE = 100
_RECONCILE_OUTBOX_CONCURRENCY = 5


def _outbox_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns:
        ISO-8601 timestamp string.
    """
    from datetime import UTC as _UTC
    from datetime import datetime as _datetime

    return _datetime.now(tz=_UTC).isoformat()


async def _deliver_scan_notifications(scan_id: str) -> bool:
    """Run one best-effort notification attempt; mark sent only on success.

    Bumps the outbox attempt counter, generates from the latest committed
    rows, and stamps ``sent_at`` only when generation reports success, so
    a failed attempt stays unsent for startup reconciliation to retry.

    Args:
        scan_id: Persisted scan UUID to notify for.

    Returns:
        True only when notifications were generated successfully.
    """
    try:
        await _bump_notification_outbox_attempts(scan_id)
        ok = False
        from apme_gateway.db import get_session as _get_session  # noqa: PLC0415

        async with _get_session() as db:
            from apme_gateway.grpc_reporting.servicer import (  # noqa: PLC0415
                _generate_scan_notifications as _generate,
            )

            ok = await _generate(db, scan_id)
        if ok:
            await _mark_notification_outbox_sent(scan_id)
        else:
            logger.warning(
                "Notification generation unsuccessful for scan %s; outbox left unsent",
                scan_id,
            )
        return ok
    except Exception:
        logger.warning(
            "Notification generation failed for scan %s",
            scan_id,
            exc_info=True,
        )
        return False


def _violation_set_hash(violations: Sequence[Any]) -> str:
    """Hash a scan's violation set for outbox re-queue decisions (#21).

    Args:
        violations: ORM Violation rows (or objects with rule_id/file/line/message).

    Returns:
        Hex SHA-256 over sorted violation identity tuples.
    """
    import hashlib as _hashlib  # noqa: PLC0415

    items: list[tuple[str, str, str, str]] = []
    for v in violations:
        items.append(
            (
                str(getattr(v, "rule_id", "") or ""),
                str(getattr(v, "file", "") or ""),
                str(getattr(v, "line", "") or ""),
                str(getattr(v, "message", "") or ""),
            )
        )
    items.sort()
    h = _hashlib.sha256()
    for parts in items:
        h.update("\x00".join(parts).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


async def _enqueue_notification_outbox(scan_id: str) -> None:
    """Persist notification intent before ACK (N18, best-effort).

    Runs in its own short transaction after the scan commit — separate
    from (not the same as) the scan persistence transaction — so outbox
    failures never fail the already-committed RPC. Uses get-or-create
    with an ``IntegrityError`` fallback instead of check-then-insert so
    concurrent duplicate ``ReportFixCompleted`` deliveries for one scan
    cannot race into a duplicate-key failure. A sent row is re-queued
    only when the violation set changed (``content_hash`` differs, #21);
    true duplicates (same hash) stay silent.

    Args:
        scan_id: Persisted scan UUID to notify for.
    """
    try:
        from sqlalchemy import select as _sa_select  # noqa: PLC0415
        from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

        from apme_gateway.db import get_session as _get_session  # noqa: PLC0415
        from apme_gateway.db.models import NotificationOutbox, Violation  # noqa: PLC0415

        async with _get_session() as db:
            rows = (await db.execute(_sa_select(Violation).where(Violation.scan_id == scan_id))).scalars().all()
            content_hash = _violation_set_hash(rows)
            existing = (
                await db.execute(_sa_select(NotificationOutbox).where(NotificationOutbox.scan_id == scan_id))
            ).scalar_one_or_none()
            if existing is None:
                db.add(
                    NotificationOutbox(
                        scan_id=scan_id,
                        created_at=_outbox_now_iso(),
                        sent_at=None,
                        attempts=0,
                        content_hash=content_hash,
                    )
                )
                try:
                    await db.commit()
                except IntegrityError:
                    # Lost a concurrent insert race for the same scan_id:
                    # roll back; the winner row was just inserted unsent, so
                    # no state change is needed — and a sent row must never
                    # be resurrected here.
                    await db.rollback()
                    winner = (
                        await db.execute(_sa_select(NotificationOutbox).where(NotificationOutbox.scan_id == scan_id))
                    ).scalar_one_or_none()
                    if winner is not None and winner.sent_at is not None:
                        logger.debug(
                            "Notification outbox for scan %s already sent; race loser left sent",
                            scan_id,
                        )
                    return
            elif existing.sent_at is None:
                # Re-queue on replay: a new ReportFixCompleted means fresh
                # notifications may be due (idempotent generation skips dupes).
                existing.sent_at = None
                existing.content_hash = content_hash
                await db.commit()
            elif getattr(existing, "content_hash", None) != content_hash:
                # Genuinely new violations for an already-notified scan:
                # re-queue so generation runs again (#21).
                existing.sent_at = None
                existing.content_hash = content_hash
                await db.commit()
            else:
                logger.debug(
                    "Notification outbox for scan %s already sent; duplicate delivery left sent",
                    scan_id,
                )
    except Exception:
        logger.warning("Failed to enqueue notification outbox for scan %s", scan_id, exc_info=True)


async def _bump_notification_outbox_attempts(scan_id: str) -> None:
    """Increment the outbox attempt counter for one generation try (N18).

    Called on every try — success or failure — so ``attempts`` measures
    generation pressure for observability, not just completions. Failures
    are logged and never propagated.

    Args:
        scan_id: Scan whose notifications are being generated.
    """
    try:
        from sqlalchemy import select as _sa_select  # noqa: PLC0415

        from apme_gateway.db import get_session as _get_session  # noqa: PLC0415
        from apme_gateway.db.models import NotificationOutbox  # noqa: PLC0415

        async with _get_session() as db:
            row = (
                await db.execute(_sa_select(NotificationOutbox).where(NotificationOutbox.scan_id == scan_id))
            ).scalar_one_or_none()
            if row is not None:
                row.attempts = int(row.attempts or 0) + 1
                await db.commit()
    except Exception:
        logger.warning("Failed to bump notification outbox attempts for scan %s", scan_id, exc_info=True)


async def _mark_notification_outbox_sent(scan_id: str) -> None:
    """Mark the outbox row sent after successful generation (N18).

    Only stamps ``sent_at`` — ``attempts`` is bumped on every try by
    :func:`_bump_notification_outbox_attempts`, not here, so failed
    attempts remain visible.

    Args:
        scan_id: Scan whose notifications were generated.
    """
    try:
        from sqlalchemy import select as _sa_select  # noqa: PLC0415

        from apme_gateway.db import get_session as _get_session  # noqa: PLC0415
        from apme_gateway.db.models import NotificationOutbox  # noqa: PLC0415

        async with _get_session() as db:
            row = (
                await db.execute(_sa_select(NotificationOutbox).where(NotificationOutbox.scan_id == scan_id))
            ).scalar_one_or_none()
            if row is not None:
                row.sent_at = _outbox_now_iso()
                await db.commit()
    except Exception:
        logger.warning("Failed to mark notification outbox sent for scan %s", scan_id, exc_info=True)


async def reconcile_notification_outbox(*, limit: int = 100) -> int:
    """Replay unsent notification intents after startup (N18).

    Queries the oldest unsent outbox rows first (``ORDER BY attempts,
    created_at``) with ``LIMIT 100`` and caps re-queued rows per call;
    generations run inline with a semaphore (max 5 concurrent) instead
    of an unbounded fire-and-forget fan-out. Ordering low-attempt rows
    first keeps one repeatedly-failing (high-attempt) row from head-of-
    line blocking fresh rows on every call. Safe to call multiple
    times; generation itself is idempotent (existing notification types
    are skipped). Returns the count of successfully delivered rows (not
    rows selected), so drain loops stop on a fully-failing page instead
    of spinning forever on persistently failing rows.

    Args:
        limit: Maximum rows to re-queue per call (clamped to 100).

    Returns:
        Number of scans successfully delivered (marked sent).
    """
    page = max(1, min(limit, _RECONCILE_OUTBOX_PAGE_SIZE))
    try:
        from sqlalchemy import select as _sa_select  # noqa: PLC0415

        from apme_gateway.db import get_session as _get_session  # noqa: PLC0415
        from apme_gateway.db.models import NotificationOutbox  # noqa: PLC0415

        async with _get_session() as db:
            rows = list(
                (
                    await db.execute(
                        _sa_select(NotificationOutbox)
                        .where(NotificationOutbox.sent_at.is_(None))
                        .order_by(NotificationOutbox.attempts, NotificationOutbox.created_at)
                        .limit(page)
                    )
                )
                .scalars()
                .all()
            )
    except Exception:
        logger.warning("Notification outbox reconciliation failed (query)", exc_info=True)
        return 0
    scan_ids = [row.scan_id for row in rows]
    semaphore = asyncio.Semaphore(_RECONCILE_OUTBOX_CONCURRENCY)

    async def _one(scan_id: str) -> bool:
        async with semaphore:
            return await _deliver_scan_notifications(scan_id)

    results = await asyncio.gather(*(_one(scan_id) for scan_id in scan_ids), return_exceptions=True)
    delivered = sum(1 for r in results if r is True)
    if scan_ids:
        logger.info(
            "Notification outbox reconciliation delivered %d/%d scan(s)",
            delivered,
            len(scan_ids),
        )
    return delivered
