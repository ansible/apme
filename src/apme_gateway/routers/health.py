"""Health endpoint — aggregate status from all backend gRPC services."""

from __future__ import annotations

from fastapi import APIRouter

from apme_gateway.models.schemas import HealthOut, ServiceHealth
from apme_gateway.services.grpc_client import HealthProbe

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
async def health_check():
    results = await HealthProbe.probe_all()
    services = [
        ServiceHealth(
            name=r["name"],
            status=str(r["status"]),
            latency_ms=r.get("latency_ms"),
        )
        for r in results
    ]
    all_ok = all(s.status.startswith("ok") for s in services)
    return HealthOut(
        status="ok" if all_ok else "degraded",
        services=services,
    )
