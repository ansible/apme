"""Error envelope and contract tests for /api/v1 (N20)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError

from apme_gateway.app import create_app
from apme_gateway.db import get_session
from apme_gateway.db.models import Project, Scan, Session
from apme_gateway.operation_registry import get_operation_registry
from apme_gateway.operation_types import OperationStatus, Proposal

pytestmark = pytest.mark.usefixtures("gateway_db")


@pytest.fixture  # type: ignore[untyped-decorator]
async def client() -> AsyncIterator[AsyncClient]:
    """Build an async test client for the gateway app.

    Yields:
        AsyncClient: Client bound to the ASGI app.
    """
    app = create_app()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_project(
    *,
    project_id: str = "proj-err-1",
    name: str = "err-proj",
    repo_url: str = "https://github.com/org/repo.git",
) -> None:
    """Insert a project directly into the DB.

    Args:
        project_id: Project primary key.
        name: Display name.
        repo_url: SCM clone URL.
    """
    async with get_session() as db:
        db.add(
            Project(
                id=project_id,
                name=name,
                repo_url=repo_url,
                branch="main",
                created_at="2026-01-01T00:00:00Z",
            )
        )
        await db.commit()


async def test_duplicate_project_returns_409_envelope(client: AsyncClient) -> None:
    """Duplicate project name returns 409 with a string detail envelope.

    Args:
        client: Async HTTP test client.
    """
    first = await client.post(
        "/api/v1/projects",
        json={"name": "dup-proj", "repo_url": "https://github.com/org/repo.git"},
    )
    assert first.status_code == 201
    second = await client.post(
        "/api/v1/projects",
        json={"name": "dup-proj", "repo_url": "https://github.com/org/other.git"},
    )
    assert second.status_code == 409
    body = second.json()
    assert "detail" in body
    assert isinstance(body["detail"], str)
    assert "already exists" in body["detail"]


async def test_integrity_error_maps_to_409_envelope(client: AsyncClient) -> None:
    """A raw IntegrityError on create maps to the 409 envelope, not 500.

    Args:
        client: Async HTTP test client.
    """
    with patch(
        "apme_gateway.db.queries.create_project",
        side_effect=IntegrityError("INSERT", {}, Exception("duplicate key")),
    ):
        resp = await client.post(
            "/api/v1/projects",
            json={"name": "any-name", "repo_url": "https://github.com/org/repo.git"},
        )
    assert resp.status_code == 409
    body = resp.json()
    assert isinstance(body["detail"], str)


async def test_unexpected_error_is_500_with_string_detail(client: AsyncClient) -> None:
    """Unexpected persistence failures surface as 500 with string detail.

    Args:
        client: Async HTTP test client.
    """
    with patch(
        "apme_gateway.db.queries.create_project",
        side_effect=RuntimeError("db exploded"),
    ):
        resp = await client.post(
            "/api/v1/projects",
            json={"name": "boom-proj", "repo_url": "https://github.com/org/repo.git"},
        )
    # FastAPI converts unhandled exceptions to 500 with string detail.
    assert resp.status_code == 500
    body = resp.json()
    assert isinstance(body["detail"], str)


async def test_duplicate_session_scan_persist_is_idempotent() -> None:
    """Duplicate scan persist (same scan_id) does not raise IntegrityError."""
    from unittest.mock import MagicMock

    from sqlalchemy import func, select

    from apme.v1 import reporting_pb2
    from apme_gateway.db.models import ScanLog, ScanPatch, Violation
    from apme_gateway.grpc_reporting.servicer import ReportingServicer

    async def _child_counts(scan_id: str) -> tuple[int, int, int, int]:
        """Count the scan row plus append-only children for one scan.

        Args:
            scan_id: Scan whose rows to count.

        Returns:
            Tuple of (scan rows, violations, patches, logs) counts.
        """
        async with get_session() as db:
            scans = (
                await db.execute(select(func.count()).select_from(Scan).where(Scan.scan_id == scan_id))
            ).scalar_one()
            violations = (
                await db.execute(select(func.count()).select_from(Violation).where(Violation.scan_id == scan_id))
            ).scalar_one()
            patches = (
                await db.execute(select(func.count()).select_from(ScanPatch).where(ScanPatch.scan_id == scan_id))
            ).scalar_one()
            logs = (
                await db.execute(select(func.count()).select_from(ScanLog).where(ScanLog.scan_id == scan_id))
            ).scalar_one()
        return scans, violations, patches, logs

    ctx = MagicMock()
    from unittest.mock import AsyncMock as _AsyncMock

    ctx.abort = _AsyncMock()
    servicer = ReportingServicer()
    event = reporting_pb2.FixCompletedEvent(
        scan_id="dup-scan-1",
        session_id="sess-dup-1",
        project_path="/proj",
        source="cli",
    )
    first = await servicer.ReportFixCompleted(event, ctx)
    assert first is not None
    counts_after_first = await _child_counts("dup-scan-1")
    second = await servicer.ReportFixCompleted(event, ctx)
    assert second is not None
    # Idempotent replay: the duplicate returns the existing scan instead of
    # raising IntegrityError — same ack payload, no abort.
    assert second == first
    assert ctx.abort.await_count == 0
    counts_after_second = await _child_counts("dup-scan-1")

    from apme_gateway.grpc_reporting.servicer import drain_notification_tasks

    await drain_notification_tasks()
    # Exactly one Scan row for the duplicated scan_id ...
    assert counts_after_second[0] == 1
    # ... and replay clears + re-inserts children instead of duplicating them.
    assert counts_after_second == counts_after_first
    async with get_session() as db:
        scan = await db.get(Scan, "dup-scan-1")
    assert scan is not None


async def test_error_detail_is_string_across_endpoints(client: AsyncClient) -> None:
    """Uniform contract: plain errors stay strings, coded 409s are dicts.

    Covers 404 (string detail) and 409 coded errors (``{"code",
    "message"}`` dict per ADR-060 additive-only), including the
    normalized operation_router coded errors (N20).

    Args:
        client: Async HTTP test client.
    """
    # 404 project lookup (plain string detail, unchanged).
    resp = await client.get("/api/v1/projects/does-not-exist-123")
    assert resp.status_code == 404
    assert isinstance(resp.json()["detail"], str)

    # 404 operation state (plain string detail, unchanged).
    resp = await client.get("/api/v1/projects/does-not-exist-123/operation")
    assert resp.status_code == 404
    assert isinstance(resp.json()["detail"], str)

    # 409 coded error: begin-remediate in wrong state uses dict shape.
    await _seed_project(project_id="proj-contract-1", name="contract-1")
    registry = get_operation_registry()
    registry.create(
        operation_id="op-contract-1",
        project_id="proj-contract-1",
        scan_id="scan-contract-1",
        scan_type="check",
    )
    resp = await client.post("/api/v1/projects/proj-contract-1/operation/begin-remediate")
    assert resp.status_code == 409
    body = resp.json()
    assert isinstance(body["detail"], dict)
    assert body["detail"]["code"] == "invalid_status"
    assert isinstance(body["detail"]["message"], str)

    # 409 coded error: escalate-ai in wrong state uses dict shape.
    resp = await client.post("/api/v1/projects/proj-contract-1/operation/escalate-ai", json={"targets": []})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == "invalid_status"


async def test_working_set_conflict_detail_is_string(client: AsyncClient) -> None:
    """Working-set 409 uses the dict coded form with code + message.

    Args:
        client: Async HTTP test client.
    """
    await _seed_project(project_id="proj-contract-2", name="contract-2")
    async with get_session() as db:
        db.add(Session(session_id="sess-ws", project_path="/proj", first_seen="t0", last_seen="t1"))
        db.add(
            Scan(
                scan_id="scan-ws-1",
                session_id="sess-ws",
                project_id="proj-contract-2",
                project_path="/proj",
                source="gateway",
                created_at="2026-01-01T00:00:00Z",
                scan_type="remediate",
            )
        )
        await db.commit()

    from apme_gateway.db.models import Proposal as _Proposal

    async with get_session() as db:
        db.add(
            _Proposal(
                scan_id="scan-ws-1",
                proposal_id="p-ws-1",
                rule_id="L001",
                file="a.yml",
                tier=1,
                confidence=0.9,
                status="pending",
                draft=1,
            )
        )
        await db.commit()

    resp = await client.post(
        "/api/v1/projects/proj-contract-2/operation",
        json={"action": "remediate"},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert isinstance(body["detail"], dict)
    assert body["detail"]["code"] == "working_set_in_progress"


async def test_approve_response_is_additive(client: AsyncClient) -> None:
    """Approve keeps status and adds submit_token (no breaking change).

    Args:
        client: Async HTTP test client.
    """
    await _seed_project(project_id="proj-contract-3", name="contract-3")
    registry = get_operation_registry()
    registry.create(
        operation_id="op-contract-3",
        project_id="proj-contract-3",
        scan_id="scan-contract-3",
        scan_type="remediate",
    )
    await registry.set_proposals("op-contract-3", [Proposal(id="t1-x", rule_id="L001", file="a.yml")])
    created = registry.get("op-contract-3")
    assert created is not None
    assert created.status == OperationStatus.AWAITING_APPROVAL

    with patch("apme_gateway.proposals.draft.commit_gate_decisions", new_callable=AsyncMock) as mock_commit:
        mock_commit.return_value = None
        resp = await client.post(
            "/api/v1/projects/proj-contract-3/operation/approve",
            json={"approved_ids": ["t1-x"]},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert isinstance(body.get("submit_token"), str)
