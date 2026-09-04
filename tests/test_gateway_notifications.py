"""Unit tests for the notification system (generator, REST endpoints, SSE hub)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from apme_gateway.app import create_app
from apme_gateway.db import get_session
from apme_gateway.db.models import Notification, Project, Scan, Session, Violation
from apme_gateway.notifications import (
    _broadcast,
    broadcast_notifications,
    generate_notifications,
    subscribe,
    unsubscribe,
)

pytestmark = pytest.mark.usefixtures("gateway_db")


@pytest.fixture  # type: ignore[untyped-decorator]
async def client() -> AsyncIterator[AsyncClient]:
    """Build an async test client for the gateway app.

    Yields:
        AsyncClient: Client bound to the ASGI app.
    """
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_session_and_scan(
    *,
    scan_id: str = "scan-1",
    session_id: str = "sess-1",
    project_path: str = "/proj",
    scan_type: str = "check",
    total_violations: int = 3,
    fixed_count: int = 0,
    project_id: str | None = None,
) -> Scan:
    """Insert a session and scan for testing.

    Args:
        scan_id: Scan UUID.
        session_id: Session hash.
        project_path: Filesystem path.
        scan_type: Either check or remediate.
        total_violations: Violation count.
        fixed_count: Number of fixed violations.
        project_id: Optional project FK.

    Returns:
        The created Scan row.
    """
    async with get_session() as db:
        db.add(Session(session_id=session_id, project_path=project_path, first_seen="t0", last_seen="t1"))
        scan = Scan(
            scan_id=scan_id,
            session_id=session_id,
            project_id=project_id,
            project_path=project_path,
            source="cli",
            created_at="2026-01-01T00:00:00Z",
            scan_type=scan_type,
            total_violations=total_violations,
            fixed_count=fixed_count,
        )
        db.add(scan)
        await db.commit()
        return scan


# ---------------------------------------------------------------------------
# Notification generator tests
# ---------------------------------------------------------------------------


class TestGenerateNotifications:
    """Tests for generate_notifications()."""

    async def test_scan_complete_notification_created(self) -> None:
        """A scan_complete notification is generated when none exists yet."""
        await _seed_session_and_scan()
        async with get_session() as db:
            from sqlalchemy import select

            scan = (await db.execute(select(Scan).where(Scan.scan_id == "scan-1"))).scalar_one()
            payloads = await generate_notifications(db, scan, [])
            await db.commit()

        assert len(payloads) == 1
        assert payloads[0]["type"] == "scan_complete"
        assert payloads[0]["variant"] == "info"
        assert "3 violations" in payloads[0]["message"]

    async def test_remediate_scan_notification(self) -> None:
        """Remediation scans include fixed count and correct remaining in the message."""
        await _seed_session_and_scan(scan_type="remediate", fixed_count=5, total_violations=7)
        async with get_session() as db:
            from sqlalchemy import select

            scan = (await db.execute(select(Scan).where(Scan.scan_id == "scan-1"))).scalar_one()
            payloads = await generate_notifications(db, scan, [])
            await db.commit()

        assert payloads[0]["type"] == "scan_complete"
        assert payloads[0]["title"] == "Remediation Complete"
        assert "5 findings resolved" in payloads[0]["message"]
        assert "2 remaining" in payloads[0]["message"]
        assert payloads[0]["variant"] == "success"

    async def test_zero_violation_check_is_success(self) -> None:
        """A clean check with zero violations uses success variant."""
        await _seed_session_and_scan(total_violations=0)
        async with get_session() as db:
            from sqlalchemy import select

            scan = (await db.execute(select(Scan).where(Scan.scan_id == "scan-1"))).scalar_one()
            payloads = await generate_notifications(db, scan, [])
            await db.commit()

        assert payloads[0]["variant"] == "success"

    async def test_secrets_detected_notification(self) -> None:
        """SEC:* violations trigger a separate danger notification."""
        await _seed_session_and_scan()
        sec_violations = [
            Violation(scan_id="scan-1", rule_id="SEC:aws-access-key", level="error", message="", file="creds.yml"),
            Violation(scan_id="scan-1", rule_id="SEC:private-key", level="error", message="", file="keys.pem"),
        ]
        async with get_session() as db:
            from sqlalchemy import select

            scan = (await db.execute(select(Scan).where(Scan.scan_id == "scan-1"))).scalar_one()
            payloads = await generate_notifications(db, scan, sec_violations)
            await db.commit()

        assert len(payloads) == 2
        sec_payload = payloads[1]
        assert sec_payload["type"] == "secrets_detected"
        assert sec_payload["variant"] == "danger"
        assert sec_payload["title"] == "Secrets Detected"
        assert "2 secret(s)" in sec_payload["message"]

    async def test_no_secrets_notification_for_non_sec_rules(self) -> None:
        """Non-SEC violations should not trigger a secrets notification."""
        await _seed_session_and_scan()
        violations = [
            Violation(scan_id="scan-1", rule_id="L001", level="error", message="", file="a.yml"),
        ]
        async with get_session() as db:
            from sqlalchemy import select

            scan = (await db.execute(select(Scan).where(Scan.scan_id == "scan-1"))).scalar_one()
            payloads = await generate_notifications(db, scan, violations)
            await db.commit()

        assert len(payloads) == 1
        assert payloads[0]["type"] == "scan_complete"

    async def test_health_drop_notification(self) -> None:
        """A health score drop >= 10 triggers a warning notification."""
        await _seed_session_and_scan()
        async with get_session() as db:
            from sqlalchemy import select

            scan = (await db.execute(select(Scan).where(Scan.scan_id == "scan-1"))).scalar_one()
            payloads = await generate_notifications(
                db,
                scan,
                [],
                old_health_score=80,
                new_health_score=65,
            )
            await db.commit()

        assert len(payloads) == 2
        health_payload = payloads[1]
        assert health_payload["type"] == "health_changed"
        assert health_payload["variant"] == "warning"
        assert "80" in health_payload["message"]
        assert "65" in health_payload["message"]

    async def test_small_health_drop_no_notification(self) -> None:
        """A health score drop < 10 does not trigger a notification."""
        await _seed_session_and_scan()
        async with get_session() as db:
            from sqlalchemy import select

            scan = (await db.execute(select(Scan).where(Scan.scan_id == "scan-1"))).scalar_one()
            payloads = await generate_notifications(
                db,
                scan,
                [],
                old_health_score=80,
                new_health_score=75,
            )
            await db.commit()

        assert len(payloads) == 1  # only scan_complete

    async def test_project_id_lookup_uses_project_name(self) -> None:
        """Project display name is loaded by primary key, not the relationship."""
        async with get_session() as db:
            db.add(
                Project(
                    id="proj-n1",
                    name="Named Project",
                    repo_url="https://github.com/test/named.git",
                    branch="main",
                    created_at="2026-09-04T00:00:00Z",
                    health_score=80,
                )
            )
            await db.commit()
        await _seed_session_and_scan(project_id="proj-n1")
        async with get_session() as db:
            from sqlalchemy import select

            scan = (await db.execute(select(Scan).where(Scan.scan_id == "scan-1"))).scalar_one()
            payloads = await generate_notifications(db, scan, [])
            await db.commit()

        assert "Named Project:" in payloads[0]["message"]

    async def test_skips_types_already_stored_for_scan(self) -> None:
        """A second call for the same scan_id does not duplicate scan_complete."""
        await _seed_session_and_scan()
        async with get_session() as db:
            from sqlalchemy import select

            scan = (await db.execute(select(Scan).where(Scan.scan_id == "scan-1"))).scalar_one()
            first = await generate_notifications(db, scan, [])
            await db.commit()
            second = await generate_notifications(db, scan, [])
            await db.commit()

        assert len(first) == 1
        assert first[0]["type"] == "scan_complete"
        assert second == []

    async def test_replaces_unattributed_scan_complete_after_project_link(self) -> None:
        """Operate-path generate after link_scan_to_project replaces the CLI copy."""
        async with get_session() as db:
            db.add(
                Project(
                    id="proj-n3",
                    name="Linked Project",
                    repo_url="https://github.com/test/linked.git",
                    branch="main",
                    created_at="2026-09-04T00:00:00Z",
                    health_score=80,
                )
            )
            await db.commit()
        await _seed_session_and_scan(scan_type="check", total_violations=4)
        async with get_session() as db:
            from sqlalchemy import select

            scan = (await db.execute(select(Scan).where(Scan.scan_id == "scan-1"))).scalar_one()
            first = await generate_notifications(db, scan, [])
            await db.commit()
            scan.project_id = "proj-n3"
            scan.scan_type = "check"
            second = await generate_notifications(db, scan, [])
            await db.commit()
            rows = list(
                (await db.execute(select(Notification).where(Notification.scan_id == "scan-1"))).scalars().all()
            )

        assert first[0]["message"].startswith("/proj:")
        assert len(second) == 1
        assert "Linked Project:" in second[0]["message"]
        assert second[0]["title"] == "Check Complete"
        assert len(rows) == 1
        assert rows[0].project_id == "proj-n3"


# ---------------------------------------------------------------------------
# SSE hub tests
# ---------------------------------------------------------------------------


class TestSSEHub:
    """Tests for the SSE subscribe/broadcast/unsubscribe mechanism."""

    def test_subscribe_and_broadcast(self) -> None:
        """Broadcast delivers to all subscribers."""
        q1 = subscribe()
        q2 = subscribe()
        try:
            _broadcast({"test": 1})
            assert q1.get_nowait() == {"test": 1}
            assert q2.get_nowait() == {"test": 1}
        finally:
            unsubscribe(q1)
            unsubscribe(q2)

    def test_unsubscribe_stops_delivery(self) -> None:
        """Unsubscribed queues don't receive further broadcasts."""
        q = subscribe()
        unsubscribe(q)
        _broadcast({"test": 2})
        assert q.empty()

    def test_double_unsubscribe_is_safe(self) -> None:
        """Unsubscribing twice does not raise."""
        q = subscribe()
        unsubscribe(q)
        unsubscribe(q)


# ---------------------------------------------------------------------------
# REST endpoint tests
# ---------------------------------------------------------------------------


async def _seed_notification(
    *,
    type: str = "scan_complete",
    title: str = "Test",
    message: str = "msg",
    variant: str = "info",
    read: bool = False,
) -> int:
    """Insert a notification row and return its ID.

    Args:
        type: Event category.
        title: Headline text.
        message: Body text.
        variant: Alert variant.
        read: Initial read state.

    Returns:
        The notification primary key.
    """
    async with get_session() as db:
        from apme_gateway.db.queries import insert_notification

        n = await insert_notification(
            db,
            type=type,
            title=title,
            message=message,
            variant=variant,
        )
        if read:
            n.read = True
        await db.commit()
        return int(n.id)


class TestNotificationEndpoints:
    """Tests for the notification REST endpoints."""

    async def test_list_empty(self, client: AsyncClient) -> None:
        """Listing with no notifications returns empty list.

        Args:
            client: Async HTTP test client.
        """
        resp = await client.get("/api/v1/notifications")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["items"] == []

    async def test_list_returns_notifications(self, client: AsyncClient) -> None:
        """Notifications are returned newest-first.

        Args:
            client: Async HTTP test client.
        """
        await _seed_notification(title="First")
        await _seed_notification(title="Second")
        resp = await client.get("/api/v1/notifications")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert body["items"][0]["title"] == "Second"
        assert body["items"][1]["title"] == "First"

    async def test_list_unread_only(self, client: AsyncClient) -> None:
        """unread_only=true filters out read notifications.

        Args:
            client: Async HTTP test client.
        """
        await _seed_notification(title="Unread")
        await _seed_notification(title="Read", read=True)
        resp = await client.get("/api/v1/notifications?unread_only=true")
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["title"] == "Unread"

    async def test_mark_read(self, client: AsyncClient) -> None:
        """PATCH /notifications/{id}/read marks notification as read.

        Args:
            client: Async HTTP test client.
        """
        nid = await _seed_notification()
        resp = await client.patch(f"/api/v1/notifications/{nid}/read")
        assert resp.status_code == 200

        resp2 = await client.get("/api/v1/notifications?unread_only=true")
        assert resp2.json()["total"] == 0

    async def test_mark_read_404(self, client: AsyncClient) -> None:
        """Marking a non-existent notification returns 404.

        Args:
            client: Async HTTP test client.
        """
        resp = await client.patch("/api/v1/notifications/9999/read")
        assert resp.status_code == 404

    async def test_mark_all_read(self, client: AsyncClient) -> None:
        """POST /notifications/read-all marks all as read.

        Args:
            client: Async HTTP test client.
        """
        await _seed_notification(title="A")
        await _seed_notification(title="B")
        resp = await client.post("/api/v1/notifications/read-all")
        assert resp.status_code == 200
        assert resp.json()["updated"] == 2

        resp2 = await client.get("/api/v1/notifications?unread_only=true")
        assert resp2.json()["total"] == 0

    async def test_delete_notification(self, client: AsyncClient) -> None:
        """DELETE /notifications/{id} removes the notification.

        Args:
            client: Async HTTP test client.
        """
        nid = await _seed_notification()
        resp = await client.delete(f"/api/v1/notifications/{nid}")
        assert resp.status_code == 204

        resp2 = await client.get("/api/v1/notifications")
        assert resp2.json()["total"] == 0

    async def test_delete_404(self, client: AsyncClient) -> None:
        """Deleting a non-existent notification returns 404.

        Args:
            client: Async HTTP test client.
        """
        resp = await client.delete("/api/v1/notifications/9999")
        assert resp.status_code == 404

    async def test_notification_schema_fields(self, client: AsyncClient) -> None:
        """Verify the response schema includes all expected fields.

        Args:
            client: Async HTTP test client.
        """
        await _seed_notification(
            type="secrets_detected",
            title="Secrets",
            message="Found 3",
            variant="danger",
        )
        resp = await client.get("/api/v1/notifications")
        item = resp.json()["items"][0]
        assert item["type"] == "secrets_detected"
        assert item["title"] == "Secrets"
        assert item["message"] == "Found 3"
        assert item["variant"] == "danger"
        assert item["read"] is False
        assert "created_at" in item
        assert "id" in item

    async def test_sse_stream_delivers_notification(self) -> None:
        """The SSE event stream generator yields broadcast payloads as ``data:`` lines."""
        import asyncio
        import json

        from apme_gateway.notifications import sse_event_stream

        q = subscribe()
        stream = sse_event_stream(q)
        try:
            payload = {"id": 1, "type": "scan_complete", "title": "Test"}
            _broadcast(payload)
            chunk = await asyncio.wait_for(anext(stream), timeout=2.0)
            data = json.loads(chunk.removeprefix("data: ").strip())
            assert data["type"] == "scan_complete"
            assert data["title"] == "Test"
        finally:
            unsubscribe(q)
            await stream.aclose()

    async def test_sse_endpoint_headers(self) -> None:
        """The /notifications/stream endpoint returns correct SSE and proxy headers."""
        from apme_gateway.api.router import notification_stream

        resp = await notification_stream()

        assert resp.media_type == "text/event-stream"
        assert resp.headers.get("Cache-Control") == "no-cache"
        assert resp.headers.get("X-Accel-Buffering") == "no"
        assert resp.headers.get("Connection") == "keep-alive"

        # Close the body iterator so the generator's finally block runs and
        # the subscriber is unregistered from the global _subscribers list.
        await resp.body_iterator.aclose()


# ---------------------------------------------------------------------------
# Broadcast-after-commit tests
# ---------------------------------------------------------------------------


class TestBroadcastNotifications:
    """Tests for the broadcast_notifications helper."""

    def test_broadcast_notifications_delivers_to_subscribers(self) -> None:
        """broadcast_notifications pushes payloads to all SSE subscribers."""
        q = subscribe()
        try:
            payloads = [{"id": 1, "type": "scan_complete"}, {"id": 2, "type": "secrets_detected"}]
            broadcast_notifications(payloads)
            assert q.get_nowait() == payloads[0]
            assert q.get_nowait() == payloads[1]
        finally:
            unsubscribe(q)


# ---------------------------------------------------------------------------
# Edge-case tests for notification generator
# ---------------------------------------------------------------------------


class TestNotificationEdgeCases:
    """Edge-case tests for generate_notifications."""

    async def test_secrets_with_no_file_paths(self) -> None:
        """SEC violations with empty file fields produce a clean message."""
        await _seed_session_and_scan()
        sec_violations = [
            Violation(scan_id="scan-1", rule_id="SEC:generic-secret", level="error", message="", file=""),
        ]
        async with get_session() as db:
            from sqlalchemy import select

            scan = (await db.execute(select(Scan).where(Scan.scan_id == "scan-1"))).scalar_one()
            payloads = await generate_notifications(db, scan, sec_violations)
            await db.commit()

        sec_payload = payloads[1]
        assert sec_payload["type"] == "secrets_detected"
        assert "found in" not in sec_payload["message"]
        assert "1 secret(s) found" in sec_payload["message"]
