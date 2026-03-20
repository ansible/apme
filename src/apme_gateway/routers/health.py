"""GET /api/v1/health — aggregate gateway + backend health."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from apme_gateway.deps import get_primary_client
from apme_gateway.models.schemas import HealthOut, ServiceHealthOut
from apme_gateway.services.grpc_client import PrimaryClient

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get("/health", response_model=HealthOut)
async def health(
    client: PrimaryClient = Depends(get_primary_client),
) -> HealthOut:
    """Aggregate health: gateway status + gRPC health from Primary."""
    try:
        resp = await client.health()
    except Exception:
        logger.exception("Primary health check failed")
        return HealthOut(
            gateway="ok",
            primary=ServiceHealthOut(name="primary", status="unreachable"),
        )

    downstream = [ServiceHealthOut(name=s.name, status=s.status, address=s.address) for s in resp.downstream]
    return HealthOut(
        gateway="ok",
        primary=ServiceHealthOut(name="primary", status=resp.status),
        downstream=downstream,
    )
