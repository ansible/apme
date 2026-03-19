"""Tests for the /api/v1/scans endpoints via httpx AsyncClient."""

from __future__ import annotations

import uuid

from httpx import AsyncClient


class TestIngestEndpoint:
    """Tests for POST /api/v1/scans/ingest."""

    async def test_ingest_returns_201(self, client: AsyncClient) -> None:
        """Ingesting a valid payload returns 201 and a scan summary."""
        resp = await client.post(
            "/api/v1/scans/ingest",
            json={
                "project_name": "test-project",
                "violations": [
                    {"rule_id": "L002", "level": "error", "message": "bad indent", "file": "site.yml"},
                ],
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["project_name"] == "test-project"
        assert data["total_violations"] == 1
        assert data["error_count"] == 1
        assert data["scan_type"] == "scan"

    async def test_ingest_fix_type(self, client: AsyncClient) -> None:
        """Ingesting a fix-type scan with fixed_count is reflected in response."""
        resp = await client.post(
            "/api/v1/scans/ingest",
            json={
                "project_name": "fixed-project",
                "scan_type": "fix",
                "fixed_count": 3,
                "violations": [],
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["scan_type"] == "fix"
        assert data["fixed_count"] == 3

    async def test_ingest_dedup_same_request_id(self, client: AsyncClient) -> None:
        """Two ingests with the same request_id return the same scan id."""
        rid = str(uuid.uuid4())
        payload = {
            "project_name": "dedup-test",
            "violations": [],
            "request_id": rid,
        }
        resp1 = await client.post("/api/v1/scans/ingest", json=payload)
        resp2 = await client.post("/api/v1/scans/ingest", json=payload)
        assert resp1.status_code == 201
        assert resp2.status_code == 201
        assert resp1.json()["id"] == resp2.json()["id"]

    async def test_ingest_empty_project_name_rejected(self, client: AsyncClient) -> None:
        """Missing project_name is rejected by Pydantic validation."""
        resp = await client.post("/api/v1/scans/ingest", json={"violations": []})
        assert resp.status_code == 422


class TestListEndpoint:
    """Tests for GET /api/v1/scans."""

    async def test_list_empty(self, client: AsyncClient) -> None:
        """Empty database returns paginated empty list."""
        resp = await client.get("/api/v1/scans")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    async def test_list_after_ingest(self, client: AsyncClient) -> None:
        """Ingested scan appears in the list."""
        await client.post(
            "/api/v1/scans/ingest",
            json={"project_name": "listed-project", "violations": []},
        )
        resp = await client.get("/api/v1/scans")
        data = resp.json()
        assert data["total"] >= 1
        names = [s["project_name"] for s in data["items"]]
        assert "listed-project" in names

    async def test_list_filter_by_project(self, client: AsyncClient) -> None:
        """Project query param filters results."""
        await client.post(
            "/api/v1/scans/ingest",
            json={
                "project_name": "unique-alpha-xyz",
                "violations": [],
                "request_id": str(uuid.uuid4()),
            },
        )
        resp = await client.get("/api/v1/scans", params={"project": "unique-alpha-xyz"})
        data = resp.json()
        assert data["total"] >= 1
        assert all("unique-alpha-xyz" in s["project_name"] for s in data["items"])


class TestGetEndpoint:
    """Tests for GET /api/v1/scans/{scan_id}."""

    async def test_get_existing(self, client: AsyncClient) -> None:
        """Fetching an ingested scan returns full detail."""
        create_resp = await client.post(
            "/api/v1/scans/ingest",
            json={
                "project_name": "detail-test",
                "violations": [
                    {"rule_id": "L002", "level": "error", "message": "bad", "file": "a.yml"},
                ],
            },
        )
        scan_id = create_resp.json()["id"]
        resp = await client.get(f"/api/v1/scans/{scan_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == scan_id
        assert len(data["violations"]) == 1
        assert data["violations"][0]["rule_id"] == "L002"

    async def test_get_nonexistent_returns_404(self, client: AsyncClient) -> None:
        """Requesting a non-existent scan returns 404."""
        resp = await client.get("/api/v1/scans/does-not-exist")
        assert resp.status_code == 404


class TestDeleteEndpoint:
    """Tests for DELETE /api/v1/scans/{scan_id}."""

    async def test_delete_existing(self, client: AsyncClient) -> None:
        """Deleting an existing scan returns 204."""
        create_resp = await client.post(
            "/api/v1/scans/ingest",
            json={"project_name": "del-test", "violations": []},
        )
        scan_id = create_resp.json()["id"]
        resp = await client.delete(f"/api/v1/scans/{scan_id}")
        assert resp.status_code == 204

    async def test_delete_nonexistent_returns_404(self, client: AsyncClient) -> None:
        """Deleting a non-existent scan returns 404."""
        resp = await client.delete("/api/v1/scans/no-such-scan")
        assert resp.status_code == 404

    async def test_delete_removes_from_list(self, client: AsyncClient) -> None:
        """After deletion, the scan no longer appears in list results."""
        create_resp = await client.post(
            "/api/v1/scans/ingest",
            json={"project_name": "del-check", "violations": [], "request_id": str(uuid.uuid4())},
        )
        scan_id = create_resp.json()["id"]
        await client.delete(f"/api/v1/scans/{scan_id}")
        resp = await client.get(f"/api/v1/scans/{scan_id}")
        assert resp.status_code == 404
