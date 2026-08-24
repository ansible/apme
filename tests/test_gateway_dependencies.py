"""Unit tests for dependency manifest persistence and REST API (ADR-040)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from apme.v1 import common_pb2, reporting_pb2
from apme_gateway.app import create_app
from apme_gateway.db import close_db, get_session, init_db
from apme_gateway.db import queries as q
from apme_gateway.db.models import (
    Project,
    Scan,
    ScanCollection,
    ScanManifest,
    ScanPythonPackage,
    Session,
)
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


async def _seed_project_with_manifest(
    *,
    project_id: str = "proj-1",
    name: str = "Test Project",
) -> None:
    """Insert a project, session, scan, and manifest data.

    Args:
        project_id: Project UUID.
        name: Project display name.
    """
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
            ScanManifest(
                scan_id="scan-" + project_id,
                ansible_core_version="2.16.3",
                requirements_files_json='["requirements.yml", "collections/requirements.yml"]',
                dependency_tree="ansible-core v2.16.3\n├── jinja2 v3.1.4\n└── pyyaml v6.0.3",
            )
        )
        db.add(
            ScanCollection(scan_id="scan-" + project_id, fqcn="community.general", version="8.0.0", source="specified")
        )
        db.add(
            ScanCollection(scan_id="scan-" + project_id, fqcn="ansible.netcommon", version="6.1.0", source="dependency")
        )
        db.add(ScanPythonPackage(scan_id="scan-" + project_id, name="jmespath", version="1.0.1"))
        db.add(ScanPythonPackage(scan_id="scan-" + project_id, name="netaddr", version="0.10.1"))
        await db.commit()


# ── Servicer manifest persistence tests ──────────────────────────────


async def test_report_fix_with_manifest_persists() -> None:
    """Manifest data from FixCompletedEvent is persisted."""
    servicer = ReportingServicer()
    manifest = common_pb2.ProjectManifest(
        ansible_core_version="2.16.3",
        collections=[
            common_pb2.CollectionRef(fqcn="community.general", version="8.0.0", source="specified"),
            common_pb2.CollectionRef(fqcn="ansible.netcommon", version="6.1.0", source="dependency"),
        ],
        python_packages=[
            common_pb2.PythonPackageRef(name="jmespath", version="1.0.1"),
        ],
        requirements_files=["requirements.yml"],
        dependency_tree="ansible-core v2.16.3\n├── jinja2 v3.1.4\n└── pyyaml v6.0.3",
    )
    event = reporting_pb2.FixCompletedEvent(
        scan_id="scan-m1",
        session_id="sess-m1",
        project_path="/proj",
        source="cli",
        manifest=manifest,
    )
    await servicer.ReportFixCompleted(event, _mock_context())

    async with get_session() as db:
        scan = await q.get_scan(db, "scan-m1")
        assert scan is not None

        from sqlalchemy import select

        m_result = await db.execute(select(ScanManifest).where(ScanManifest.scan_id == "scan-m1"))
        m = m_result.scalar_one_or_none()
        assert m is not None
        assert m.ansible_core_version == "2.16.3"
        assert "requirements.yml" in m.requirements_files_json
        assert "ansible-core v2.16.3" in m.dependency_tree

        c_result = await db.execute(select(ScanCollection).where(ScanCollection.scan_id == "scan-m1"))
        colls = list(c_result.scalars().all())
        assert len(colls) == 2
        fqcns = {c.fqcn for c in colls}
        assert "community.general" in fqcns
        assert "ansible.netcommon" in fqcns

        p_result = await db.execute(select(ScanPythonPackage).where(ScanPythonPackage.scan_id == "scan-m1"))
        pkgs = list(p_result.scalars().all())
        assert len(pkgs) == 1
        assert pkgs[0].name == "jmespath"


async def test_report_fix_deduplicates_duplicate_python_packages() -> None:
    """Duplicate python package names in manifest do not fail persistence."""
    servicer = ReportingServicer()
    manifest = common_pb2.ProjectManifest(
        python_packages=[
            common_pb2.PythonPackageRef(
                name="ansible-collection-ansible-posix",
                version="2.2.1",
                supplier="Ansible (github.com/ansible)",
            ),
            common_pb2.PythonPackageRef(
                name="ansible-collection-ansible-posix",
                version="2.2.1",
                supplier="Ansible (github.com/ansible)",
            ),
        ],
    )
    event = reporting_pb2.FixCompletedEvent(
        scan_id="scan-dedupe",
        session_id="sess-dedupe",
        project_path="/proj",
        source="cli",
        manifest=manifest,
    )

    await servicer.ReportFixCompleted(event, _mock_context())

    async with get_session() as db:
        from sqlalchemy import select

        p_result = await db.execute(select(ScanPythonPackage).where(ScanPythonPackage.scan_id == "scan-dedupe"))
        pkgs = list(p_result.scalars().all())
        assert len(pkgs) == 1
        assert pkgs[0].name == "ansible-collection-ansible-posix"


async def test_report_fix_without_manifest_ok() -> None:
    """Fix event without manifest still persists normally."""
    servicer = ReportingServicer()
    event = reporting_pb2.FixCompletedEvent(
        scan_id="scan-no-m",
        session_id="sess-no-m",
        project_path="/proj",
        source="cli",
    )
    await servicer.ReportFixCompleted(event, _mock_context())

    async with get_session() as db:
        scan = await q.get_scan(db, "scan-no-m")
        assert scan is not None

        from sqlalchemy import select

        m_result = await db.execute(select(ScanManifest).where(ScanManifest.scan_id == "scan-no-m"))
        assert m_result.scalar_one_or_none() is None


# ── REST API tests ────────────────────────────────────────────────────


async def test_project_dependencies_endpoint(client: AsyncClient) -> None:
    """GET /projects/{id}/dependencies returns manifest data.

    Args:
        client: Async HTTP test client.
    """
    await _seed_project_with_manifest()

    resp = await client.get("/api/v1/projects/proj-1/dependencies")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ansible_core_version"] == "2.16.3"
    assert len(data["collections"]) == 2
    fqcns = {c["fqcn"] for c in data["collections"]}
    assert "community.general" in fqcns
    assert len(data["python_packages"]) == 2
    assert len(data["requirements_files"]) == 2
    assert "ansible-core v2.16.3" in data["dependency_tree"]


async def test_project_dependencies_by_name(client: AsyncClient) -> None:
    """GET /projects/{name}/dependencies resolves by project name.

    Args:
        client: Async HTTP test client.
    """
    await _seed_project_with_manifest()

    resp = await client.get("/api/v1/projects/Test Project/dependencies")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ansible_core_version"] == "2.16.3"
    assert len(data["collections"]) == 2


async def test_project_dependencies_404(client: AsyncClient) -> None:
    """GET /projects/{id}/dependencies returns 404 for unknown project.

    Args:
        client: Async HTTP test client.
    """
    resp = await client.get("/api/v1/projects/nonexistent/dependencies")
    assert resp.status_code == 404


async def test_project_dependencies_empty(client: AsyncClient) -> None:
    """GET /projects/{id}/dependencies returns empty manifest for project with no scans.

    Args:
        client: Async HTTP test client.
    """
    async with get_session() as db:
        db.add(
            Project(
                id="empty-proj",
                name="Empty",
                repo_url="https://github.com/test/empty.git",
                branch="main",
                created_at="2026-03-01T00:00:00Z",
                health_score=100,
            )
        )
        await db.commit()

    resp = await client.get("/api/v1/projects/empty-proj/dependencies")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ansible_core_version"] == ""
    assert data["collections"] == []
    assert data["python_packages"] == []


async def test_list_collections(client: AsyncClient) -> None:
    """GET /collections returns all collections with usage counts.

    Args:
        client: Async HTTP test client.
    """
    await _seed_project_with_manifest()
    await _seed_project_with_manifest(project_id="proj-2", name="Project 2")

    resp = await client.get("/api/v1/collections")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    fqcns = {c["fqcn"] for c in data}
    assert "community.general" in fqcns
    assert "ansible.netcommon" in fqcns
    for c in data:
        assert c["project_count"] == 2


async def test_collection_detail(client: AsyncClient) -> None:
    """GET /collections/{fqcn} returns detail for a collection.

    Args:
        client: Async HTTP test client.
    """
    await _seed_project_with_manifest()

    resp = await client.get("/api/v1/collections/community.general")
    assert resp.status_code == 200
    data = resp.json()
    assert data["fqcn"] == "community.general"
    assert "8.0.0" in data["versions"]
    assert data["project_count"] == 1
    assert len(data["projects"]) == 1
    assert data["projects"][0]["name"] == "Test Project"


async def test_collection_detail_404(client: AsyncClient) -> None:
    """GET /collections/{fqcn} returns 404 for unknown collection.

    Args:
        client: Async HTTP test client.
    """
    resp = await client.get("/api/v1/collections/does.not.exist")
    assert resp.status_code == 404


async def test_collection_projects(client: AsyncClient) -> None:
    """GET /collections/{fqcn}/projects returns dependent projects.

    Args:
        client: Async HTTP test client.
    """
    await _seed_project_with_manifest()

    resp = await client.get("/api/v1/collections/community.general/projects")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == "proj-1"
    assert data[0]["health_score"] == 85
    assert data[0]["collection_version"] == "8.0.0"


async def test_list_python_packages(client: AsyncClient) -> None:
    """GET /python-packages returns all packages with usage counts.

    Args:
        client: Async HTTP test client.
    """
    await _seed_project_with_manifest()

    resp = await client.get("/api/v1/python-packages")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    names = {p["name"] for p in data}
    assert "jmespath" in names
    assert "netaddr" in names


async def test_python_package_detail(client: AsyncClient) -> None:
    """GET /python-packages/{name} returns detail for a package.

    Args:
        client: Async HTTP test client.
    """
    await _seed_project_with_manifest()

    resp = await client.get("/api/v1/python-packages/jmespath")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "jmespath"
    assert "1.0.1" in data["versions"]
    assert data["project_count"] == 1


async def test_python_package_detail_404(client: AsyncClient) -> None:
    """GET /python-packages/{name} returns 404 for unknown package.

    Args:
        client: Async HTTP test client.
    """
    resp = await client.get("/api/v1/python-packages/nonexistent")
    assert resp.status_code == 404
