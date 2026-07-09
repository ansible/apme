"""Unit tests for ADR-062 Phase 2 draft PATCH, gate commit, and abandon."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from apme_gateway.app import create_app
from apme_gateway.db import close_db, get_session, init_db
from apme_gateway.db.models import Project, Proposal, ProposalRuleAnalytics, Scan, Session, Violation
from apme_gateway.proposals.draft import (
    abandon_project_drafts,
    apply_draft_updates,
    commit_gate_decisions,
    project_has_draft_proposals,
    upsert_live_proposal_stubs,
)
from apme_gateway.proposals.flush import flush_proposals_for_project, replace_scan_proposals
from apme_gateway.proposals.grouping import GroupedProposal


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
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _seed_project_scan(*, with_draft: bool = False) -> tuple[str, str]:
    """Create project + scan + one proposal stub.

    Args:
        with_draft: When True, mark the proposal as an interactive draft.

    Returns:
        ``(project_id, scan_id)``.
    """
    project_id = "proj-draft-1"
    scan_id = "scan-draft-1"
    async with get_session() as db:
        db.add(
            Project(
                id=project_id,
                name="Draft Proj",
                repo_url="https://example.com/r.git",
                branch="main",
                created_at="2026-01-01T00:00:00+00:00",
            )
        )
        db.add(
            Session(
                session_id="sess-draft",
                project_path="/tmp/p",
                first_seen="2026-01-01T00:00:00+00:00",
                last_seen="2026-01-01T00:00:00+00:00",
            )
        )
        db.add(
            Scan(
                scan_id=scan_id,
                session_id="sess-draft",
                project_id=project_id,
                project_path="/tmp/p",
                source="gateway",
                trigger="ui",
                created_at="2026-01-01T00:00:00+00:00",
                scan_type="remediate",
            )
        )
        await db.flush()
        db.add(
            Proposal(
                scan_id=scan_id,
                proposal_id="prop-ai-abc",
                rule_id="L007",
                file="a.yml",
                tier=2,
                confidence=0.9,
                status="pending",
                path="a.yml::t[0]",
                source="ai",
                gate="ai",
                rule_ids_json='["L007"]',
                violation_ids_json="[]",
                engine_proposal_id="ai-0001",
                draft=1 if with_draft else 0,
                stamp_rule_ids_json='["L007"]',
            )
        )
        await db.commit()
    return project_id, scan_id


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_upsert_stores_primary_rule_id_for_coupled() -> None:
    """Coupled engine rule_id CSV must not land in Proposal.rule_id."""
    async with get_session() as db:
        rows = await upsert_live_proposal_stubs(
            db,
            scan_id="scan-coupled-primary",
            project_id=None,
            proposals=[
                {
                    "id": "eng-c",
                    "rule_id": "L007,L013",
                    "file": "c.yml",
                    "tier": 2,
                    "status": "pending",
                    "source": "ai",
                    "line_start": 1,
                }
            ],
        )
        await db.commit()
        assert rows[0].rule_id == "L007"
        assert "L013" in rows[0].rule_ids_json


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_upsert_live_stubs_sets_engine_proposal_id() -> None:
    """ProposalsReady stubs persist engine_proposal_id for the id bridge."""
    async with get_session() as db:
        rows = await upsert_live_proposal_stubs(
            db,
            scan_id="scan-live-1",
            project_id=None,
            proposals=[
                {
                    "id": "ai-0007",
                    "rule_id": "L007",
                    "file": "play.yml",
                    "tier": 2,
                    "status": "proposed",
                    "source": "ai",
                    "confidence": 0.8,
                    "diff_hunk": "@@ -1 +1 @@",
                }
            ],
        )
        await db.commit()
        assert len(rows) == 1
        assert rows[0].engine_proposal_id == "ai-0007"
        assert rows[0].proposal_id.startswith("prop-ai-")
        assert rows[0].status == "pending"


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_draft_update_does_not_stamp_review_or_analytics() -> None:
    """PATCH draft changes status + draft flag only."""
    _project_id, scan_id = await _seed_project_scan()
    async with get_session() as db:
        v = Violation(
            scan_id=scan_id,
            rule_id="L007",
            level="warning",
            message="x",
            file="a.yml",
            line=1,
            path="a.yml::t[0]",
            remediation_class=2,
            remediation_resolution=0,
            scope=0,
        )
        db.add(v)
        await db.flush()
        prop = (await db.execute(select(Proposal).where(Proposal.scan_id == scan_id))).scalar_one()
        prop.violation_ids_json = f"[{v.id}]"
        await db.commit()

    async with get_session() as db:
        updated = await apply_draft_updates(
            db,
            scan_id=scan_id,
            updates=[{"proposal_id": "ai-0001", "status": "approved"}],
        )
        await db.commit()
        assert updated[0].status == "approved"
        assert updated[0].draft == 1

    async with get_session() as db:
        v = (await db.execute(select(Violation).where(Violation.scan_id == scan_id))).scalar_one()
        assert v.review_status is None
        analytics = list((await db.execute(select(ProposalRuleAnalytics))).scalars().all())
        assert analytics == []


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_gate_commit_stamps_and_clears_draft() -> None:
    """Approve gate mirrors omit=reject, stamps review_status, clears draft."""
    project_id, scan_id = await _seed_project_scan(with_draft=True)
    async with get_session() as db:
        v = Violation(
            scan_id=scan_id,
            rule_id="L007",
            level="warning",
            message="x",
            file="a.yml",
            line=1,
            path="a.yml::t[0]",
            remediation_class=2,
            remediation_resolution=0,
            scope=0,
            fixed_yaml="",
        )
        db.add(v)
        await db.flush()
        prop = (await db.execute(select(Proposal).where(Proposal.scan_id == scan_id))).scalar_one()
        prop.violation_ids_json = f"[{v.id}]"
        prop.status = "pending"
        await db.commit()

    async with get_session() as db:
        n = await commit_gate_decisions(
            db,
            scan_id=scan_id,
            project_id=project_id,
            approved_engine_ids=["ai-0001"],
        )
        await db.commit()
        assert n == 1

    async with get_session() as db:
        prop = (await db.execute(select(Proposal).where(Proposal.scan_id == scan_id))).scalar_one()
        assert prop.status == "approved"
        assert prop.draft == 0
        assert prop.analytics_flushed == 1
        v = (await db.execute(select(Violation).where(Violation.scan_id == scan_id))).scalar_one()
        assert v.review_status == "ai_approved"
        analytics = list((await db.execute(select(ProposalRuleAnalytics))).scalars().all())
        assert len(analytics) == 1
        assert analytics[0].accepted_count == 1


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_gate_commit_omitted_ids_declined() -> None:
    """Engine omit=reject declines Gateway rows not in approved_ids."""
    project_id, scan_id = await _seed_project_scan()
    async with get_session() as db:
        n = await commit_gate_decisions(
            db,
            scan_id=scan_id,
            project_id=project_id,
            approved_engine_ids=[],
        )
        await db.commit()
        assert n == 1
        prop = (await db.execute(select(Proposal).where(Proposal.scan_id == scan_id))).scalar_one()
        assert prop.status == "declined"
        assert prop.draft == 0


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_abandon_detects_draft_only() -> None:
    """Abandon safeguard keys on draft=1, not mere approved status."""
    project_id, scan_id = await _seed_project_scan(with_draft=False)
    async with get_session() as db:
        assert await project_has_draft_proposals(db, project_id) is False
        prop = (await db.execute(select(Proposal).where(Proposal.scan_id == scan_id))).scalar_one()
        prop.draft = 1
        prop.status = "approved"
        await db.commit()
        assert await project_has_draft_proposals(db, project_id) is True


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_operate_remediate_409_when_draft(client: AsyncClient) -> None:
    """POST operate remediate returns 409 working_set_in_progress with draft.

    Args:
        client: Async HTTP test client.
    """
    project_id, _ = await _seed_project_scan(with_draft=True)
    resp = await client.post(
        f"/api/v1/projects/{project_id}/operation",
        json={"action": "remediate", "options": {}},
    )
    assert resp.status_code == 409
    body = resp.json()["detail"]
    assert body["code"] == "working_set_in_progress"

    resp2 = await client.post(
        f"/api/v1/projects/{project_id}/operation",
        json={"action": "remediate", "options": {}, "abandon_working_set": True},
    )
    # Abandon check passed — operation is created (background task may fail later).
    assert resp2.status_code == 201
    assert "operation_id" in resp2.json()

    async with get_session() as db:
        prop = (await db.execute(select(Proposal).where(Proposal.scan_id == "scan-draft-1"))).scalar_one()
        assert prop.draft == 0
        assert prop.status == "pending"


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_live_stub_attaches_project_and_abandon_resets_status() -> None:
    """Live stubs set project_id; abandon resets status so flush cannot invent review."""
    project_id = "proj-orphan"
    scan_id = "scan-orphan"
    async with get_session() as db:
        db.add(
            Project(
                id=project_id,
                name="Orphan",
                repo_url="https://example.com/o.git",
                branch="main",
                created_at="2026-01-01T00:00:00+00:00",
            )
        )
        await db.commit()
        rows = await upsert_live_proposal_stubs(
            db,
            scan_id=scan_id,
            project_id=project_id,
            proposals=[
                {
                    "id": "ai-orphan",
                    "rule_id": "L007",
                    "file": "o.yml",
                    "tier": 2,
                    "status": "pending",
                    "source": "ai",
                    "line_start": 3,
                }
            ],
        )
        rows[0].draft = 1
        rows[0].status = "approved"
        await db.commit()
        scan = (await db.execute(select(Scan).where(Scan.scan_id == scan_id))).scalar_one()
        assert scan.project_id == project_id
        assert await project_has_draft_proposals(db, project_id) is True
        n = await abandon_project_drafts(db, project_id)
        await db.commit()
        assert n >= 1
        prop = (await db.execute(select(Proposal).where(Proposal.scan_id == scan_id))).scalar_one()
        assert prop.draft == 0
        assert prop.status == "pending"


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_gate_commit_preserves_analytics_across_replace() -> None:
    """analytics_flushed survives replace_scan_proposals (no double-count)."""
    project_id, scan_id = await _seed_project_scan()
    async with get_session() as db:
        await commit_gate_decisions(
            db,
            scan_id=scan_id,
            project_id=project_id,
            approved_engine_ids=["ai-0001"],
            offered_engine_ids=["ai-0001"],
        )
        await db.commit()
        prop = (await db.execute(select(Proposal).where(Proposal.scan_id == scan_id))).scalar_one()
        assert prop.analytics_flushed == 1
        assert prop.status == "approved"

        # Seeded stub has line_start=0; archival group must match that key
        # (primary.Proposal has no path — bridge is file+rule+line_start).
        await replace_scan_proposals(
            db,
            scan_id=scan_id,
            proposals=[
                GroupedProposal(
                    proposal_id="prop-ai-abc",
                    rule_id="L007",
                    rule_ids=("L007",),
                    violation_ids=(),
                    file="a.yml",
                    path="a.yml::t[0]",
                    line_start=0,
                    tier=2,
                    source="ai",
                    gate="ai",
                    status="pending",
                )
            ],
        )
        await db.commit()
        prop = (await db.execute(select(Proposal).where(Proposal.scan_id == scan_id))).scalar_one()
        assert prop.analytics_flushed == 1
        assert prop.engine_proposal_id == "ai-0001"
        assert prop.status == "approved"

        await flush_proposals_for_project(db, project_id)
        await db.commit()
        analytics = list((await db.execute(select(ProposalRuleAnalytics))).scalars().all())
        assert len(analytics) == 1
        assert analytics[0].accepted_count == 1


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_gate_commit_scopes_to_offered_engine_ids() -> None:
    """A later gate must not rewrite prior decisions on the same scan."""
    project_id, scan_id = await _seed_project_scan()
    async with get_session() as db:
        db.add(
            Proposal(
                scan_id=scan_id,
                proposal_id="prop-ai-def",
                rule_id="L008",
                file="b.yml",
                tier=2,
                confidence=0.5,
                status="pending",
                path="b.yml::t[0]",
                source="ai",
                gate="ai",
                rule_ids_json='["L008"]',
                violation_ids_json="[]",
                engine_proposal_id="ai-0002",
                draft=0,
                stamp_rule_ids_json='["L008"]',
            )
        )
        await db.commit()

        await commit_gate_decisions(
            db,
            scan_id=scan_id,
            project_id=project_id,
            approved_engine_ids=["ai-0001"],
            offered_engine_ids=["ai-0001"],
        )
        await db.commit()
        by_eng = {
            p.engine_proposal_id: p
            for p in (await db.execute(select(Proposal).where(Proposal.scan_id == scan_id))).scalars().all()
        }
        assert by_eng["ai-0001"].status == "approved"
        assert by_eng["ai-0002"].status == "pending"

        await commit_gate_decisions(
            db,
            scan_id=scan_id,
            project_id=project_id,
            approved_engine_ids=[],
            offered_engine_ids=["ai-0002"],
        )
        await db.commit()
        by_eng = {
            p.engine_proposal_id: p
            for p in (await db.execute(select(Proposal).where(Proposal.scan_id == scan_id))).scalars().all()
        }
        assert by_eng["ai-0001"].status == "approved"
        assert by_eng["ai-0002"].status == "declined"


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_archival_id_includes_engine_id_when_path_set() -> None:
    """Two same-path live stubs must not collapse to one proposal_id."""
    scan_id = "scan-path-collide"
    async with get_session() as db:
        rows = await upsert_live_proposal_stubs(
            db,
            scan_id=scan_id,
            project_id=None,
            proposals=[
                {
                    "id": "eng-1",
                    "rule_id": "L007",
                    "file": "a.yml",
                    "path": "a.yml::t[0]",
                    "tier": 2,
                    "status": "pending",
                    "source": "ai",
                    "line_start": 1,
                },
                {
                    "id": "eng-2",
                    "rule_id": "L013",
                    "file": "a.yml",
                    "path": "a.yml::t[0]",
                    "tier": 2,
                    "status": "pending",
                    "source": "ai",
                    "line_start": 1,
                },
            ],
        )
        await db.commit()
        assert len(rows) == 2
        assert rows[0].proposal_id != rows[1].proposal_id
        assert {r.engine_proposal_id for r in rows} == {"eng-1", "eng-2"}


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_bridge_skips_ambiguous_duplicate_keys() -> None:
    """Duplicate bridge keys must not last-wins overwrite engine ids."""
    scan_id = "scan-ambiguous"
    async with get_session() as db:
        await upsert_live_proposal_stubs(
            db,
            scan_id=scan_id,
            project_id=None,
            proposals=[
                {
                    "id": "eng-a",
                    "rule_id": "L001",
                    "file": "same.yml",
                    "tier": 2,
                    "status": "approved",
                    "source": "ai",
                    "line_start": 0,
                },
                {
                    "id": "eng-b",
                    "rule_id": "L001",
                    "file": "same.yml",
                    "tier": 2,
                    "status": "declined",
                    "source": "ai",
                    "line_start": 0,
                },
            ],
        )
        await db.commit()
        await replace_scan_proposals(
            db,
            scan_id=scan_id,
            proposals=[
                GroupedProposal(
                    proposal_id="prop-ai-x",
                    rule_id="L001",
                    rule_ids=("L001",),
                    violation_ids=(1,),
                    file="same.yml",
                    path="",
                    line_start=0,
                    tier=2,
                    source="ai",
                    gate="ai",
                    status="pending",
                )
            ],
        )
        await db.commit()
        prop = (await db.execute(select(Proposal).where(Proposal.scan_id == scan_id))).scalar_one()
        # Ambiguous — do not attach either stub's engine id / status.
        assert prop.engine_proposal_id is None
        assert prop.status == "pending"


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_bridge_normalizes_coupled_rule_id() -> None:
    """Coupled stub rule_id 'L007,L013' bridges to grouped primary 'L007'."""
    scan_id = "scan-coupled"
    async with get_session() as db:
        await upsert_live_proposal_stubs(
            db,
            scan_id=scan_id,
            project_id=None,
            proposals=[
                {
                    "id": "eng-coupled",
                    "rule_id": "L007,L013",
                    "file": "c.yml",
                    "tier": 2,
                    "status": "approved",
                    "source": "ai",
                    "line_start": 5,
                }
            ],
        )
        await db.commit()
        prop = (await db.execute(select(Proposal).where(Proposal.scan_id == scan_id))).scalar_one()
        prop.analytics_flushed = 1
        await db.commit()

        await replace_scan_proposals(
            db,
            scan_id=scan_id,
            proposals=[
                GroupedProposal(
                    proposal_id="prop-ai-c",
                    rule_id="L007",
                    rule_ids=("L007", "L013"),
                    violation_ids=(1, 2),
                    file="c.yml",
                    path="c.yml::t[0]",
                    line_start=5,
                    tier=2,
                    source="ai-candidate",
                    gate="ai",
                    status="pending",
                )
            ],
        )
        await db.commit()
        prop = (await db.execute(select(Proposal).where(Proposal.scan_id == scan_id))).scalar_one()
        assert prop.engine_proposal_id == "eng-coupled"
        assert prop.status == "approved"
        assert prop.analytics_flushed == 1


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_bridge_uses_file_rule_line_start() -> None:
    """Bridge matches stubs to archival groups by file+rule+line_start."""
    scan_id = "scan-bridge-multi"
    async with get_session() as db:
        await upsert_live_proposal_stubs(
            db,
            scan_id=scan_id,
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
                },
                {
                    "id": "eng-b",
                    "rule_id": "L001",
                    "file": "same.yml",
                    "tier": 2,
                    "status": "declined",
                    "source": "ai",
                    "line_start": 2,
                },
            ],
        )
        await db.commit()
        for prop in (await db.execute(select(Proposal).where(Proposal.scan_id == scan_id))).scalars().all():
            prop.analytics_flushed = 1 if prop.engine_proposal_id == "eng-a" else 0
        await db.commit()

        await replace_scan_proposals(
            db,
            scan_id=scan_id,
            proposals=[
                GroupedProposal(
                    proposal_id="prop-ai-a",
                    rule_id="L001",
                    rule_ids=("L001",),
                    violation_ids=(1,),
                    file="same.yml",
                    path="same.yml::t[0]",
                    line_start=1,
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
                    line_start=2,
                    tier=2,
                    source="ai",
                    gate="ai",
                    status="pending",
                ),
            ],
        )
        await db.commit()
        by_line = {
            p.line_start: p
            for p in (await db.execute(select(Proposal).where(Proposal.scan_id == scan_id))).scalars().all()
        }
        assert by_line[1].engine_proposal_id == "eng-a"
        assert by_line[1].status == "approved"
        assert by_line[1].analytics_flushed == 1
        assert by_line[2].engine_proposal_id == "eng-b"
        assert by_line[2].status == "declined"
