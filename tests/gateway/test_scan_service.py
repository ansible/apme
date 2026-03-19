"""Tests for scan_service — ingest, list, get, delete, deduplication."""

from __future__ import annotations

import importlib
import uuid

import pytest

if not importlib.util.find_spec("sqlalchemy"):
    pytest.skip("gateway extras not installed", allow_module_level=True)

from sqlalchemy.ext.asyncio import AsyncSession

from apme_gateway.services import scan_service


class TestIngestScan:
    """Tests for the ingest_scan service function."""

    async def test_ingest_creates_record(self, db_session: AsyncSession) -> None:
        """A basic ingest persists a scan with violations."""
        scan = await scan_service.ingest_scan(
            db_session,
            project_name="my-project",
            violations=[
                {"rule_id": "L002", "level": "error", "message": "bad indent", "file": "site.yml"},
                {"rule_id": "L007", "level": "warning", "message": "trailing ws", "file": "site.yml"},
            ],
        )
        assert scan.project_name == "my-project"
        assert scan.status == "completed"
        assert scan.total_violations == 2
        assert scan.error_count == 1
        assert scan.warning_count == 1

    async def test_ingest_fix_scan_type(self, db_session: AsyncSession) -> None:
        """Ingesting with scan_type='fix' and fixed_count persists correctly."""
        scan = await scan_service.ingest_scan(
            db_session,
            project_name="fixed-project",
            scan_type="fix",
            fixed_count=5,
            violations=[{"rule_id": "L002", "level": "warning", "message": "remaining", "file": "a.yml"}],
        )
        assert scan.scan_type == "fix"
        assert scan.fixed_count == 5
        assert scan.total_violations == 1

    async def test_ingest_empty_violations(self, db_session: AsyncSession) -> None:
        """Ingesting with no violations still creates a valid record."""
        scan = await scan_service.ingest_scan(
            db_session,
            project_name="clean-project",
            violations=[],
        )
        assert scan.total_violations == 0
        assert scan.error_count == 0

    async def test_ingest_dedup_by_request_id(self, db_session: AsyncSession) -> None:
        """Duplicate ingest with same request_id returns the existing record."""
        rid = str(uuid.uuid4())
        first = await scan_service.ingest_scan(
            db_session,
            project_name="dedup-test",
            violations=[{"rule_id": "L001", "level": "error", "message": "x", "file": "a.yml"}],
            request_id=rid,
        )
        second = await scan_service.ingest_scan(
            db_session,
            project_name="dedup-test",
            violations=[{"rule_id": "L001", "level": "error", "message": "x", "file": "a.yml"}],
            request_id=rid,
        )
        assert first.id == second.id

    async def test_ingest_different_request_ids_creates_two(self, db_session: AsyncSession) -> None:
        """Different request_ids create separate scan records."""
        first = await scan_service.ingest_scan(
            db_session,
            project_name="multi-test",
            violations=[],
            request_id=str(uuid.uuid4()),
        )
        # Sleep workaround: insert enough separation to bypass time-based dedup
        import asyncio

        await asyncio.sleep(6)
        second = await scan_service.ingest_scan(
            db_session,
            project_name="multi-test",
            violations=[],
            request_id=str(uuid.uuid4()),
        )
        assert first.id != second.id

    async def test_ingest_counts_hint_level(self, db_session: AsyncSession) -> None:
        """Levels other than error/warning count as hints."""
        scan = await scan_service.ingest_scan(
            db_session,
            project_name="hints",
            violations=[
                {"rule_id": "L099", "level": "hint", "message": "style", "file": "a.yml"},
                {"rule_id": "L098", "level": "info", "message": "fyi", "file": "b.yml"},
            ],
        )
        assert scan.hint_count == 2
        assert scan.error_count == 0
        assert scan.warning_count == 0


class TestListScans:
    """Tests for the list_scans service function."""

    async def test_list_empty(self, db_session: AsyncSession) -> None:
        """Empty database returns an empty list with total 0."""
        scans, total = await scan_service.list_scans(db_session)
        assert scans == []
        assert total == 0

    async def test_list_returns_ingested(self, db_session: AsyncSession) -> None:
        """Ingested scans appear in the list."""
        await scan_service.ingest_scan(
            db_session,
            project_name="project-a",
            violations=[],
            request_id=str(uuid.uuid4()),
        )
        import asyncio

        await asyncio.sleep(6)
        await scan_service.ingest_scan(
            db_session,
            project_name="project-b",
            violations=[],
            request_id=str(uuid.uuid4()),
        )
        scans, total = await scan_service.list_scans(db_session)
        assert total == 2
        assert len(scans) == 2

    async def test_list_filter_by_project(self, db_session: AsyncSession) -> None:
        """Filtering by project name narrows results."""
        await scan_service.ingest_scan(
            db_session,
            project_name="alpha-project",
            violations=[],
            request_id=str(uuid.uuid4()),
        )
        import asyncio

        await asyncio.sleep(6)
        await scan_service.ingest_scan(
            db_session,
            project_name="beta-project",
            violations=[],
            request_id=str(uuid.uuid4()),
        )
        scans, total = await scan_service.list_scans(db_session, project="alpha")
        assert total == 1
        assert scans[0].project_name == "alpha-project"

    async def test_list_pagination(self, db_session: AsyncSession) -> None:
        """Pagination limits results per page."""
        import asyncio

        for i in range(3):
            await scan_service.ingest_scan(
                db_session,
                project_name=f"page-test-{i}",
                violations=[],
                request_id=str(uuid.uuid4()),
            )
            if i < 2:
                await asyncio.sleep(6)

        scans, total = await scan_service.list_scans(db_session, page=1, page_size=2)
        assert total == 3
        assert len(scans) == 2


class TestGetScan:
    """Tests for the get_scan service function."""

    async def test_get_existing(self, db_session: AsyncSession) -> None:
        """Fetching an existing scan returns it with violations loaded."""
        ingested = await scan_service.ingest_scan(
            db_session,
            project_name="get-test",
            violations=[{"rule_id": "L001", "level": "error", "message": "x", "file": "a.yml"}],
        )
        fetched = await scan_service.get_scan(db_session, ingested.id)
        assert fetched is not None
        assert fetched.id == ingested.id
        assert len(fetched.violations) == 1

    async def test_get_nonexistent_returns_none(self, db_session: AsyncSession) -> None:
        """Fetching a non-existent scan_id returns None."""
        result = await scan_service.get_scan(db_session, "nonexistent-id")
        assert result is None


class TestDeleteScan:
    """Tests for the delete_scan service function."""

    async def test_delete_existing(self, db_session: AsyncSession) -> None:
        """Deleting an existing scan returns True."""
        scan = await scan_service.ingest_scan(
            db_session,
            project_name="delete-test",
            violations=[],
        )
        assert await scan_service.delete_scan(db_session, scan.id) is True

    async def test_delete_nonexistent_returns_false(self, db_session: AsyncSession) -> None:
        """Deleting a non-existent scan_id returns False."""
        assert await scan_service.delete_scan(db_session, "no-such-scan") is False

    async def test_delete_cascades_violations(self, db_session: AsyncSession) -> None:
        """Deleting a scan also removes its violations."""
        scan = await scan_service.ingest_scan(
            db_session,
            project_name="cascade-test",
            violations=[{"rule_id": "L001", "level": "error", "message": "x", "file": "a.yml"}],
        )
        assert await scan_service.delete_scan(db_session, scan.id) is True
        refetched = await scan_service.get_scan(db_session, scan.id)
        assert refetched is None
