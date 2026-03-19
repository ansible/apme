"""Format endpoint — normalize YAML files via Primary.Format."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from apme_gateway.deps import get_primary_client
from apme_gateway.models.schemas import FormatDiffOut, FormatRequest
from apme_gateway.services.grpc_client import PrimaryClient

router = APIRouter(tags=["format"])


@router.post("/format", response_model=list[FormatDiffOut])
async def format_files(
    body: FormatRequest,
    client: PrimaryClient = Depends(get_primary_client),
):
    resp = await client.format_files(body.files)
    return [FormatDiffOut(path=d.path, diff=d.diff) for d in resp.diffs]
