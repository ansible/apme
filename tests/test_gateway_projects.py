"""Unit tests for project CRUD and dashboard REST API endpoints (ADR-037)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from apme_gateway.app import create_app
from apme_gateway.db import close_db, get_session, init_db
from apme_gateway.db.models import Project, Scan, Session, Violation


@pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
async def _db(tmp_path: Path) -> AsyncIterator[None]:
    """Initialise a fresh DB per test.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Yields:
        None: The fixture is auto-used for side-effects only.
    """
    await init_db(str(tmp_path / "test.db"))
    yield
    await close_db()


@pytest.fixture  # type: ignore[untyped-decorator]
async def client() -> AsyncIterator[AsyncClient]:
    """Build an async test client for the gateway app.

    Yields:
        AsyncClient: Configured HTTPX client targeting the in-process ASGI app.
    """
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_project(
    *,
    project_id: str = "proj-1",
    name: str = "Test Project",
    repo_url: str = "https://github.com/test/repo.git",
    branch: str = "main",
    add_scan: bool = False,
    scan_violations: int = 0,
) -> None:
    """Insert test project data directly into the DB.

    Args:
        project_id: Primary key for the project.
        name: Display name.
        repo_url: SCM clone URL.
        branch: Git branch.
        add_scan: If True, also seed a scan and associated session.
        scan_violations: Number of violations to attach to the scan.
    """
    async with get_session() as db:
        db.add(
            Project(
                id=project_id,
                name=name,
                repo_url=repo_url,
                branch=branch,
                created_at="2026-03-01T00:00:00Z",
                health_score=100,
            )
        )
        if add_scan:
            db.add(Session(session_id="s-" + project_id, project_path="/tmp", first_seen="t0", last_seen="t1"))
            db.add(
                Scan(
                    scan_id="scan-" + project_id,
                    session_id="s-" + project_id,
                    project_id=project_id,
                    project_path="/tmp/project",
                    source="gateway",
                    trigger="ui",
                    created_at="2026-03-15T12:00:00Z",
                    scan_type="check",
                    total_violations=scan_violations,
                )
            )
            if scan_violations > 0:
                for i in range(scan_violations):
                    db.add(
                        Violation(
                            scan_id="scan-" + project_id,
                            rule_id=f"L{i + 1:03d}",
                            level="error" if i % 2 == 0 else "medium",
                            message=f"violation {i + 1}",
                            file="a.yml",
                        )
                    )
        await db.commit()


# ── Project CRUD ──────────────────────────────────────────────────────


async def test_create_project(client: AsyncClient) -> None:
    """POST /projects creates a project and returns 201.

    Args:
        client: Async HTTPX test client.
    """
    resp = await client.post(
        "/api/v1/projects",
        json={
            "name": "My Project",
            "repo_url": "https://github.com/org/repo.git",
            "branch": "develop",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "My Project"
    assert body["repo_url"] == "https://github.com/org/repo.git"
    assert body["branch"] == "develop"
    assert body["health_score"] == 0
    assert "id" in body


async def test_list_projects_empty(client: AsyncClient) -> None:
    """Empty DB returns empty project list.

    Args:
        client: Async HTTPX test client.
    """
    resp = await client.get("/api/v1/projects")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []


async def test_list_projects(client: AsyncClient) -> None:
    """Seeded project appears in list.

    Args:
        client: Async HTTPX test client.
    """
    await _seed_project()
    resp = await client.get("/api/v1/projects")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Test Project"


async def test_get_project_detail(client: AsyncClient) -> None:
    """GET /projects/{id} returns project with scan info.

    Args:
        client: Async HTTPX test client.
    """
    await _seed_project(add_scan=True, scan_violations=3)
    resp = await client.get("/api/v1/projects/proj-1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Test Project"
    assert body["total_violations"] == 3
    assert body["latest_scan"] is not None
    assert body["latest_scan"]["scan_id"] == "scan-proj-1"


async def test_get_project_not_found(client: AsyncClient) -> None:
    """Missing project returns 404.

    Args:
        client: Async HTTPX test client.
    """
    resp = await client.get("/api/v1/projects/missing")
    assert resp.status_code == 404


async def test_update_project(client: AsyncClient) -> None:
    """PATCH /projects/{id} updates fields.

    Args:
        client: Async HTTPX test client.
    """
    await _seed_project()
    resp = await client.patch("/api/v1/projects/proj-1", json={"name": "Renamed"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Renamed"
    assert body["repo_url"] == "https://github.com/test/repo.git"


async def test_update_project_no_fields(client: AsyncClient) -> None:
    """PATCH with empty body returns 400.

    Args:
        client: Async HTTPX test client.
    """
    await _seed_project()
    resp = await client.patch("/api/v1/projects/proj-1", json={})
    assert resp.status_code == 400


async def test_update_project_not_found(client: AsyncClient) -> None:
    """PATCH on missing project returns 404.

    Args:
        client: Async HTTPX test client.
    """
    resp = await client.patch("/api/v1/projects/missing", json={"name": "x"})
    assert resp.status_code == 404


async def test_delete_project(client: AsyncClient) -> None:
    """DELETE /projects/{id} removes the project.

    Args:
        client: Async HTTPX test client.
    """
    await _seed_project()
    resp = await client.delete("/api/v1/projects/proj-1")
    assert resp.status_code == 204
    resp = await client.get("/api/v1/projects/proj-1")
    assert resp.status_code == 404


async def test_delete_project_not_found(client: AsyncClient) -> None:
    """DELETE on missing project returns 404.

    Args:
        client: Async HTTPX test client.
    """
    resp = await client.delete("/api/v1/projects/missing")
    assert resp.status_code == 404


# ── Project-scoped endpoints ─────────────────────────────────────────


async def test_project_scans(client: AsyncClient) -> None:
    """GET /projects/{id}/activity returns activity for the project.

    Args:
        client: Async HTTPX test client.
    """
    await _seed_project(add_scan=True)
    resp = await client.get("/api/v1/projects/proj-1/activity")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["scan_id"] == "scan-proj-1"


async def test_project_scans_not_found(client: AsyncClient) -> None:
    """GET /projects/{id}/activity returns 404 for unknown project.

    Args:
        client: Async HTTPX test client.
    """
    resp = await client.get("/api/v1/projects/missing/activity")
    assert resp.status_code == 404


async def test_project_violations(client: AsyncClient) -> None:
    """GET /projects/{id}/violations returns violations from latest scan.

    Args:
        client: Async HTTPX test client.
    """
    await _seed_project(add_scan=True, scan_violations=2)
    resp = await client.get("/api/v1/projects/proj-1/violations")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2


async def test_project_violations_empty(client: AsyncClient) -> None:
    """GET /projects/{id}/violations returns empty for project with no scans.

    Args:
        client: Async HTTPX test client.
    """
    await _seed_project()
    resp = await client.get("/api/v1/projects/proj-1/violations")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_project_trend(client: AsyncClient) -> None:
    """GET /projects/{id}/trend returns trend data.

    Args:
        client: Async HTTPX test client.
    """
    await _seed_project(add_scan=True)
    resp = await client.get("/api/v1/projects/proj-1/trend")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1


async def test_project_trend_not_found(client: AsyncClient) -> None:
    """GET /projects/{id}/trend returns 404 for unknown project.

    Args:
        client: Async HTTPX test client.
    """
    resp = await client.get("/api/v1/projects/missing/trend")
    assert resp.status_code == 404


# ── Dashboard ─────────────────────────────────────────────────────────


async def test_dashboard_summary_empty(client: AsyncClient) -> None:
    """Dashboard summary works with no projects.

    Args:
        client: Async HTTPX test client.
    """
    resp = await client.get("/api/v1/dashboard/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_projects"] == 0
    assert body["total_scans"] == 0
    assert body["current_violations"] == 0
    assert body["current_fixable"] == 0
    assert body["current_ai_candidates"] == 0


async def test_dashboard_summary(client: AsyncClient) -> None:
    """Dashboard summary aggregates across projects.

    Args:
        client: Async HTTPX test client.
    """
    await _seed_project(project_id="p1", name="Proj 1", add_scan=True, scan_violations=5)
    await _seed_project(project_id="p2", name="Proj 2", add_scan=True, scan_violations=2)
    resp = await client.get("/api/v1/dashboard/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_projects"] == 2
    assert body["total_scans"] == 2
    assert body["current_violations"] == 7
    assert body["current_fixable"] == 0
    assert body["current_ai_candidates"] == 0


async def test_dashboard_rankings(client: AsyncClient) -> None:
    """Dashboard rankings returns ranked projects.

    Args:
        client: Async HTTPX test client.
    """
    await _seed_project(project_id="p1", name="Clean", add_scan=True, scan_violations=0)
    await _seed_project(project_id="p2", name="Dirty", add_scan=True, scan_violations=10)
    resp = await client.get("/api/v1/dashboard/rankings", params={"sort_by": "health_score", "order": "desc"})
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 2


async def test_dashboard_rankings_empty(client: AsyncClient) -> None:
    """Dashboard rankings with no projects returns empty list.

    Args:
        client: Async HTTPX test client.
    """
    resp = await client.get("/api/v1/dashboard/rankings")
    assert resp.status_code == 200
    assert resp.json() == []


# ── Name-based resolution ────────────────────────────────────────────


async def test_get_project_by_name(client: AsyncClient) -> None:
    """GET /projects/{name} resolves by unique name.

    Args:
        client: Async HTTPX test client.
    """
    await _seed_project(add_scan=True, scan_violations=2)
    resp = await client.get("/api/v1/projects/Test Project")
    assert resp.status_code == 200
    assert resp.json()["id"] == "proj-1"


async def test_update_project_by_name(client: AsyncClient) -> None:
    """PATCH /projects/{name} resolves by name.

    Args:
        client: Async HTTPX test client.
    """
    await _seed_project()
    resp = await client.patch("/api/v1/projects/Test Project", json={"branch": "develop"})
    assert resp.status_code == 200
    assert resp.json()["branch"] == "develop"


async def test_delete_project_by_name(client: AsyncClient) -> None:
    """DELETE /projects/{name} resolves by name.

    Args:
        client: Async HTTPX test client.
    """
    await _seed_project()
    resp = await client.delete("/api/v1/projects/Test Project")
    assert resp.status_code == 204


async def test_create_duplicate_name_rejected(client: AsyncClient) -> None:
    """POST /projects rejects duplicate name with 409.

    Args:
        client: Async HTTPX test client.
    """
    payload = {"name": "Unique Name", "repo_url": "https://github.com/a/b.git"}
    resp1 = await client.post("/api/v1/projects", json=payload)
    assert resp1.status_code == 201

    resp2 = await client.post("/api/v1/projects", json=payload)
    assert resp2.status_code == 409
    assert "already exists" in resp2.json()["detail"]
