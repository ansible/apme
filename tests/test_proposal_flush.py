"""Unit tests for ADR-062 proposal flush and historical rebuild."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from typing import cast

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from apme_gateway.app import create_app
from apme_gateway.db import get_session
from apme_gateway.db.models import Project, Proposal, ProposalRuleAnalytics, Scan, Session, Violation
from apme_gateway.proposals.flush import flush_proposals_for_project, replace_scan_proposals
from apme_gateway.proposals.grouping import GroupedProposal, group_violations

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


class TestLineEndMapping:
    """line_end flows from engine proposals to ProposalDetail (finding #50)."""

    def test_grouped_line_end_reaches_detail(self) -> None:
        """Grouped views carry line_end into ProposalDetail construction."""
        from apme_gateway.api.schemas import ProposalDetail
        from apme_gateway.proposals.flush import proposal_to_detail_dict

        grouped = GroupedProposal(
            proposal_id="t1-abc",
            rule_id="L007",
            rule_ids=("L007",),
            violation_ids=(),
            file="a.yml",
            path="",
            line_start=10,
            line_end=14,
            tier=1,
            source="deterministic",
            gate="tier1",
        )
        detail = ProposalDetail.model_validate(proposal_to_detail_dict(grouped))
        assert detail.line_start == 10
        assert detail.line_end == 14

    def test_operation_proposal_defaults_line_end_zero(self) -> None:
        """Registry proposals default line_end to 0 when unknown."""
        from apme_gateway.operation_types import Proposal as OperationProposal

        proposal = OperationProposal(id="p-1", rule_id="L007", file="a.yml")
        assert proposal.line_start == 0
        assert proposal.line_end == 0

    def test_orm_branch_includes_line_end(self) -> None:
        """DB-backed rows serialize line_end like the duck-typed branch."""
        from apme_gateway.api.schemas import ProposalDetail
        from apme_gateway.db.models import Proposal
        from apme_gateway.proposals.flush import proposal_to_detail_dict

        row = Proposal(
            id=1,
            scan_id="scan-x",
            proposal_id="prop-tier1-abc",
            rule_id="L007",
            file="a.yml",
            tier=1,
            confidence=0.9,
            status="pending",
            path="a.yml::t[0]",
            source="deterministic",
            gate="tier1",
            rule_ids_json='["L007"]',
            violation_ids_json="[1]",
            line_start=10,
            line_end=14,
            diff_hunk="",
            explanation="",
            suggestion="",
            engine_proposal_id=None,
            draft=0,
        )
        payload = proposal_to_detail_dict(row)
        assert payload["line_start"] == 10
        assert payload["line_end"] == 14
        detail = ProposalDetail.model_validate(payload)
        assert detail.line_start == 10
        assert detail.line_end == 14


def test_proposal_detail_dict_coerces_string_lines() -> None:
    """Duck-typed string spans coerce instead of raising."""
    from types import SimpleNamespace

    from apme_gateway.proposals.flush import proposal_to_detail_dict

    good = SimpleNamespace(
        proposal_id="p-1",
        rule_id="L007",
        file="a.yml",
        tier="1",
        confidence="0.9",
        status="pending",
        path="",
        node_type="",
        source="deterministic",
        gate="tier1",
        rule_ids=("L007",),
        violation_ids=(),
        line_start="10",
        line_end="12.0",
        diff_hunk="",
        explanation="",
        suggestion="",
        engine_proposal_id=None,
        draft=False,
    )
    payload = proposal_to_detail_dict(good)
    assert payload["line_start"] == 10
    assert payload["line_end"] == 12
    assert payload["tier"] == 1
    assert payload["confidence"] == 0.9

    bad = SimpleNamespace(
        proposal_id="p-2",
        rule_id="L007",
        file="a.yml",
        tier="high",
        confidence="high",
        status="pending",
        path="",
        node_type="",
        source="deterministic",
        gate="tier1",
        rule_ids=("L007",),
        violation_ids=(),
        line_start="high",
        line_end="abc",
        diff_hunk="",
        explanation="",
        suggestion="",
        engine_proposal_id=None,
        draft=False,
    )
    fallback = proposal_to_detail_dict(bad)
    assert fallback["line_start"] == 0
    assert fallback["line_end"] == 0
    assert fallback["tier"] == 0
    assert fallback["confidence"] == 0.0


async def test_bridge_matches_stub_when_rebuild_line_end_zero() -> None:
    """Rebuild groups without line_end still bridge to live stubs with spans."""
    from apme_gateway.proposals.draft import upsert_live_proposal_stubs

    await _seed_project_scan(scan_id="bridge-zero-end")
    async with get_session() as db:
        await upsert_live_proposal_stubs(
            db,
            scan_id="bridge-zero-end",
            project_id=None,
            proposals=[
                {
                    "id": "eng-span",
                    "rule_id": "L001",
                    "file": "tasks/main.yml",
                    "tier": 2,
                    "status": "approved",
                    "source": "ai",
                    "line_start": 10,
                    "line_end": 14,
                }
            ],
        )
        await db.commit()
        prop = (await db.execute(select(Proposal).where(Proposal.scan_id == "bridge-zero-end"))).scalar_one()
        prop.analytics_flushed = 1
        await db.commit()

        await replace_scan_proposals(
            db,
            scan_id="bridge-zero-end",
            proposals=[
                GroupedProposal(
                    proposal_id="prop-ai-span",
                    rule_id="L001",
                    rule_ids=("L001",),
                    violation_ids=(1,),
                    file="tasks/main.yml",
                    path="tasks/main.yml::t[0]",
                    line_start=10,
                    line_end=0,
                    tier=2,
                    source="ai",
                    gate="ai",
                    status="pending",
                )
            ],
        )
        await db.commit()
        rebuilt = (await db.execute(select(Proposal).where(Proposal.scan_id == "bridge-zero-end"))).scalar_one()
        assert rebuilt.engine_proposal_id == "eng-span"
        assert rebuilt.status == "approved"
        assert rebuilt.analytics_flushed == 1


async def test_bridge_matches_stub_when_rebuild_line_end_differs() -> None:
    """Rebuild groups with a different nonzero line_end still bridge via line-start key."""
    from apme_gateway.proposals.draft import upsert_live_proposal_stubs

    await _seed_project_scan(scan_id="bridge-diff-end")
    async with get_session() as db:
        await upsert_live_proposal_stubs(
            db,
            scan_id="bridge-diff-end",
            project_id=None,
            proposals=[
                {
                    "id": "eng-span",
                    "rule_id": "L001",
                    "file": "tasks/main.yml",
                    "tier": 2,
                    "status": "approved",
                    "source": "ai",
                    "line_start": 10,
                    "line_end": 14,
                }
            ],
        )
        await db.commit()
        prop = (await db.execute(select(Proposal).where(Proposal.scan_id == "bridge-diff-end"))).scalar_one()
        prop.analytics_flushed = 1
        await db.commit()

        await replace_scan_proposals(
            db,
            scan_id="bridge-diff-end",
            proposals=[
                GroupedProposal(
                    proposal_id="prop-ai-span",
                    rule_id="L001",
                    rule_ids=("L001",),
                    violation_ids=(1,),
                    file="tasks/main.yml",
                    path="tasks/main.yml::t[0]",
                    line_start=10,
                    line_end=18,
                    tier=2,
                    source="ai",
                    gate="ai",
                    status="pending",
                )
            ],
        )
        await db.commit()
        rebuilt = (await db.execute(select(Proposal).where(Proposal.scan_id == "bridge-diff-end"))).scalar_one()
        assert rebuilt.engine_proposal_id == "eng-span"
        assert rebuilt.status == "approved"
        assert rebuilt.analytics_flushed == 1


async def test_bridge_distinguishes_line_end() -> None:
    """Same file/rule/line_start with different line_end must not collide."""
    from apme_gateway.proposals.draft import upsert_live_proposal_stubs

    await _seed_project_scan(scan_id="bridge-line-end")
    async with get_session() as db:
        await upsert_live_proposal_stubs(
            db,
            scan_id="bridge-line-end",
            project_id=None,
            proposals=[
                {
                    "id": "eng-a",
                    "rule_id": "L001",
                    "file": "same.yml",
                    "tier": 2,
                    "status": "approved",
                    "source": "ai",
                    "line_start": 1,
                    "line_end": 10,
                },
                {
                    "id": "eng-b",
                    "rule_id": "L001",
                    "file": "same.yml",
                    "tier": 2,
                    "status": "declined",
                    "source": "ai",
                    "line_start": 1,
                    "line_end": 20,
                },
            ],
        )
        await db.commit()
        for prop in (await db.execute(select(Proposal).where(Proposal.scan_id == "bridge-line-end"))).scalars().all():
            prop.analytics_flushed = 1
        await db.commit()

        await replace_scan_proposals(
            db,
            scan_id="bridge-line-end",
            proposals=[
                GroupedProposal(
                    proposal_id="prop-ai-a",
                    rule_id="L001",
                    rule_ids=("L001",),
                    violation_ids=(1,),
                    file="same.yml",
                    path="same.yml::t[0]",
                    line_start=1,
                    line_end=10,
                    tier=2,
                    source="ai",
                    gate="ai",
                    status="pending",
                ),
                GroupedProposal(
                    proposal_id="prop-ai-b",
                    rule_id="L001",
                    rule_ids=("L001",),
                    violation_ids=(2,),
                    file="same.yml",
                    path="same.yml::t[1]",
                    line_start=1,
                    line_end=20,
                    tier=2,
                    source="ai",
                    gate="ai",
                    status="pending",
                ),
            ],
        )
        await db.commit()
        by_end = {
            p.line_end: p
            for p in (await db.execute(select(Proposal).where(Proposal.scan_id == "bridge-line-end"))).scalars().all()
        }
        assert by_end[10].engine_proposal_id == "eng-a"
        assert by_end[10].status == "approved"
        assert by_end[20].engine_proposal_id == "eng-b"
        assert by_end[20].status == "declined"


async def test_replace_tolerates_string_line_end() -> None:
    """Grouped string line_end coerces instead of crashing replace."""
    from apme_gateway.db.models import Proposal as ProposalRow

    await _seed_project_scan(scan_id="replace-str-line-end")
    async with get_session() as db:
        prop = GroupedProposal(
            proposal_id="prop-str",
            rule_id="L001",
            rule_ids=("L001",),
            violation_ids=(),
            file="a.yml",
            path="",
            line_start=1,
            tier=1,
            source="deterministic",
            gate="tier1",
            status="pending",
        )
        # GroupedProposal is frozen — rebuild (not plain assignment) the way
        # JSON-ish callers do.
        prop = replace(prop, line_end=cast(int, "12.0"))
        await replace_scan_proposals(db, scan_id="replace-str-line-end", proposals=[prop])
        await db.commit()
        row = (await db.execute(select(ProposalRow).where(ProposalRow.scan_id == "replace-str-line-end"))).scalar_one()
        assert row.line_end == 12


def test_proposal_detail_dict_filters_invalid_violation_ids() -> None:
    """Duck-typed violation_ids with garbage filter instead of raising."""
    from types import SimpleNamespace

    from apme_gateway.proposals.flush import proposal_to_detail_dict

    obj = SimpleNamespace(
        proposal_id="p-1",
        rule_id="L007",
        file="a.yml",
        tier=1,
        confidence=0.9,
        status="pending",
        path="",
        node_type="",
        source="deterministic",
        gate="tier1",
        rule_ids=("L007",),
        violation_ids=("abc", True, 12.9, 3, "4"),
        line_start=1,
        line_end=2,
        diff_hunk="",
        explanation="",
        suggestion="",
        engine_proposal_id=None,
        draft=False,
    )
    payload = proposal_to_detail_dict(obj)
    assert payload["violation_ids"] == [3, 4]


def test_proposal_detail_dict_orm_branch_filters_invalid_violation_ids() -> None:
    """ORM violation_ids_json with garbage filters instead of raising."""
    from apme_gateway.db.models import Proposal
    from apme_gateway.proposals.flush import proposal_to_detail_dict

    row = Proposal(
        id=1,
        scan_id="scan-x",
        proposal_id="prop-tier1-abc",
        rule_id="L007",
        file="a.yml",
        tier=1,
        confidence=0.9,
        status="pending",
        path="a.yml::t[0]",
        source="deterministic",
        gate="tier1",
        rule_ids_json='["L007"]',
        violation_ids_json='["abc", true, 12.9, 3, "4"]',
        line_start=1,
        line_end=2,
        diff_hunk="",
        explanation="",
        suggestion="",
        engine_proposal_id=None,
        draft=0,
    )
    payload = proposal_to_detail_dict(row)
    assert payload["violation_ids"] == [3, 4]


def test_replace_tolerates_string_draft_flag() -> None:
    """String draft flags coerce to bool instead of crashing detail render."""
    from types import SimpleNamespace

    from apme_gateway.proposals.flush import proposal_to_detail_dict

    obj = SimpleNamespace(
        proposal_id="p-3",
        rule_id="L007",
        file="a.yml",
        tier="1",
        confidence="0.9",
        status="pending",
        path="",
        node_type="",
        source="deterministic",
        gate="tier1",
        rule_ids=("L007",),
        violation_ids=(1,),
        line_start=1,
        line_end=2,
        diff_hunk="",
        explanation="",
        suggestion="",
        engine_proposal_id=None,
        draft="1",
    )
    payload = proposal_to_detail_dict(obj)
    assert payload["draft"] is True
    assert payload["violation_ids"] == [1]


async def test_replace_tolerates_string_draft_in_store() -> None:
    """String draft/bridge flags coerce instead of crashing replace."""
    from apme_gateway.db.models import Proposal as ProposalRow

    await _seed_project_scan(scan_id="replace-str-draft")
    async with get_session() as db:
        good = GroupedProposal(
            proposal_id="prop-draft-str",
            rule_id="L001",
            rule_ids=("L001",),
            violation_ids=(),
            file="a.yml",
            path="",
            line_start=1,
            tier=1,
            source="deterministic",
            gate="tier1",
            status="pending",
        )
        # GroupedProposal is frozen and has no draft field — bypass both the
        # way JSON-ish callers do (plain assignment raises FrozenInstanceError
        # and replace() rejects the unknown field).
        object.__setattr__(good, "draft", "1.0")
        await replace_scan_proposals(db, scan_id="replace-str-draft", proposals=[good])
        await db.commit()
        row = (await db.execute(select(ProposalRow).where(ProposalRow.scan_id == "replace-str-draft"))).scalar_one()
        assert row.draft == 1

    async with get_session() as db:
        from apme_gateway.db.models import Scan as ScanRow

        db.add(
            ScanRow(
                scan_id="replace-bad-draft",
                session_id="sess-1",
                project_id="proj-1",
                project_path="/proj",
                source="cli",
                created_at="2026-01-01T00:00:00Z",
                scan_type="remediate",
                total_violations=0,
            )
        )
        bad = GroupedProposal(
            proposal_id="prop-draft-bad",
            rule_id="L001",
            rule_ids=("L001",),
            violation_ids=(),
            file="a.yml",
            path="",
            line_start=1,
            tier=1,
            source="deterministic",
            gate="tier1",
            status="pending",
        )
        # Same frozen/no-field bypass as above — plain assignment cannot
        # attach a non-field to a frozen dataclass.
        object.__setattr__(bad, "draft", "high")
        await replace_scan_proposals(db, scan_id="replace-bad-draft", proposals=[bad])
        await db.commit()
        row = (await db.execute(select(ProposalRow).where(ProposalRow.scan_id == "replace-bad-draft"))).scalar_one()
        assert row.draft == 0
