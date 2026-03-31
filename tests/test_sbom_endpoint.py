"""Tests for the /sbom REST endpoint and manifest_to_cyclonedx integration."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from apme.v1 import common_pb2, reporting_pb2
from apme_gateway.app import create_app
from apme_gateway.db import close_db, get_session, init_db
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
    ctx = MagicMock()
    ctx.abort = AsyncMock()
    return ctx


async def _seed_project_with_manifest(
    *,
    project_id: str = "proj-sbom",
    name: str = "SBOM Project",
) -> None:
    """Insert a project, session, scan, and manifest data with license/supplier fields.

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
                requirements_files_json='["requirements.yml"]',
                dependency_tree="ansible-core v2.16.3",
            )
        )
        db.add(
            ScanCollection(
                scan_id="scan-" + project_id,
                fqcn="community.general",
                version="8.0.0",
                source="specified",
                license="GPL-3.0-or-later",
                supplier="Ansible Community",
            )
        )
        db.add(
            ScanPythonPackage(
                scan_id="scan-" + project_id,
                name="jmespath",
                version="1.0.1",
                license="MIT",
                supplier="James Saryerwinnie",
            )
        )
        await db.commit()


# ── Endpoint tests ───────────────────────────────────────────────────


async def test_sbom_endpoint_returns_cyclonedx(client: AsyncClient) -> None:
    """GET /projects/{id}/sbom returns CycloneDX 1.5 JSON with correct content type.

    Args:
        client: Async HTTP test client.
    """
    await _seed_project_with_manifest()
    resp = await client.get("/api/v1/projects/proj-sbom/sbom")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/vnd.cyclonedx+json"

    data = resp.json()
    assert data["bomFormat"] == "CycloneDX"
    assert data["specVersion"] == "1.5"
    assert "components" in data
    assert "dependencies" in data

    names = {c["name"] for c in data["components"]}
    assert "ansible-core" in names
    assert "community.general" in names
    assert "jmespath" in names


async def test_sbom_endpoint_includes_license_and_supplier(client: AsyncClient) -> None:
    """SBOM components include license and supplier metadata.

    Args:
        client: Async HTTP test client.
    """
    await _seed_project_with_manifest()
    resp = await client.get("/api/v1/projects/proj-sbom/sbom")
    data = resp.json()

    coll = next(c for c in data["components"] if c["name"] == "community.general")
    assert coll["licenses"] == [{"license": {"id": "GPL-3.0-or-later"}}]
    assert coll["supplier"]["name"] == "Ansible Community"

    pkg = next(c for c in data["components"] if c["name"] == "jmespath")
    assert pkg["licenses"] == [{"license": {"id": "MIT"}}]
    assert pkg["supplier"]["name"] == "James Saryerwinnie"


async def test_sbom_endpoint_404_unknown_project(client: AsyncClient) -> None:
    """GET /projects/{id}/sbom returns 404 for unknown project.

    Args:
        client: Async HTTP test client.
    """
    resp = await client.get("/api/v1/projects/nonexistent/sbom")
    assert resp.status_code == 404


async def test_sbom_endpoint_404_no_scan_data(client: AsyncClient) -> None:
    """GET /projects/{id}/sbom returns 404 when project has no scan data.

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

    resp = await client.get("/api/v1/projects/empty-proj/sbom")
    assert resp.status_code == 404
    assert "No scan data" in resp.json()["detail"]


async def test_sbom_endpoint_400_unsupported_format(client: AsyncClient) -> None:
    """GET /projects/{id}/sbom returns 400 for unsupported format parameter.

    Args:
        client: Async HTTP test client.
    """
    await _seed_project_with_manifest()
    resp = await client.get("/api/v1/projects/proj-sbom/sbom?format=spdx")
    assert resp.status_code == 400
    assert "Unsupported" in resp.json()["detail"]


async def test_sbom_endpoint_by_project_name(client: AsyncClient) -> None:
    """GET /projects/{name}/sbom resolves by project name.

    Args:
        client: Async HTTP test client.
    """
    await _seed_project_with_manifest()
    resp = await client.get("/api/v1/projects/SBOM Project/sbom")
    assert resp.status_code == 200
    assert resp.json()["bomFormat"] == "CycloneDX"


# ── Integration: gRPC servicer -> DB -> SBOM endpoint ───────────────


async def test_roundtrip_servicer_to_sbom(client: AsyncClient) -> None:
    """Full round-trip: persist manifest via ReportingServicer, then query SBOM endpoint.

    Args:
        client: Async HTTP test client.
    """
    # Create project and link scan via DB seeding (servicer sets project_id=None)
    async with get_session() as db:
        db.add(
            Project(
                id="proj-rt",
                name="Roundtrip",
                repo_url="https://github.com/test/rt.git",
                branch="main",
                created_at="2026-03-01T00:00:00Z",
                health_score=90,
            )
        )
        await db.commit()

    # Persist via gRPC servicer
    servicer = ReportingServicer()
    manifest = common_pb2.ProjectManifest(
        ansible_core_version="2.17.0",
        collections=[
            common_pb2.CollectionRef(
                fqcn="amazon.aws",
                version="7.0.0",
                source="specified",
                license="Apache-2.0",
                supplier="Amazon",
            ),
        ],
        python_packages=[
            common_pb2.PythonPackageRef(
                name="boto3",
                version="1.34.0",
                license="Apache-2.0",
                supplier="Amazon Web Services",
            ),
        ],
        requirements_files=["requirements.yml"],
        dependency_tree="ansible-core v2.17.0",
    )
    event = reporting_pb2.FixCompletedEvent(
        scan_id="scan-rt",
        session_id="sess-rt",
        project_path="/proj",
        source="cli",
        manifest=manifest,
    )
    await servicer.ReportFixCompleted(event, _mock_context())

    # Link the scan to the project (servicer doesn't do this automatically)
    async with get_session() as db:
        from sqlalchemy import update  # noqa: PLC0415

        await db.execute(update(Scan).where(Scan.scan_id == "scan-rt").values(project_id="proj-rt"))
        await db.commit()

    # Query SBOM endpoint
    resp = await client.get("/api/v1/projects/proj-rt/sbom")
    assert resp.status_code == 200
    data = resp.json()

    assert data["bomFormat"] == "CycloneDX"
    names = {c["name"] for c in data["components"]}
    assert "ansible-core" in names
    assert "amazon.aws" in names
    assert "boto3" in names

    # Verify license/supplier round-tripped
    aws_coll = next(c for c in data["components"] if c["name"] == "amazon.aws")
    assert aws_coll["licenses"] == [{"license": {"id": "Apache-2.0"}}]
    assert aws_coll["supplier"]["name"] == "Amazon"

    boto_pkg = next(c for c in data["components"] if c["name"] == "boto3")
    assert boto_pkg["licenses"] == [{"license": {"id": "Apache-2.0"}}]
    assert boto_pkg["supplier"]["name"] == "Amazon Web Services"


# ── manifest_to_cyclonedx unit tests ────────────────────────────────


def test_manifest_to_cyclonedx_empty_collections() -> None:
    """Empty collections and packages produce BOM with just ansible-core."""
    from apme_gateway.api.sbom import manifest_to_cyclonedx  # noqa: PLC0415

    manifest = ScanManifest(
        scan_id="test",
        ansible_core_version="2.16.0",
        requirements_files_json="[]",
        dependency_tree="",
    )
    result = manifest_to_cyclonedx(manifest, [], [], tools_version="0.1.0")
    assert result["bomFormat"] == "CycloneDX"
    assert len(result["components"]) == 1
    assert result["components"][0]["name"] == "ansible-core"


def test_manifest_to_cyclonedx_purl_format() -> None:
    """Components have correct PURL format."""
    from apme_gateway.api.sbom import manifest_to_cyclonedx  # noqa: PLC0415

    manifest = ScanManifest(
        scan_id="test",
        ansible_core_version="2.16.0",
        requirements_files_json="[]",
        dependency_tree="",
    )
    coll = ScanCollection(
        scan_id="test", fqcn="cisco.ios", version="2.0.0", source="specified", license="", supplier=""
    )
    pkg = ScanPythonPackage(scan_id="test", name="ruamel.yaml", version="0.18.0", license="", supplier="")
    result = manifest_to_cyclonedx(manifest, [coll], [pkg], tools_version="0.1.0")

    purls = {c["name"]: c["purl"] for c in result["components"]}
    assert purls["ansible-core"] == "pkg:pypi/ansible-core@2.16.0"
    assert purls["cisco.ios"] == "pkg:generic/cisco.ios@2.0.0?repository_url=https://galaxy.ansible.com"
    assert purls["ruamel.yaml"] == "pkg:pypi/ruamel-yaml@0.18.0"
