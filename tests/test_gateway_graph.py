"""Unit tests for ContentGraph visualization persistence and REST API."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from apme.v1 import reporting_pb2
from apme_gateway.app import create_app
from apme_gateway.db import close_db, get_session, init_db
from apme_gateway.db import queries as q
from apme_gateway.db.models import Project, Scan, ScanGraph, Session
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


def _mock_context() -> MagicMock:
    """Build a mock gRPC servicer context.

    Returns:
        MagicMock with async abort.
    """
    ctx = MagicMock()
    ctx.abort = AsyncMock()
    return ctx


_SAMPLE_GRAPH = {
    "version": 2,
    "nodes": [
        {"id": "n1", "data": {"node_type": "playbook", "name": "site.yml"}},
        {"id": "n2", "data": {"node_type": "play", "name": "Configure servers"}},
    ],
    "edges": [
        {"source": "n1", "target": "n2", "edge_type": "contains", "position": 0},
    ],
    "execution_edges": [
        {"source": "n1", "target": "n2"},
    ],
}


async def _seed_project_with_graph(
    *,
    project_id: str = "proj-g1",
    name: str = "Graph Project",
    graph_json: str | None = None,
) -> None:
    """Insert a project, session, scan, and graph data.

    Args:
        project_id: Project UUID.
        name: Project display name.
        graph_json: Optional override for graph JSON payload.
    """
    if graph_json is None:
        graph_json = json.dumps(_SAMPLE_GRAPH)

    async with get_session() as db:
        db.add(
            Project(
                id=project_id,
                name=name,
                repo_url="https://github.com/test/repo.git",
                branch="main",
                created_at="2026-03-01T00:00:00Z",
                health_score=85,
            )
        )
        db.add(Session(session_id="s-" + project_id, project_path="/tmp", first_seen="t0", last_seen="t1"))
        db.add(
            Scan(
                scan_id="scan-" + project_id,
                session_id="s-" + project_id,
                project_id=project_id,
                project_path="/tmp",
                source="cli",
                created_at="2026-03-01T00:00:00Z",
                scan_type="check",
            )
        )
        db.add(
            ScanGraph(
                scan_id="scan-" + project_id,
                graph_json=graph_json,
                node_count=2,
                edge_count=1,
            )
        )
        await db.commit()


# ── Servicer graph persistence tests ──────────────────────────────────


async def test_report_fix_with_graph_persists() -> None:
    """ContentGraph JSON from FixCompletedEvent is persisted."""
    servicer = ReportingServicer()
    graph_json = json.dumps(_SAMPLE_GRAPH)
    event = reporting_pb2.FixCompletedEvent(
        scan_id="scan-g1",
        session_id="sess-g1",
        project_path="/proj",
        source="cli",
        content_graph_json=graph_json,
    )
    await servicer.ReportFixCompleted(event, _mock_context())

    async with get_session() as db:
        scan = await q.get_scan(db, "scan-g1")
        assert scan is not None

        from sqlalchemy import select

        g_result = await db.execute(select(ScanGraph).where(ScanGraph.scan_id == "scan-g1"))
        g = g_result.scalar_one_or_none()
        assert g is not None
        assert g.node_count == 2
        assert g.edge_count == 1
        parsed = json.loads(g.graph_json)
        assert parsed["version"] == 2
        assert len(parsed["nodes"]) == 2


async def test_report_fix_without_graph_ok() -> None:
    """Fix event without graph data still persists normally."""
    servicer = ReportingServicer()
    event = reporting_pb2.FixCompletedEvent(
        scan_id="scan-no-g",
        session_id="sess-no-g",
        project_path="/proj",
        source="cli",
    )
    await servicer.ReportFixCompleted(event, _mock_context())

    async with get_session() as db:
        scan = await q.get_scan(db, "scan-no-g")
        assert scan is not None

        from sqlalchemy import select

        g_result = await db.execute(select(ScanGraph).where(ScanGraph.scan_id == "scan-no-g"))
        assert g_result.scalar_one_or_none() is None


async def test_report_fix_with_invalid_graph_stores_raw() -> None:
    """Invalid JSON graph is stored raw with zero counts."""
    servicer = ReportingServicer()
    event = reporting_pb2.FixCompletedEvent(
        scan_id="scan-bad-g",
        session_id="sess-bad-g",
        project_path="/proj",
        source="cli",
        content_graph_json="not valid json {{{",
    )
    await servicer.ReportFixCompleted(event, _mock_context())

    async with get_session() as db:
        from sqlalchemy import select

        g_result = await db.execute(select(ScanGraph).where(ScanGraph.scan_id == "scan-bad-g"))
        g = g_result.scalar_one_or_none()
        assert g is not None
        assert g.node_count == 0
        assert g.edge_count == 0
        assert g.graph_json == "not valid json {{{"


async def test_report_fix_with_non_dict_graph_stores_raw() -> None:
    """Non-dict JSON (e.g. a list) is stored raw with zero counts."""
    servicer = ReportingServicer()
    event = reporting_pb2.FixCompletedEvent(
        scan_id="scan-list-g",
        session_id="sess-list-g",
        project_path="/proj",
        source="cli",
        content_graph_json="[1, 2, 3]",
    )
    await servicer.ReportFixCompleted(event, _mock_context())

    async with get_session() as db:
        from sqlalchemy import select

        g_result = await db.execute(select(ScanGraph).where(ScanGraph.scan_id == "scan-list-g"))
        g = g_result.scalar_one_or_none()
        assert g is not None
        assert g.node_count == 0
        assert g.edge_count == 0


# ── REST API tests ────────────────────────────────────────────────────


async def test_project_graph_endpoint(client: AsyncClient) -> None:
    """GET /projects/{id}/graph returns graph data.

    Args:
        client: Async HTTP test client.
    """
    await _seed_project_with_graph()

    resp = await client.get("/api/v1/projects/proj-g1/graph")
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == 2
    assert len(data["nodes"]) == 2
    assert len(data["edges"]) == 1
    assert data["nodes"][0]["id"] == "n1"


async def test_project_graph_by_name(client: AsyncClient) -> None:
    """GET /projects/{name}/graph resolves by project name.

    Args:
        client: Async HTTP test client.
    """
    await _seed_project_with_graph()

    resp = await client.get("/api/v1/projects/Graph Project/graph")
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == 2


async def test_project_graph_404_unknown_project(client: AsyncClient) -> None:
    """GET /projects/{id}/graph returns 404 for unknown project.

    Args:
        client: Async HTTP test client.
    """
    resp = await client.get("/api/v1/projects/nonexistent/graph")
    assert resp.status_code == 404


async def test_project_graph_404_no_graph(client: AsyncClient) -> None:
    """GET /projects/{id}/graph returns 404 when project exists but has no graph.

    Args:
        client: Async HTTP test client.
    """
    async with get_session() as db:
        db.add(
            Project(
                id="no-graph-proj",
                name="No Graph",
                repo_url="https://github.com/test/empty.git",
                branch="main",
                created_at="2026-03-01T00:00:00Z",
                health_score=100,
            )
        )
        await db.commit()

    resp = await client.get("/api/v1/projects/no-graph-proj/graph")
    assert resp.status_code == 404


async def test_project_graph_500_invalid_stored_json(client: AsyncClient) -> None:
    """GET /projects/{id}/graph returns 500 when stored JSON is corrupt.

    Args:
        client: Async HTTP test client.
    """
    await _seed_project_with_graph(
        project_id="proj-corrupt",
        name="Corrupt Graph",
        graph_json="not valid json {{{",
    )

    resp = await client.get("/api/v1/projects/proj-corrupt/graph")
    assert resp.status_code == 500
    assert "invalid JSON" in resp.json()["detail"]


async def test_project_graph_500_non_dict_stored_json(client: AsyncClient) -> None:
    """GET /projects/{id}/graph returns 500 when stored JSON is a non-object value.

    Args:
        client: Async HTTP test client.
    """
    await _seed_project_with_graph(
        project_id="proj-array",
        name="Array Graph",
        graph_json="[1, 2, 3]",
    )

    resp = await client.get("/api/v1/projects/proj-array/graph")
    assert resp.status_code == 500
    assert "not a JSON object" in resp.json()["detail"]
