"""Unit tests for the gateway gRPC Reporting servicer."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from apme.v1 import common_pb2, engine_pb2, reporting_pb2
from apme_gateway.db import get_session
from apme_gateway.db import queries as q
from apme_gateway.db.models import Notification, Project, Scan, Session
from apme_gateway.grpc_reporting.servicer import ReportingServicer

pytestmark = pytest.mark.usefixtures("gateway_db")


def _mock_context() -> MagicMock:
    """Build a mock gRPC servicer context.

    Returns:
        MagicMock with async abort.
    """
    ctx = MagicMock()
    ctx.abort = AsyncMock()
    return ctx


async def test_report_fix_completed_persists_scan() -> None:
    """FixCompletedEvent is persisted to the database."""
    servicer = ReportingServicer()
    event = reporting_pb2.FixCompletedEvent(
        scan_id="scan-1",
        session_id="sess-1",
        project_path="/proj",
        source="cli",
    )
    ctx = _mock_context()
    result = await servicer.ReportFixCompleted(event, ctx)
    assert isinstance(result, reporting_pb2.ReportAck)

    async with get_session() as db:
        scan = await q.get_scan(db, "scan-1")
    assert scan is not None
    assert scan.scan_type == "remediate"
    assert scan.session_id == "sess-1"


async def test_report_fix_creates_session() -> None:
    """Session row is created on first event."""
    servicer = ReportingServicer()
    event = reporting_pb2.FixCompletedEvent(
        scan_id="s1",
        session_id="sess-new",
        project_path="/new",
        source="ci",
    )
    await servicer.ReportFixCompleted(event, _mock_context())

    async with get_session() as db:
        sess = await q.get_session(db, "sess-new")
    assert sess is not None
    assert sess.project_path == "/new"


async def test_report_fix_with_violations() -> None:
    """Violations in the event are persisted."""
    servicer = ReportingServicer()
    viol = common_pb2.Violation(
        rule_id="L001",
        severity=common_pb2.SEVERITY_ERROR,
        message="bad task",
        file="a.yml",
        line=10,
    )
    event = reporting_pb2.FixCompletedEvent(
        scan_id="s1",
        session_id="sess-1",
        project_path="/p",
        remaining_violations=[viol],
    )
    await servicer.ReportFixCompleted(event, _mock_context())

    async with get_session() as db:
        scan = await q.get_scan(db, "s1")
    assert scan is not None
    assert len(scan.violations) == 1
    assert scan.violations[0].rule_id == "L001"


async def test_report_fix_persists_structured_audit_metadata() -> None:
    """Audit metadata JSON strings are decoded once before Gateway persistence."""
    import json

    servicer = ReportingServicer()
    payload = [{"name": "my_var", "source": "play", "task": "tasks[0]"}]
    viol = common_pb2.Violation(
        rule_id="R402",
        severity=common_pb2.SEVERITY_INFO,
        message="audit",
        file="a.yml",
        line=1,
        metadata={"variables_used": json.dumps(payload)},
    )
    event = reporting_pb2.FixCompletedEvent(
        scan_id="audit-scan",
        session_id="sess-1",
        project_path="/p",
        remaining_violations=[viol],
    )
    await servicer.ReportFixCompleted(event, _mock_context())

    async with get_session() as db:
        scan = await q.get_scan(db, "audit-scan")
    assert scan is not None
    assert len(scan.violations) == 1
    stored = json.loads(scan.violations[0].audit_metadata)
    assert stored["variables_used"] == payload


async def test_report_fix_resanitizes_variable_set_before_persistence() -> None:
    """Gateway persistence scrubs cleartext values from audit metadata blobs."""
    import json

    servicer = ReportingServicer()
    payload = [{"name": "db_password", "value": "s3cret", "source": "play"}]
    viol = common_pb2.Violation(
        rule_id="R404",
        severity=common_pb2.SEVERITY_INFO,
        message="audit",
        file="a.yml",
        line=1,
        metadata={"variable_set": json.dumps(payload)},
    )
    event = reporting_pb2.FixCompletedEvent(
        scan_id="audit-r404",
        session_id="sess-1",
        project_path="/p",
        remaining_violations=[viol],
    )
    await servicer.ReportFixCompleted(event, _mock_context())

    async with get_session() as db:
        scan = await q.get_scan(db, "audit-r404")
    assert scan is not None
    assert len(scan.violations) == 1
    stored = json.loads(scan.violations[0].audit_metadata)
    assert stored["variable_set"][0]["name"] == "[REDACTED]"
    assert stored["variable_set"][0]["value"] == "[REDACTED]"


async def test_report_fix_with_logs() -> None:
    """Pipeline logs in the event are persisted."""
    servicer = ReportingServicer()
    log = common_pb2.ProgressUpdate(
        message="scanning",
        phase="engine",
        progress=0.5,
        level=2,
    )
    event = reporting_pb2.FixCompletedEvent(
        scan_id="s1",
        session_id="sess-1",
        project_path="/p",
        logs=[log],
    )
    await servicer.ReportFixCompleted(event, _mock_context())

    async with get_session() as db:
        logs = await q.get_scan_logs(db, "s1")
    assert len(logs) == 1
    assert logs[0].phase == "engine"


async def test_report_fix_completed_persists() -> None:
    """Remediate event is persisted with scan_type='remediate'."""
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
    assert scan.scan_type == "remediate"
    assert len(scan.proposals) == 1
    assert scan.proposals[0].status == "approved"


async def test_report_fix_updates_session_last_seen() -> None:
    """Second event updates session last_seen timestamp."""
    servicer = ReportingServicer()
    ctx = _mock_context()

    ev1 = reporting_pb2.FixCompletedEvent(scan_id="s1", session_id="sess", project_path="/p")
    await servicer.ReportFixCompleted(ev1, ctx)

    async with get_session() as db:
        sess1 = await q.get_session(db, "sess")
    assert sess1 is not None
    ts1 = sess1.last_seen

    ev2 = reporting_pb2.FixCompletedEvent(scan_id="s2", session_id="sess", project_path="/p")
    await servicer.ReportFixCompleted(ev2, ctx)

    async with get_session() as db:
        sess2 = await q.get_session(db, "sess")
    assert sess2 is not None
    assert sess2.last_seen >= ts1


async def test_report_fix_with_summary() -> None:
    """Summary fields are extracted from the event."""
    servicer = ReportingServicer()
    summary = common_pb2.ScanSummary(total=10, auto_fixable=3, ai_candidate=4, manual_review=3)
    event = reporting_pb2.FixCompletedEvent(
        scan_id="s1",
        session_id="sess-1",
        project_path="/p",
        summary=summary,
    )
    await servicer.ReportFixCompleted(event, _mock_context())

    async with get_session() as db:
        scan = await q.get_scan(db, "s1")
    assert scan is not None
    assert scan.total_violations == 10
    assert scan.auto_fixable == 3
    assert scan.ai_candidate == 4
    assert scan.manual_review == 3


async def test_report_fix_creates_scan_complete_notification() -> None:
    """Successful persistence also writes a scan_complete notification."""
    servicer = ReportingServicer()
    event = reporting_pb2.FixCompletedEvent(
        scan_id="scan-notif",
        session_id="sess-notif",
        project_path="/proj",
        source="cli",
        summary=common_pb2.ScanSummary(total=4, auto_fixable=1, ai_candidate=1, manual_review=2),
        report=engine_pb2.FixReport(fixed=4),
    )
    ctx = _mock_context()
    await servicer.ReportFixCompleted(event, ctx)

    async with get_session() as db:
        rows = list(
            (await db.execute(select(Notification).where(Notification.scan_id == "scan-notif"))).scalars().all()
        )
    assert len(rows) == 1
    assert rows[0].type == "scan_complete"
    assert "4 findings resolved" in rows[0].message
    ctx.abort.assert_not_awaited()


async def test_report_fix_notification_uses_project_name() -> None:
    """Stub scans with a project FK resolve the display name by project id."""
    async with get_session() as db:
        db.add(
            Project(
                id="proj-ui",
                name="Playground App",
                repo_url="https://github.com/test/playground.git",
                branch="main",
                created_at="2026-09-04T00:00:00Z",
                health_score=50,
            )
        )
        db.add(
            Session(
                session_id="sess-ui",
                project_path="/tmp/playground",
                first_seen="t0",
                last_seen="t1",
            )
        )
        db.add(
            Scan(
                scan_id="scan-ui",
                session_id="sess-ui",
                project_id="proj-ui",
                project_path="/tmp/playground",
                source="gateway",
                trigger="ui",
                created_at="2026-09-04T00:00:00Z",
                scan_type="remediate",
                total_violations=0,
            )
        )
        await db.commit()

    servicer = ReportingServicer()
    event = reporting_pb2.FixCompletedEvent(
        scan_id="scan-ui",
        session_id="sess-ui",
        project_path="/tmp/playground",
        source="gateway",
        summary=common_pb2.ScanSummary(total=3, auto_fixable=1, ai_candidate=1, manual_review=1),
        report=engine_pb2.FixReport(fixed=2),
    )
    ctx = _mock_context()
    await servicer.ReportFixCompleted(event, ctx)

    async with get_session() as db:
        rows = list((await db.execute(select(Notification).where(Notification.scan_id == "scan-ui"))).scalars().all())
        scan = await q.get_scan(db, "scan-ui")
    assert len(rows) == 1
    assert "Playground App" in rows[0].message
    assert rows[0].title == "Remediation Complete"
    assert rows[0].project_id == "proj-ui"
    assert scan is not None
    ctx.abort.assert_not_awaited()


async def test_report_fix_notification_session_failure_does_not_abort() -> None:
    """Opening the notification session must not be reported as persistence failure."""
    servicer = ReportingServicer()
    event = reporting_pb2.FixCompletedEvent(
        scan_id="scan-sess-fail",
        session_id="sess-sess-fail",
        project_path="/proj",
        source="cli",
    )
    ctx = _mock_context()
    real_get_session = get_session
    persist_calls = 0

    def _flaky_session() -> object:
        nonlocal persist_calls
        persist_calls += 1
        if persist_calls == 1:
            return real_get_session()
        msg = "notification session unavailable"
        raise RuntimeError(msg)

    with patch("apme_gateway.grpc_reporting.servicer.get_session", side_effect=_flaky_session):
        result = await servicer.ReportFixCompleted(event, ctx)

    assert isinstance(result, reporting_pb2.ReportAck)
    ctx.abort.assert_not_awaited()
    async with get_session() as db:
        scan = await q.get_scan(db, "scan-sess-fail")
    assert scan is not None


async def test_report_fix_notification_failure_does_not_abort() -> None:
    """Notification errors must not be reported as persistence failures."""
    servicer = ReportingServicer()
    event = reporting_pb2.FixCompletedEvent(
        scan_id="scan-notif-fail",
        session_id="sess-notif-fail",
        project_path="/proj",
        source="cli",
    )
    ctx = _mock_context()
    with patch(
        "apme_gateway.notifications.generate_notifications",
        side_effect=RuntimeError("simulated notification failure"),
    ):
        result = await servicer.ReportFixCompleted(event, ctx)

    assert isinstance(result, reporting_pb2.ReportAck)
    ctx.abort.assert_not_awaited()
    async with get_session() as db:
        scan = await q.get_scan(db, "scan-notif-fail")
    assert scan is not None
