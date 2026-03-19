"""Tests for the /api/v1/health endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from httpx import AsyncClient


class TestHealthEndpoint:
    """Tests for GET /api/v1/health."""

    async def test_health_all_ok(self, client: AsyncClient) -> None:
        """When all services report ok, overall status is ok."""
        mock_results = [
            {"name": "primary", "status": "ok", "latency_ms": 1.2},
            {"name": "native", "status": "ok", "latency_ms": 0.8},
            {"name": "opa", "status": "ok", "latency_ms": 1.0},
        ]
        with patch(
            "apme_gateway.routers.health.HealthProbe.probe_all", new_callable=AsyncMock, return_value=mock_results
        ):
            resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert len(data["services"]) == 3
        assert all(s["status"] == "ok" for s in data["services"])

    async def test_health_degraded(self, client: AsyncClient) -> None:
        """When any service reports non-ok, overall status is degraded."""
        mock_results = [
            {"name": "primary", "status": "ok", "latency_ms": 1.0},
            {"name": "native", "status": "error: connection refused", "latency_ms": 3000.0},
        ]
        with patch(
            "apme_gateway.routers.health.HealthProbe.probe_all", new_callable=AsyncMock, return_value=mock_results
        ):
            resp = await client.get("/api/v1/health")
        data = resp.json()
        assert data["status"] == "degraded"

    async def test_health_verbose_ok_counts_as_ok(self, client: AsyncClient) -> None:
        """Services returning 'ok (version info)' are treated as ok."""
        mock_results = [
            {"name": "gitleaks", "status": "ok (gitleaks v8.30.0)", "latency_ms": 2.0},
        ]
        with patch(
            "apme_gateway.routers.health.HealthProbe.probe_all", new_callable=AsyncMock, return_value=mock_results
        ):
            resp = await client.get("/api/v1/health")
        data = resp.json()
        assert data["status"] == "ok"
