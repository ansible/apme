"""Persistent notification outbox tests (N18)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import select as sa_select

from apme.v1 import reporting_pb2
from apme_gateway.db import get_session
from apme_gateway.db.models import Notification, NotificationOutbox, Scan
from apme_gateway.grpc_reporting.servicer import (
    ReportingServicer,
    _pending_notification_tasks,
    drain_notification_tasks,
    reconcile_notification_outbox,
)

pytestmark = pytest.mark.usefixtures("gateway_db")


def _mock_context() -> MagicMock:
    """Build a mock gRPC servicer context.

    Returns:
        MagicMock with async abort.
    """
    from unittest.mock import AsyncMock

    ctx = MagicMock()
    ctx.abort = AsyncMock()
    return ctx


async def _outbox_rows(scan_id: str) -> list[NotificationOutbox]:
    """Fetch outbox rows for a scan.

    Args:
        scan_id: Scan UUID to filter by.

    Returns:
        List of matching outbox rows.
    """
    async with get_session() as db:
        result = await db.execute(sa_select(NotificationOutbox).where(NotificationOutbox.scan_id == scan_id))
        return list(result.scalars().all())


async def test_report_persists_outbox_intent_before_ack() -> None:
    """ReportFixCompleted stores an unsent outbox row alongside the scan."""
    servicer = ReportingServicer()
    event = reporting_pb2.FixCompletedEvent(
        scan_id="outbox-scan-1",
        session_id="sess-outbox-1",
        project_path="/proj",
        source="cli",
    )
    await servicer.ReportFixCompleted(event, _mock_context())
    await drain_notification_tasks()

    rows = await _outbox_rows("outbox-scan-1")
    assert len(rows) == 1
    assert rows[0].sent_at is not None

    async with get_session() as db:
        scan = await db.get(Scan, "outbox-scan-1")
    assert scan is not None


async def test_restart_replays_unsent_outbox() -> None:
    """Unsent outbox rows are replayed by startup reconciliation."""
    servicer = ReportingServicer()
    event = reporting_pb2.FixCompletedEvent(
        scan_id="outbox-scan-2",
        session_id="sess-outbox-2",
        project_path="/proj",
        source="cli",
    )
    await servicer.ReportFixCompleted(event, _mock_context())

    # Simulate a restart that loses in-memory tasks: drop pending tasks
    # without awaiting them, then reset the outbox row to unsent.
    for task in list(_pending_notification_tasks):
        task.cancel()
    _pending_notification_tasks.clear()
    async with get_session() as db:
        row = (
            await db.execute(sa_select(NotificationOutbox).where(NotificationOutbox.scan_id == "outbox-scan-2"))
        ).scalar_one()
        row.sent_at = None
        await db.commit()
        # Remove any notifications the first attempt already wrote so the
        # replay is observable as a fresh insert.
        from sqlalchemy import delete as sa_delete

        await db.execute(sa_delete(Notification).where(Notification.scan_id == "outbox-scan-2"))
        await db.commit()

    requeued = await reconcile_notification_outbox()
    assert requeued >= 1
    await drain_notification_tasks()

    async with get_session() as db:
        notifs = list(
            (await db.execute(sa_select(Notification).where(Notification.scan_id == "outbox-scan-2"))).scalars().all()
        )
        outbox = (
            await db.execute(sa_select(NotificationOutbox).where(NotificationOutbox.scan_id == "outbox-scan-2"))
        ).scalar_one()
    assert len(notifs) >= 1
    assert outbox.sent_at is not None


async def test_reconcile_empty_outbox_is_noop() -> None:
    """Reconciliation with no pending rows re-queues nothing."""
    requeued = await reconcile_notification_outbox()
    assert requeued == 0


async def test_outbox_replay_is_idempotent() -> None:
    """Replaying an already-notified scan does not duplicate notifications."""
    servicer = ReportingServicer()
    event = reporting_pb2.FixCompletedEvent(
        scan_id="outbox-scan-3",
        session_id="sess-outbox-3",
        project_path="/proj",
        source="cli",
    )
    await servicer.ReportFixCompleted(event, _mock_context())
    await drain_notification_tasks()

    async with get_session() as db:
        before = list(
            (await db.execute(sa_select(Notification).where(Notification.scan_id == "outbox-scan-3"))).scalars().all()
        )
    assert len(before) >= 1

    # Force the outbox back to unsent and replay: generation skips existing
    # types, so counts must not grow.
    async with get_session() as db:
        row = (
            await db.execute(sa_select(NotificationOutbox).where(NotificationOutbox.scan_id == "outbox-scan-3"))
        ).scalar_one()
        row.sent_at = None
        await db.commit()

    await reconcile_notification_outbox()
    await drain_notification_tasks()

    async with get_session() as db:
        after = list(
            (await db.execute(sa_select(Notification).where(Notification.scan_id == "outbox-scan-3"))).scalars().all()
        )
    assert len(after) == len(before)


async def test_failed_generation_leaves_outbox_unsent() -> None:
    """A failed generation attempt never stamps the outbox row sent."""
    from unittest.mock import patch

    from apme_gateway.grpc_reporting.servicer import _deliver_scan_notifications

    servicer = ReportingServicer()
    event = reporting_pb2.FixCompletedEvent(
        scan_id="outbox-scan-fail",
        session_id="sess-outbox-fail",
        project_path="/proj",
        source="cli",
    )
    await servicer.ReportFixCompleted(event, _mock_context())
    await drain_notification_tasks()

    async with get_session() as db:
        row = (
            await db.execute(sa_select(NotificationOutbox).where(NotificationOutbox.scan_id == "outbox-scan-fail"))
        ).scalar_one()
        row.sent_at = None
        await db.commit()

    with patch(
        "apme_gateway.notifications.generate_notifications",
        side_effect=RuntimeError("notify boom"),
    ):
        ok = await _deliver_scan_notifications("outbox-scan-fail")
    assert ok is False

    rows = await _outbox_rows("outbox-scan-fail")
    assert len(rows) == 1
    assert rows[0].sent_at is None
    assert rows[0].attempts >= 1


async def test_generate_scan_notifications_bool_contract() -> None:
    """_generate_scan_notifications returns True/False, never raises."""
    from unittest.mock import patch

    from apme_gateway.grpc_reporting.servicer import _generate_scan_notifications

    servicer = ReportingServicer()
    event = reporting_pb2.FixCompletedEvent(
        scan_id="outbox-scan-bool",
        session_id="sess-outbox-bool",
        project_path="/proj",
        source="cli",
    )
    with patch("apme_gateway.grpc_reporting.servicer._schedule_scan_notifications"):
        await servicer.ReportFixCompleted(event, _mock_context())

    async with get_session() as db:
        assert await _generate_scan_notifications(db, "outbox-scan-bool") is True
    with patch(
        "apme_gateway.notifications.generate_notifications",
        side_effect=RuntimeError("notify boom"),
    ):
        async with get_session() as db:
            assert await _generate_scan_notifications(db, "outbox-scan-bool") is False


async def test_reconcile_caps_requeued_per_call_oldest_first() -> None:
    """Reconciliation pages oldest-first and caps rows per call."""
    from unittest.mock import patch

    servicer = ReportingServicer()
    with patch("apme_gateway.grpc_reporting.servicer._schedule_scan_notifications"):
        for i in range(3):
            await servicer.ReportFixCompleted(
                reporting_pb2.FixCompletedEvent(
                    scan_id=f"outbox-scan-page-{i}",
                    session_id=f"sess-outbox-page-{i}",
                    project_path="/proj",
                    source="cli",
                ),
                _mock_context(),
            )

    # Reset all three to unsent with staggered enqueue times (oldest first).
    async with get_session() as db:
        for i in range(3):
            row = (
                await db.execute(
                    sa_select(NotificationOutbox).where(NotificationOutbox.scan_id == f"outbox-scan-page-{i}")
                )
            ).scalar_one()
            row.sent_at = None
            row.created_at = f"2026-01-0{i + 1}T00:00:00+00:00"
        await db.commit()

    requeued = await reconcile_notification_outbox(limit=2)
    assert requeued == 2

    async with get_session() as db:
        states = {}
        for i in range(3):
            row = (
                await db.execute(
                    sa_select(NotificationOutbox).where(NotificationOutbox.scan_id == f"outbox-scan-page-{i}")
                )
            ).scalar_one()
            states[f"outbox-scan-page-{i}"] = row.sent_at
    # Oldest two delivered; newest still pending for the next call.
    assert states["outbox-scan-page-0"] is not None
    assert states["outbox-scan-page-1"] is not None
    assert states["outbox-scan-page-2"] is None

    # A follow-up call drains the remainder.
    assert await reconcile_notification_outbox() == 1
    await drain_notification_tasks()


async def test_duplicate_delivery_after_sent_does_not_resurrect() -> None:
    """A duplicate ReportFixCompleted for an already-sent scan stays sent (#15)."""
    from apme_gateway.grpc_reporting.servicer import _enqueue_notification_outbox

    servicer = ReportingServicer()
    event = reporting_pb2.FixCompletedEvent(
        scan_id="outbox-scan-noresurrect",
        session_id="sess-outbox-noresurrect",
        project_path="/proj",
        source="cli",
    )
    await servicer.ReportFixCompleted(event, _mock_context())
    await drain_notification_tasks()

    rows = await _outbox_rows("outbox-scan-noresurrect")
    assert len(rows) == 1
    assert rows[0].sent_at is not None
    first_sent = rows[0].sent_at

    # Duplicate delivery (idempotent replay) must not flip the row back
    # to unsent — generation is idempotent and skips existing types.
    await _enqueue_notification_outbox("outbox-scan-noresurrect")
    rows = await _outbox_rows("outbox-scan-noresurrect")
    assert len(rows) == 1
    assert rows[0].sent_at == first_sent


async def test_reconcile_prefers_low_attempt_rows() -> None:
    """A flapping high-attempt row does not head-of-line block fresh rows (#15)."""
    from unittest.mock import patch

    servicer = ReportingServicer()
    with patch("apme_gateway.grpc_reporting.servicer._schedule_scan_notifications"):
        for suffix in ("flap", "fresh"):
            await servicer.ReportFixCompleted(
                reporting_pb2.FixCompletedEvent(
                    scan_id=f"outbox-scan-prio-{suffix}",
                    session_id=f"sess-outbox-prio-{suffix}",
                    project_path="/proj",
                    source="cli",
                ),
                _mock_context(),
            )

    # Flap row is older but has failed repeatedly; fresh row is newer
    # with no attempts. Attempts order must win over age.
    async with get_session() as db:
        flap = (
            await db.execute(sa_select(NotificationOutbox).where(NotificationOutbox.scan_id == "outbox-scan-prio-flap"))
        ).scalar_one()
        flap.attempts = 5
        flap.created_at = "2026-01-01T00:00:00+00:00"
        fresh = (
            await db.execute(
                sa_select(NotificationOutbox).where(NotificationOutbox.scan_id == "outbox-scan-prio-fresh")
            )
        ).scalar_one()
        fresh.attempts = 0
        fresh.created_at = "2026-06-01T00:00:00+00:00"
        await db.commit()

    assert await reconcile_notification_outbox(limit=1) == 1
    await drain_notification_tasks()

    async with get_session() as db:
        flap_after = (
            await db.execute(sa_select(NotificationOutbox).where(NotificationOutbox.scan_id == "outbox-scan-prio-flap"))
        ).scalar_one()
        fresh_after = (
            await db.execute(
                sa_select(NotificationOutbox).where(NotificationOutbox.scan_id == "outbox-scan-prio-fresh")
            )
        ).scalar_one()
    assert fresh_after.sent_at is not None
    assert flap_after.sent_at is None
