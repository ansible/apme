"""Unit tests for the gateway database layer and query functions."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from apme_gateway.db import close_db, get_session, init_db
from apme_gateway.db import queries as q
from apme_gateway.db.models import Proposal, Scan, ScanLog, Session, Violation


@pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
async def _db(tmp_path: Path) -> AsyncIterator[None]:
    """Initialise a fresh DB per test.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Yields:
        None: Test runs between setup and teardown.
    """
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    yield
    await close_db()


async def _seed_session(session_id: str = "abc123", project_path: str = "/proj") -> None:
    """Insert a test session row.

    Args:
        session_id: Session hash.
        project_path: Project root.
    """
    async with get_session() as db:
        db.add(Session(session_id=session_id, project_path=project_path, first_seen="t0", last_seen="t0"))
        await db.commit()


async def _seed_scan(
    scan_id: str = "scan-1",
    session_id: str = "abc123",
    project_path: str = "/proj",
    scan_type: str = "scan",
) -> None:
    """Insert a test scan row (requires session to exist).

    Args:
        scan_id: Scan UUID.
        session_id: Owning session.
        project_path: Project root.
        scan_type: Either "scan" or "fix".
    """
    async with get_session() as db:
        db.add(
            Scan(
                scan_id=scan_id,
                session_id=session_id,
                project_path=project_path,
                source="cli",
                created_at="2026-01-01T00:00:00Z",
                scan_type=scan_type,
                total_violations=3,
                auto_fixable=1,
                ai_candidate=1,
                manual_review=1,
            )
        )
        await db.commit()


async def test_list_sessions_empty() -> None:
    """Listing sessions on empty DB returns empty list."""
    async with get_session() as db:
        result = await q.list_sessions(db)
    assert result == []


async def test_list_sessions_returns_rows() -> None:
    """Seeded session appears in list."""
    await _seed_session()
    async with get_session() as db:
        result = await q.list_sessions(db)
    assert len(result) == 1
    assert result[0].session_id == "abc123"


async def test_get_session_not_found() -> None:
    """Missing session returns None."""
    async with get_session() as db:
        result = await q.get_session(db, "missing")
    assert result is None


async def test_get_session_with_scans() -> None:
    """Session detail eagerly loads scans."""
    await _seed_session()
    await _seed_scan()
    async with get_session() as db:
        result = await q.get_session(db, "abc123")
    assert result is not None
    assert len(result.scans) == 1


async def test_list_scans_filter_by_session() -> None:
    """Scans filter by session_id."""
    await _seed_session("s1", "/a")
    await _seed_session("s2", "/b")
    await _seed_scan("scan-1", "s1")
    await _seed_scan("scan-2", "s2")
    async with get_session() as db:
        result = await q.list_scans(db, session_id="s1")
    assert len(result) == 1
    assert result[0].scan_id == "scan-1"


async def test_get_scan_detail() -> None:
    """Scan detail loads violations and logs."""
    await _seed_session()
    await _seed_scan()
    async with get_session() as db:
        db.add(Violation(scan_id="scan-1", rule_id="L001", level="error", message="bad", file="a.yml"))
        db.add(ScanLog(scan_id="scan-1", message="starting", phase="engine"))
        await db.commit()
    async with get_session() as db:
        scan = await q.get_scan(db, "scan-1")
    assert scan is not None
    assert len(scan.violations) == 1
    assert len(scan.logs) == 1


async def test_top_violations() -> None:
    """Top violations aggregates across scans."""
    await _seed_session()
    await _seed_scan("s1")
    await _seed_scan("s2")
    async with get_session() as db:
        for sid in ("s1", "s2"):
            db.add(Violation(scan_id=sid, rule_id="L001", level="error", message="x", file="a.yml"))
        db.add(Violation(scan_id="s1", rule_id="L002", level="warning", message="y", file="b.yml"))
        await db.commit()
    async with get_session() as db:
        result = await q.top_violations(db, limit=5)
    assert result[0] == ("L001", 2)
    assert result[1] == ("L002", 1)


async def test_delete_scan() -> None:
    """Deleting a scan removes it from the DB."""
    await _seed_session()
    await _seed_scan()
    async with get_session() as db:
        deleted = await q.delete_scan(db, "scan-1")
    assert deleted is True
    async with get_session() as db:
        scan = await q.get_scan(db, "scan-1")
    assert scan is None


async def test_delete_scan_not_found() -> None:
    """Deleting a non-existent scan returns False."""
    async with get_session() as db:
        deleted = await q.delete_scan(db, "missing")
    assert deleted is False


async def test_session_count() -> None:
    """Session count reflects inserted rows."""
    await _seed_session("s1")
    await _seed_session("s2")
    async with get_session() as db:
        count = await q.session_count(db)
    assert count == 2


async def test_scan_count_with_filter() -> None:
    """Scan count filters by session_id."""
    await _seed_session("s1", "/a")
    await _seed_session("s2", "/b")
    await _seed_scan("sc1", "s1")
    await _seed_scan("sc2", "s1")
    await _seed_scan("sc3", "s2")
    async with get_session() as db:
        total = await q.scan_count(db)
        filtered = await q.scan_count(db, session_id="s1")
    assert total == 3
    assert filtered == 2


async def test_proposals_stored() -> None:
    """Proposals are persisted and queryable."""
    await _seed_session()
    await _seed_scan()
    async with get_session() as db:
        db.add(
            Proposal(
                scan_id="scan-1",
                proposal_id="p1",
                rule_id="L001",
                file="a.yml",
                tier=2,
                confidence=0.85,
                status="approved",
            )
        )
        await db.commit()
    async with get_session() as db:
        props = await q.get_proposals(db, "scan-1")
    assert len(props) == 1
    assert props[0].status == "approved"
