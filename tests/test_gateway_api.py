"""Unit tests for the gateway REST API endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from apme_gateway.app import create_app
from apme_gateway.db import close_db, get_session, init_db
from apme_gateway.db.models import Proposal, Scan, ScanLog, Session, Violation


@pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
async def _db(tmp_path: Path) -> AsyncIterator[None]:
    """Initialise a fresh DB per test.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Yields:
        None: Test runs between setup and teardown.
    """
    await init_db(str(tmp_path / "test.db"))
    yield
    await close_db()


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


async def _seed(
    *,
    session_id: str = "abc",
    scan_id: str = "scan-1",
    add_violation: bool = False,
    add_proposal: bool = False,
    add_log: bool = False,
) -> None:
    """Insert test data.

    Args:
        session_id: Session hash.
        scan_id: Scan UUID.
        add_violation: Whether to add a violation row.
        add_proposal: Whether to add a proposal row.
        add_log: Whether to add a log row.
    """
    async with get_session() as db:
        db.add(Session(session_id=session_id, project_path="/proj", first_seen="t0", last_seen="t1"))
        db.add(
            Scan(
                scan_id=scan_id,
                session_id=session_id,
                project_path="/proj",
                source="cli",
                created_at="2026-01-01T00:00:00Z",
                scan_type="scan",
                total_violations=1 if add_violation else 0,
            )
        )
        if add_violation:
            db.add(Violation(scan_id=scan_id, rule_id="L001", level="error", message="bad", file="a.yml", line=5))
        if add_proposal:
            db.add(
                Proposal(
                    scan_id=scan_id,
                    proposal_id="p1",
                    rule_id="L001",
                    file="a.yml",
                    tier=2,
                    confidence=0.9,
                    status="approved",
                )
            )
        if add_log:
            db.add(ScanLog(scan_id=scan_id, message="starting", phase="engine", level=2))
        await db.commit()


async def test_health(client: AsyncClient) -> None:
    """Health endpoint returns ok when DB is available.

    Args:
        client: Async HTTP test client.
    """
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


async def test_list_sessions_empty(client: AsyncClient) -> None:
    """Empty DB returns empty session list.

    Args:
        client: Async HTTP test client.
    """
    resp = await client.get("/api/v1/sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []


async def test_list_sessions(client: AsyncClient) -> None:
    """Seeded session appears in list.

    Args:
        client: Async HTTP test client.
    """
    await _seed()
    resp = await client.get("/api/v1/sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["session_id"] == "abc"


async def test_get_session_detail(client: AsyncClient) -> None:
    """Session detail includes scans.

    Args:
        client: Async HTTP test client.
    """
    await _seed()
    resp = await client.get("/api/v1/sessions/abc")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "abc"
    assert len(body["scans"]) == 1


async def test_get_session_not_found(client: AsyncClient) -> None:
    """Missing session returns 404.

    Args:
        client: Async HTTP test client.
    """
    resp = await client.get("/api/v1/sessions/missing")
    assert resp.status_code == 404


async def test_list_scans(client: AsyncClient) -> None:
    """Scans are listed with pagination.

    Args:
        client: Async HTTP test client.
    """
    await _seed()
    resp = await client.get("/api/v1/scans")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1


async def test_list_scans_filter_session(client: AsyncClient) -> None:
    """Scans filter by session_id query param.

    Args:
        client: Async HTTP test client.
    """
    await _seed(session_id="s1", scan_id="sc1")
    await _seed(session_id="s2", scan_id="sc2")
    resp = await client.get("/api/v1/scans", params={"session_id": "s1"})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["scan_id"] == "sc1"


async def test_get_scan_detail_with_children(client: AsyncClient) -> None:
    """Scan detail includes violations, proposals, and logs.

    Args:
        client: Async HTTP test client.
    """
    await _seed(add_violation=True, add_proposal=True, add_log=True)
    resp = await client.get("/api/v1/scans/scan-1")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["violations"]) == 1
    assert body["violations"][0]["rule_id"] == "L001"
    assert len(body["proposals"]) == 1
    assert body["proposals"][0]["status"] == "approved"
    assert len(body["logs"]) == 1


async def test_get_scan_not_found(client: AsyncClient) -> None:
    """Missing scan returns 404.

    Args:
        client: Async HTTP test client.
    """
    resp = await client.get("/api/v1/scans/missing")
    assert resp.status_code == 404


async def test_delete_scan(client: AsyncClient) -> None:
    """Delete removes a scan.

    Args:
        client: Async HTTP test client.
    """
    await _seed()
    resp = await client.delete("/api/v1/scans/scan-1")
    assert resp.status_code == 204
    resp = await client.get("/api/v1/scans/scan-1")
    assert resp.status_code == 404


async def test_delete_scan_not_found(client: AsyncClient) -> None:
    """Delete on missing scan returns 404.

    Args:
        client: Async HTTP test client.
    """
    resp = await client.delete("/api/v1/scans/missing")
    assert resp.status_code == 404


async def test_top_violations(client: AsyncClient) -> None:
    """Top violations aggregates across scans.

    Args:
        client: Async HTTP test client.
    """
    await _seed(scan_id="sc1", add_violation=True)
    async with get_session() as db:
        db.add(
            Scan(
                scan_id="sc2",
                session_id="abc",
                project_path="/proj",
                source="cli",
                created_at="2026-01-02T00:00:00Z",
                scan_type="scan",
                total_violations=1,
            )
        )
        db.add(Violation(scan_id="sc2", rule_id="L001", level="error", message="bad", file="b.yml"))
        await db.commit()
    resp = await client.get("/api/v1/violations/top")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["rule_id"] == "L001"
    assert body[0]["count"] == 2
