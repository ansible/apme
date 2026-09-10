"""Unit tests for project CRUD and dashboard REST API endpoints (ADR-037)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from apme_gateway.app import create_app
from apme_gateway.db import get_session
from apme_gateway.db import queries as q
from apme_gateway.db.models import Project, Scan, Session, Violation
from apme_gateway.scm.repo_url import normalize_repo_url

pytestmark = pytest.mark.usefixtures("gateway_db")


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
    """Seeded project appears in list with correct total_violations.

    Args:
        client: Async HTTPX test client.
    """
    await _seed_project(add_scan=True, scan_violations=5)
    resp = await client.get("/api/v1/projects")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Test Project"
    assert body["items"][0]["total_violations"] == 5


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


async def test_lookup_project_by_repo_url(client: AsyncClient) -> None:
    """GET /projects/lookup resolves a project by normalized clone URL.

    Args:
        client: Async HTTPX test client.
    """
    await _seed_project(
        repo_url="https://github.com/acme-scm/amazon.aws.git",
    )
    resp = await client.get(
        "/api/v1/projects/lookup",
        params={"repo_url": "https://GitHub.com/acme-scm/amazon.aws"},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == "proj-1"
    assert resp.json()["repo_url"] == "https://github.com/acme-scm/amazon.aws.git"


async def test_lookup_project_by_repo_url_not_found(client: AsyncClient) -> None:
    """GET /projects/lookup returns 404 when no project matches.

    Args:
        client: Async HTTPX test client.
    """
    await _seed_project()
    resp = await client.get(
        "/api/v1/projects/lookup",
        params={"repo_url": "https://github.com/other/repo"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Project not found"


async def test_lookup_project_by_repo_url_and_branch(client: AsyncClient) -> None:
    """GET /projects/lookup can disambiguate branches for the same repo URL.

    Args:
        client: Async HTTPX test client.
    """
    repo = "https://github.com/acme-scm/amazon.aws.git"
    await _seed_project(
        project_id="proj-main",
        name="amazon-main",
        repo_url=repo,
        branch="main",
    )
    await _seed_project(
        project_id="proj-backup",
        name="amazon-backup",
        repo_url=repo,
        branch="backup",
    )

    main_resp = await client.get(
        "/api/v1/projects/lookup",
        params={"repo_url": repo, "branch": "main"},
    )
    assert main_resp.status_code == 200
    assert main_resp.json()["id"] == "proj-main"
    assert main_resp.json()["branch"] == "main"

    backup_resp = await client.get(
        "/api/v1/projects/lookup",
        params={"repo_url": repo, "branch": "backup"},
    )
    assert backup_resp.status_code == 200
    assert backup_resp.json()["id"] == "proj-backup"
    assert backup_resp.json()["branch"] == "backup"


async def test_create_project_populates_normalized_url(client: AsyncClient) -> None:
    """POST /projects stores the canonical URL for indexed lookup.

    Args:
        client: Async HTTPX test client.
    """
    resp = await client.post(
        "/api/v1/projects",
        json={
            "name": "Normalized Project",
            "repo_url": "https://GitHub.com/org/Repo.git",
            "branch": "main",
        },
    )
    assert resp.status_code == 201
    async with get_session() as db:
        proj = await q.resolve_project(db, resp.json()["id"])
    assert proj is not None
    assert proj.normalized_repo_url == "https://github.com/org/Repo"


async def test_lookup_finds_indexed_row_by_variant_url(client: AsyncClient) -> None:
    """Lookup resolves variant spellings against the indexed column.

    Args:
        client: Async HTTPX test client.
    """
    created = await client.post(
        "/api/v1/projects",
        json={
            "name": "Indexed Project",
            "repo_url": "https://github.com/org/repo.git",
            "branch": "main",
        },
    )
    assert created.status_code == 201
    resp = await client.get(
        "/api/v1/projects/lookup",
        params={"repo_url": "https://GITHUB.com/org/repo"},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == created.json()["id"]


async def test_normalized_url_preserves_scheme_and_port(client: AsyncClient) -> None:
    """Canonical URLs keep scheme and explicit ports; paths keep case.

    Args:
        client: Async HTTPX test client.
    """
    resp = await client.post(
        "/api/v1/projects",
        json={
            "name": "Port Project",
            "repo_url": "https://git.example.com:8443/org/Repo.git",
            "branch": "main",
            "scm_provider": "github",
        },
    )
    assert resp.status_code == 201
    async with get_session() as db:
        proj = await q.resolve_project(db, resp.json()["id"])
    assert proj is not None
    assert proj.normalized_repo_url == "https://git.example.com:8443/org/Repo"

    other = await client.post(
        "/api/v1/projects",
        json={
            "name": "Other Port Project",
            "repo_url": "https://git.example.com/org/Repo.git",
            "branch": "main",
            "scm_provider": "github",
        },
    )
    assert other.status_code == 201
    async with get_session() as db:
        other_proj = await q.resolve_project(db, other.json()["id"])
    assert other_proj is not None
    assert other_proj.normalized_repo_url != proj.normalized_repo_url


async def test_update_project_refreshes_normalized_url(client: AsyncClient) -> None:
    """PATCH repo_url keeps the indexed canonical URL in sync.

    Args:
        client: Async HTTPX test client.
    """
    created = await client.post(
        "/api/v1/projects",
        json={
            "name": "Moving Project",
            "repo_url": "https://github.com/org/old.git",
            "branch": "main",
        },
    )
    assert created.status_code == 201
    project_id = created.json()["id"]
    patched = await client.patch(
        f"/api/v1/projects/{project_id}",
        json={"repo_url": "https://github.com/org/new.git"},
    )
    assert patched.status_code == 200
    found = await client.get(
        "/api/v1/projects/lookup",
        params={"repo_url": "https://github.com/org/new"},
    )
    assert found.status_code == 200
    assert found.json()["id"] == project_id
    gone = await client.get(
        "/api/v1/projects/lookup",
        params={"repo_url": "https://github.com/org/old"},
    )
    assert gone.status_code == 404


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


async def test_update_project_partial_without_repo_url(client: AsyncClient) -> None:
    """PATCH without repo_url strips unset fields instead of 500ing.

    The router builds ``updates`` only from explicitly set fields, so a
    partial update that omits ``repo_url`` must never reach
    ``q.update_project`` with ``repo_url=None`` (which raises ValueError).

    Args:
        client: Async HTTPX test client.
    """
    await _seed_project()
    resp = await client.patch("/api/v1/projects/proj-1", json={"branch": "main"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["branch"] == "main"
    assert body["repo_url"] == "https://github.com/test/repo.git"


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


def test_normalize_repo_url_strips_default_ports() -> None:
    """Explicit default ports collapse to the implicit identity."""
    assert normalize_repo_url("https://host.example.com:443/org/repo.git") == "https://host.example.com/org/repo"
    assert normalize_repo_url("http://host.example.com:80/org/repo") == "http://host.example.com/org/repo"
    assert normalize_repo_url("https://host.example.com:8443/org/repo") == "https://host.example.com:8443/org/repo"
    assert normalize_repo_url("http://host.example.com:443/org/repo") == "http://host.example.com:443/org/repo"
    assert normalize_repo_url("https://host.example.com/org/repo") != ("http://host.example.com/org/repo")


def test_normalize_repo_url_strips_userinfo() -> None:
    """Userinfo never participates in project identity."""
    assert normalize_repo_url("https://user:token@host.example.com/org/repo.git") == "https://host.example.com/org/repo"


def test_normalize_repo_url_malformed_port_strips_userinfo() -> None:
    """Invalid ports still strip embedded credentials from the canonical URL."""
    assert normalize_repo_url("https://user:secret@github.com:notaport/repo.git") == "https://github.com/repo"


def test_normalize_repo_url_empty_host_strips_userinfo() -> None:
    """Malformed URLs with userinfo but no host never retain credentials."""
    assert normalize_repo_url("https://user:token@") == "https://"
    assert normalize_repo_url("https://user:token@/repo") == "https:///repo"


async def test_lookup_collapses_default_port_variants(client: AsyncClient) -> None:
    """Lookup resolves explicit-default-port spellings to one project.

    Args:
        client: Async HTTPX test client.
    """
    created = await client.post(
        "/api/v1/projects",
        json={
            "name": "Default Port Project",
            "repo_url": "https://default-port.example.com:443/org/repo.git",
            "branch": "main",
            "scm_provider": "github",
        },
    )
    assert created.status_code == 201
    resp = await client.get(
        "/api/v1/projects/lookup",
        params={"repo_url": "https://default-port.example.com/org/repo"},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == created.json()["id"]


async def test_update_project_ignores_caller_normalized_url() -> None:
    """Caller-supplied normalized URL never bypasses canonical recompute."""
    async with get_session() as db:
        await q.create_project(
            db,
            project_id="proj-spoof",
            name="Spoof Project",
            repo_url="https://github.com/org/old.git",
        )
        updated = await q.update_project(
            db,
            "proj-spoof",
            repo_url="https://github.com/org/new.git",
            normalized_repo_url="https://evil.example.com/spoof",
        )
        assert updated is not None
        assert updated.repo_url == "https://github.com/org/new.git"
        assert updated.normalized_repo_url == normalize_repo_url("https://github.com/org/new.git")


async def test_update_project_rejects_none_repo_url() -> None:
    """None repo_url is rejected before a NULL write."""
    async with get_session() as db:
        await q.create_project(
            db,
            project_id="proj-null",
            name="Null Project",
            repo_url="https://github.com/org/repo.git",
        )
        with pytest.raises(ValueError, match="repo_url"):
            await q.update_project(db, "proj-null", repo_url=None)


async def test_find_project_by_repo_url_rejects_blank_target() -> None:
    """Whitespace-only URLs never match a legacy row."""
    async with get_session() as db:
        db.add(
            Project(
                id="proj-legacy",
                name="Legacy Project",
                repo_url="https://github.com/org/real.git",
                normalized_repo_url="",
                branch="main",
                created_at="2026-03-01T00:00:00Z",
                health_score=100,
            )
        )
        await db.commit()
        found = await q.find_project_by_repo_url(db, "   ")
        assert found is None


async def test_find_project_by_repo_url_paginates_legacy_fallback() -> None:
    """Legacy fallback scans past the first bounded batch."""
    target_url = "https://github.com/org/wanted.git"
    async with get_session() as db:
        for idx in range(505):
            db.add(
                Project(
                    id=f"filler-{idx:04d}-abcd1234abcd1234abcd1234abcd12",
                    name=f"Filler {idx}",
                    repo_url=f"https://github.com/org/filler-{idx}.git",
                    normalized_repo_url="",
                    branch="main",
                    created_at="2026-03-01T00:00:00Z",
                    health_score=100,
                )
            )
        db.add(
            Project(
                id="wanted-proj-1234567890abcdef1234567890ab",
                name="Wanted Project",
                repo_url=target_url,
                normalized_repo_url="",
                branch="main",
                created_at="2026-03-01T00:00:00Z",
                health_score=100,
            )
        )
        await db.commit()
        found = await q.find_project_by_repo_url(db, target_url)
        assert found is not None
        assert found.id == "wanted-proj-1234567890abcdef1234567890ab"


async def test_find_project_by_repo_url_heals_legacy_row() -> None:
    """Fallback hits persist the canonical URL so the next lookup takes the primary path."""
    target_url = "https://github.com/org/healed.git"
    canonical = normalize_repo_url(target_url)
    assert canonical
    async with get_session() as db:
        db.add(
            Project(
                id="healed-proj-1234567890abcdef1234567890ab",
                name="Healed Project",
                repo_url=target_url,
                normalized_repo_url="",
                branch="main",
                created_at="2026-03-01T00:00:00Z",
                health_score=100,
            )
        )
        await db.commit()
        found = await q.find_project_by_repo_url(db, target_url)
        assert found is not None
        assert found.id == "healed-proj-1234567890abcdef1234567890ab"

    async with get_session() as db:
        stored = await db.get(Project, "healed-proj-1234567890abcdef1234567890ab")
        assert stored is not None
        assert stored.normalized_repo_url == canonical
        second = await q.find_project_by_repo_url(db, target_url)
        assert second is not None
        assert second.id == "healed-proj-1234567890abcdef1234567890ab"


async def test_find_project_by_repo_url_heal_failure_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed isolated heal does not poison the caller session.

    Args:
        monkeypatch: Pytest fixture used to stub the isolated heal session.
    """
    from sqlalchemy import select

    from apme_gateway import db as db_module

    target_url = "https://github.com/org/heal-fail.git"
    async with get_session() as db:
        db.add(
            Project(
                id="heal-fail-proj-1234567890abcdef12345678",
                name="Heal Fail Project",
                repo_url=target_url,
                normalized_repo_url="",
                branch="main",
                created_at="2026-03-01T00:00:00Z",
                health_score=100,
            )
        )
        await db.commit()

    real_get_session = db_module.get_session
    get_session_calls = 0

    class _FailHealSession:
        """Minimal async session stub whose commit always fails."""

        async def execute(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def commit(self) -> None:
            raise RuntimeError("isolated heal commit failed")

        async def __aenter__(self) -> _FailHealSession:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    def _get_session_router() -> object:
        nonlocal get_session_calls
        get_session_calls += 1
        if get_session_calls == 1:
            return real_get_session()
        return _FailHealSession()

    monkeypatch.setattr(db_module, "get_session", _get_session_router)

    async with db_module.get_session() as db:
        found = await q.find_project_by_repo_url(db, target_url)
        assert found is not None
        assert found.id == "heal-fail-proj-1234567890abcdef12345678"
        assert get_session_calls == 2
        stored = await db.get(Project, found.id)
        assert stored is not None
        assert stored.normalized_repo_url == ""
        rows = (await db.execute(select(Project.id))).scalars().all()
        assert "heal-fail-proj-1234567890abcdef12345678" in rows


async def test_heal_project_normalized_url_swallows_commit_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Isolated heal failures are logged and do not raise to callers.

    Args:
        monkeypatch: Pytest fixture used to stub the isolated heal session.
    """
    target = normalize_repo_url("https://github.com/org/heal-swallow.git")
    assert target
    async with get_session() as db:
        await q.create_project(
            db,
            project_id="heal-swallow-proj",
            name="Heal Swallow",
            repo_url="https://github.com/org/heal-swallow.git",
        )

    class _FailSession:
        """Minimal async session stub whose commit always fails."""

        async def execute(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def commit(self) -> None:
            raise RuntimeError("boom")

        async def __aenter__(self) -> _FailSession:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr("apme_gateway.db.get_session", lambda: _FailSession())
    await q._heal_project_normalized_url("heal-swallow-proj", target)


async def test_find_project_by_repo_url_stale_normalized_index() -> None:
    """Stale non-empty normalized values fall back to raw URL matching."""
    new_url = "https://github.com/org/new-repo.git"
    old_canonical = normalize_repo_url("https://github.com/org/old-repo.git")
    assert old_canonical
    async with get_session() as db:
        db.add(
            Project(
                id="stale-norm-proj-1234567890abcdef123456",
                name="Stale Norm Project",
                repo_url=new_url,
                normalized_repo_url=old_canonical,
                branch="main",
                created_at="2026-03-01T00:00:00Z",
                health_score=100,
            )
        )
        await db.commit()
        found = await q.find_project_by_repo_url(db, new_url)
        assert found is not None
        assert found.id == "stale-norm-proj-1234567890abcdef123456"
        await db.refresh(found)
        assert found.normalized_repo_url == normalize_repo_url(new_url)
        assert await q.find_project_by_repo_url(db, "https://github.com/org/old-repo") is None


async def test_update_project_rejects_blank_repo_url() -> None:
    """Blank repo_url is rejected before colliding with the legacy sentinel."""
    async with get_session() as db:
        await q.create_project(
            db,
            project_id="proj-blank",
            name="Blank Project",
            repo_url="https://github.com/org/repo.git",
        )
        with pytest.raises(ValueError, match="repo_url"):
            await q.update_project(db, "proj-blank", repo_url="")
        with pytest.raises(ValueError, match="repo_url"):
            await q.update_project(db, "proj-blank", repo_url="   ")


async def test_find_project_by_repo_url_skips_stale_indexed_duplicate() -> None:
    """Indexed lookup skips stale rows and returns the first raw-URL match."""
    target = normalize_repo_url("https://github.com/org/wanted.git")
    async with get_session() as db:
        db.add(
            Project(
                id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                name="Stale Indexed",
                repo_url="https://github.com/org/other.git",
                normalized_repo_url=target,
                branch="main",
                created_at="2026-03-01T00:00:00Z",
                health_score=100,
            )
        )
        db.add(
            Project(
                id="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                name="Valid Indexed",
                repo_url="https://github.com/org/wanted.git",
                normalized_repo_url=target,
                branch="main",
                created_at="2026-03-01T00:00:00Z",
                health_score=100,
            )
        )
        await db.commit()
        found = await q.find_project_by_repo_url(db, "https://github.com/org/wanted.git")
        assert found is not None
        assert found.id == "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


async def test_find_project_by_repo_url_deterministic_order() -> None:
    """Duplicate normalized URLs resolve to the smallest id deterministically."""
    async with get_session() as db:
        await q.create_project(
            db,
            project_id="zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
            name="Dup Z Project",
            repo_url="https://github.com/org/dup.git",
        )
        await q.create_project(
            db,
            project_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            name="Dup A Project",
            repo_url="https://github.com/org/dup.git",
        )
        first = await q.find_project_by_repo_url(db, "https://github.com/org/dup")
        second = await q.find_project_by_repo_url(db, "https://github.com/org/dup")
        assert first is not None
        assert second is not None
        assert first.id == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        assert second.id == first.id


async def test_update_project_warns_on_normalized_url_pop(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Caller-supplied normalized_repo_url is popped with a warning.

    Args:
        caplog: Pytest log capture fixture.
    """
    async with get_session() as db:
        await q.create_project(
            db,
            project_id="proj-warn",
            name="Warn Project",
            repo_url="https://github.com/org/old.git",
        )
        with caplog.at_level("WARNING", logger="apme_gateway.db.queries"):
            updated = await q.update_project(
                db,
                "proj-warn",
                repo_url="https://github.com/org/new.git",
                normalized_repo_url="https://evil.example.com/spoof",
            )
        assert updated is not None
        assert updated.normalized_repo_url == normalize_repo_url("https://github.com/org/new.git")
        assert any("normalized_repo_url" in record.message for record in caplog.records)
