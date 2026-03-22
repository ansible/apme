"""Unit tests for the gateway gRPC Reporting servicer."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from apme.v1 import common_pb2, reporting_pb2
from apme_gateway.db import close_db, get_session, init_db
from apme_gateway.db import queries as q
from apme_gateway.grpc_reporting.servicer import ReportingServicer


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


def _mock_context() -> MagicMock:
    """Build a mock gRPC servicer context.

    Returns:
        MagicMock with async abort.
    """
    ctx = MagicMock()
    ctx.abort = AsyncMock()
    return ctx


async def test_report_scan_completed_persists() -> None:
    """Scan event is persisted to the database."""
    servicer = ReportingServicer()
    event = reporting_pb2.ScanCompletedEvent(
        scan_id="scan-1",
        session_id="sess-1",
        project_path="/proj",
        source="cli",
    )
    ctx = _mock_context()
    result = await servicer.ReportScanCompleted(event, ctx)
    assert isinstance(result, reporting_pb2.ReportAck)

    async with get_session() as db:
        scan = await q.get_scan(db, "scan-1")
    assert scan is not None
    assert scan.scan_type == "scan"
    assert scan.session_id == "sess-1"


async def test_report_scan_creates_session() -> None:
    """Session row is created on first event."""
    servicer = ReportingServicer()
    event = reporting_pb2.ScanCompletedEvent(
        scan_id="s1",
        session_id="sess-new",
        project_path="/new",
        source="ci",
    )
    await servicer.ReportScanCompleted(event, _mock_context())

    async with get_session() as db:
        sess = await q.get_session(db, "sess-new")
    assert sess is not None
    assert sess.project_path == "/new"


async def test_report_scan_with_violations() -> None:
    """Violations in the event are persisted."""
    servicer = ReportingServicer()
    viol = common_pb2.Violation(
        rule_id="L001",
        level="error",
        message="bad task",
        file="a.yml",
        line=10,
    )
    event = reporting_pb2.ScanCompletedEvent(
        scan_id="s1",
        session_id="sess-1",
        project_path="/p",
        violations=[viol],
    )
    await servicer.ReportScanCompleted(event, _mock_context())

    async with get_session() as db:
        scan = await q.get_scan(db, "s1")
    assert scan is not None
    assert len(scan.violations) == 1
    assert scan.violations[0].rule_id == "L001"


async def test_report_scan_with_logs() -> None:
    """Pipeline logs in the event are persisted."""
    servicer = ReportingServicer()
    log = common_pb2.ProgressUpdate(
        message="scanning",
        phase="engine",
        progress=0.5,
        level=2,
    )
    event = reporting_pb2.ScanCompletedEvent(
        scan_id="s1",
        session_id="sess-1",
        project_path="/p",
        logs=[log],
    )
    await servicer.ReportScanCompleted(event, _mock_context())

    async with get_session() as db:
        logs = await q.get_scan_logs(db, "s1")
    assert len(logs) == 1
    assert logs[0].phase == "engine"


async def test_report_fix_completed_persists() -> None:
    """Fix event is persisted with type='fix'."""
    servicer = ReportingServicer()
    proposal = reporting_pb2.ProposalOutcome(
        proposal_id="p1",
        rule_id="L001",
        file="a.yml",
        tier=2,
        confidence=0.9,
        status="approved",
    )
    event = reporting_pb2.FixCompletedEvent(
        scan_id="fix-1",
        session_id="sess-1",
        project_path="/proj",
        source="cli",
        proposals=[proposal],
    )
    await servicer.ReportFixCompleted(event, _mock_context())

    async with get_session() as db:
        scan = await q.get_scan(db, "fix-1")
    assert scan is not None
    assert scan.scan_type == "fix"
    assert len(scan.proposals) == 1
    assert scan.proposals[0].status == "approved"


async def test_report_scan_updates_session_last_seen() -> None:
    """Second event updates session last_seen timestamp."""
    servicer = ReportingServicer()
    ctx = _mock_context()

    ev1 = reporting_pb2.ScanCompletedEvent(scan_id="s1", session_id="sess", project_path="/p")
    await servicer.ReportScanCompleted(ev1, ctx)

    async with get_session() as db:
        sess1 = await q.get_session(db, "sess")
    assert sess1 is not None
    ts1 = sess1.last_seen

    ev2 = reporting_pb2.ScanCompletedEvent(scan_id="s2", session_id="sess", project_path="/p")
    await servicer.ReportScanCompleted(ev2, ctx)

    async with get_session() as db:
        sess2 = await q.get_session(db, "sess")
    assert sess2 is not None
    assert sess2.last_seen >= ts1


async def test_report_scan_with_summary() -> None:
    """Summary fields are extracted from the event."""
    servicer = ReportingServicer()
    summary = common_pb2.ScanSummary(total=10, auto_fixable=3, ai_candidate=4, manual_review=3)
    event = reporting_pb2.ScanCompletedEvent(
        scan_id="s1",
        session_id="sess-1",
        project_path="/p",
        summary=summary,
    )
    await servicer.ReportScanCompleted(event, _mock_context())

    async with get_session() as db:
        scan = await q.get_scan(db, "s1")
    assert scan is not None
    assert scan.total_violations == 10
    assert scan.auto_fixable == 3
    assert scan.ai_candidate == 4
    assert scan.manual_review == 3
