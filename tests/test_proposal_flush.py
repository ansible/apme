"""Unit tests for ADR-062 proposal flush and historical rebuild."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from apme_gateway.app import create_app
from apme_gateway.db import close_db, get_session, init_db
from apme_gateway.db.models import Project, Proposal, ProposalRuleAnalytics, Scan, Session, Violation
from apme_gateway.proposals.flush import flush_proposals_for_project, replace_scan_proposals
from apme_gateway.proposals.grouping import GroupedProposal, group_violations


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


async def _seed_project_scan(
    *,
    project_id: str = "proj-1",
    scan_id: str = "scan-1",
    session_id: str = "sess-1",
) -> None:
    """Insert project, session, and scan rows.

    Args:
        project_id: Project UUID.
        scan_id: Scan UUID.
        session_id: Session hash.
    """
    async with get_session() as db:
        db.add(
            Project(
                id=project_id,
                name="demo",
                repo_url="https://example.com/demo.git",
                branch="main",
                created_at="2026-01-01T00:00:00Z",
            )
        )
        db.add(Session(session_id=session_id, project_path="/proj", first_seen="t0", last_seen="t0"))
        db.add(
            Scan(
                scan_id=scan_id,
                session_id=session_id,
                project_id=project_id,
                project_path="/proj",
                source="cli",
                created_at="2026-01-01T00:00:00Z",
                scan_type="remediate",
                total_violations=2,
            )
        )
        await db.commit()


async def test_flush_writes_analytics_and_deletes_proposals() -> None:
    """Flush rolls terminal decisions into analytics and deletes proposals."""
    await _seed_project_scan()
    async with get_session() as db:
        db.add(
            Violation(
                scan_id="scan-1",
                rule_id="L013",
                level="warning",
                message="shell",
                file="a.yml",
                path="a.yml::t[0]",
                remediation_class=1,
                fixed_yaml="command: x\n",
                original_yaml="shell: x\n",
            )
        )
        await db.flush()
        result = await db.execute(select(Violation).where(Violation.scan_id == "scan-1"))
        violations = list(result.scalars().all())
        grouped = group_violations(violations, include_diff=False)
        # Force declined so analytics get a declined_delta.
        declined = [replace(grouped[0], status="declined")]
        await replace_scan_proposals(db, scan_id="scan-1", proposals=declined)
        await db.commit()

    async with get_session() as db:
        deleted = await flush_proposals_for_project(db, "proj-1")
        await db.commit()
        assert deleted == 1

        props = list((await db.execute(select(Proposal).where(Proposal.scan_id == "scan-1"))).scalars().all())
        assert props == []

        analytics = list((await db.execute(select(ProposalRuleAnalytics))).scalars().all())
        assert len(analytics) >= 1
        assert any(a.rule_id == "L013" and a.declined_count == 1 for a in analytics)

        violation = (await db.execute(select(Violation).where(Violation.scan_id == "scan-1"))).scalar_one()
        assert violation.review_status == "deterministic_declined"


async def test_flush_skips_pending_analytics() -> None:
    """Pending proposals are deleted without incrementing analytics."""
    await _seed_project_scan()
    async with get_session() as db:
        await replace_scan_proposals(
            db,
            scan_id="scan-1",
            proposals=[
                GroupedProposal(
                    proposal_id="prop-pending",
                    rule_id="L001",
                    rule_ids=("L001",),
                    violation_ids=(),
                    file="a.yml",
                    path="a.yml::t[0]",
                    line_start=1,
                    tier=1,
                    source="deterministic",
                    gate="tier1",
                    status="pending",
                    confidence=1.0,
                    coupled=False,
                )
            ],
        )
        await db.commit()

    async with get_session() as db:
        deleted = await flush_proposals_for_project(db, "proj-1")
        await db.commit()
        assert deleted == 1
        analytics = list((await db.execute(select(ProposalRuleAnalytics))).scalars().all())
        assert analytics == []


async def test_activity_rebuilds_proposals_after_flush(client: AsyncClient) -> None:
    """GET /activity rebuilds proposals from violations when none are stored.

    Args:
        client: Async HTTP test client.
    """
    await _seed_project_scan(scan_id="hist-1")
    async with get_session() as db:
        db.add(
            Violation(
                scan_id="hist-1",
                rule_id="L013",
                level="warning",
                message="shell",
                file="tasks/main.yml",
                path="tasks/main.yml::task[0]",
                line=10,
                remediation_class=1,
                fixed_yaml="command: echo hi\n",
                original_yaml="shell: echo hi\n",
                review_status="deterministic_approved",
            )
        )
        await db.commit()

    resp = await client.get("/api/v1/activity/hist-1")
    assert resp.status_code == 200
    body = resp.json()
    # No stored proposals → rebuild from violations (ADR-062).
    assert len(body["proposals"]) == 1
    prop = body["proposals"][0]
    assert prop["rule_id"] == "L013"
    assert prop["path"] == "tasks/main.yml::task[0]"
    assert prop["source"] == "deterministic"
    assert prop["status"] == "approved"
    assert body["violations"][0]["review_status"] == "deterministic_approved"


async def test_link_scan_check_does_not_flush_working_set() -> None:
    """Check link_scan_to_project leaves prior remediate proposals intact."""
    from apme_gateway.db import queries as q

    await _seed_project_scan(scan_id="rem-1")
    async with get_session() as db:
        await replace_scan_proposals(
            db,
            scan_id="rem-1",
            proposals=[
                GroupedProposal(
                    proposal_id="prop-keep",
                    rule_id="L001",
                    rule_ids=("L001",),
                    violation_ids=(),
                    file="a.yml",
                    path="a.yml::t[0]",
                    line_start=1,
                    tier=1,
                    source="deterministic",
                    gate="tier1",
                    status="pending",
                    confidence=1.0,
                    coupled=False,
                )
            ],
        )
        db.add(
            Scan(
                scan_id="check-1",
                session_id="sess-1",
                project_path="/proj",
                source="cli",
                created_at="2026-01-01T00:00:00Z",
                scan_type="check",
                total_violations=0,
            )
        )
        await db.commit()

    async with get_session() as db:
        ok = await q.link_scan_to_project(db, "check-1", "proj-1", scan_type="check")
        assert ok is True

    async with get_session() as db:
        props = list((await db.execute(select(Proposal).where(Proposal.scan_id == "rem-1"))).scalars().all())
        assert len(props) == 1
        assert props[0].proposal_id == "prop-keep"


async def test_link_scan_check_discards_check_proposals_and_stamps() -> None:
    """Check link discards that scan's invented proposals and review_status."""
    from apme_gateway.db import queries as q
    from apme_gateway.proposals.flush import discard_scan_proposals

    await _seed_project_scan(scan_id="check-scan")
    async with get_session() as db:
        db.add(
            Violation(
                scan_id="check-scan",
                rule_id="L013",
                level="warning",
                message="shell",
                file="a.yml",
                path="a.yml::t[0]",
                remediation_class=1,
                fixed_yaml="command: x\n",
                review_status="deterministic_approved",
            )
        )
        await replace_scan_proposals(
            db,
            scan_id="check-scan",
            proposals=[
                GroupedProposal(
                    proposal_id="prop-check",
                    rule_id="L013",
                    rule_ids=("L013",),
                    violation_ids=(),
                    file="a.yml",
                    path="a.yml::t[0]",
                    line_start=1,
                    tier=1,
                    source="deterministic",
                    gate="tier1",
                    status="approved",
                    confidence=1.0,
                    coupled=False,
                    fixed_yaml="command: x\n",
                )
            ],
        )
        await db.commit()

    async with get_session() as db:
        # Simulate gateway learning this FixCompleted was a check.
        n = await discard_scan_proposals(db, "check-scan")
        await db.commit()
        assert n == 1
        props = list((await db.execute(select(Proposal).where(Proposal.scan_id == "check-scan"))).scalars().all())
        assert props == []
        v = (await db.execute(select(Violation).where(Violation.scan_id == "check-scan"))).scalar_one()
        assert v.review_status is None

    async with get_session() as db:
        # Also via link_scan_to_project(scan_type=check).
        await replace_scan_proposals(
            db,
            scan_id="check-scan",
            proposals=[
                GroupedProposal(
                    proposal_id="prop-check-2",
                    rule_id="L013",
                    rule_ids=("L013",),
                    violation_ids=(),
                    file="a.yml",
                    path="",
                    line_start=1,
                    tier=1,
                    source="deterministic",
                    gate="tier1",
                    status="approved",
                    confidence=1.0,
                    coupled=False,
                )
            ],
        )
        await db.commit()
        ok = await q.link_scan_to_project(db, "check-scan", "proj-1", scan_type="check")
        assert ok is True
        props = list((await db.execute(select(Proposal).where(Proposal.scan_id == "check-scan"))).scalars().all())
        assert props == []


async def test_flush_proposals_for_scan_is_scan_scoped() -> None:
    """Publish flush deletes only the published scan's proposals."""
    from apme_gateway.proposals.flush import flush_proposals_for_scan

    await _seed_project_scan(scan_id="pub-1")
    async with get_session() as db:
        db.add(
            Scan(
                scan_id="open-2",
                session_id="sess-1",
                project_id="proj-1",
                project_path="/proj",
                source="cli",
                created_at="2026-01-01T00:00:00Z",
                scan_type="remediate",
                total_violations=0,
            )
        )
        await replace_scan_proposals(
            db,
            scan_id="pub-1",
            proposals=[
                GroupedProposal(
                    proposal_id="prop-pub",
                    rule_id="L001",
                    rule_ids=("L001",),
                    violation_ids=(),
                    file="a.yml",
                    path="",
                    line_start=1,
                    tier=1,
                    source="deterministic",
                    gate="tier1",
                    status="approved",
                    confidence=1.0,
                    coupled=False,
                )
            ],
        )
        await replace_scan_proposals(
            db,
            scan_id="open-2",
            proposals=[
                GroupedProposal(
                    proposal_id="prop-open",
                    rule_id="L002",
                    rule_ids=("L002",),
                    violation_ids=(),
                    file="b.yml",
                    path="",
                    line_start=1,
                    tier=1,
                    source="deterministic",
                    gate="tier1",
                    status="pending",
                    confidence=1.0,
                    coupled=False,
                )
            ],
        )
        await db.commit()

    async with get_session() as db:
        deleted = await flush_proposals_for_scan(db, "pub-1", project_id="proj-1")
        await db.commit()
        assert deleted == 1
        remaining = list((await db.execute(select(Proposal))).scalars().all())
        assert len(remaining) == 1
        assert remaining[0].proposal_id == "prop-open"


async def test_ai_acceptance_prefers_analytics_after_flush() -> None:
    """After flush, /stats path via ai_acceptance reads proposal_rule_analytics."""
    from apme_gateway.db import queries as q

    await _seed_project_scan()
    async with get_session() as db:
        await replace_scan_proposals(
            db,
            scan_id="scan-1",
            proposals=[
                GroupedProposal(
                    proposal_id="prop-ai",
                    rule_id="L010",
                    rule_ids=("L010",),
                    violation_ids=(),
                    file="a.yml",
                    path="",
                    line_start=1,
                    tier=2,
                    source="ai",
                    gate="ai",
                    status="approved",
                    confidence=0.9,
                    coupled=False,
                )
            ],
        )
        await db.commit()

    async with get_session() as db:
        await flush_proposals_for_project(db, "proj-1")
        await db.commit()
        # Live proposals gone.
        assert list((await db.execute(select(Proposal))).scalars().all()) == []
        rows = await q.ai_acceptance(db)

    assert len(rows) == 1
    rule_id, approved, rejected, pending, _avg = rows[0]
    assert rule_id == "L010"
    assert approved == 1
    assert rejected == 0
    assert pending == 0
