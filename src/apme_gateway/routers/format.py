"""POST /api/v1/format — format Ansible files via Primary.FormatStream."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from apme.v1.common_pb2 import File
from apme.v1.primary_pb2 import ScanChunk
from apme_gateway.deps import get_primary_client, validate_project_path
from apme_gateway.models.schemas import FileDiffOut, FormatOut, FormatRequest
from apme_gateway.services.grpc_client import PrimaryClient
from apme_gateway.services.scan_service import _discover_files

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["format"])

CHUNK_MAX_BYTES = 1024 * 1024


async def _format_chunks(files: list[tuple[str, bytes]]) -> AsyncIterator[ScanChunk]:
    """Yield ScanChunk messages for FormatStream.

    Args:
        files: Discovered (relative_path, content) pairs.

    Yields:
        ScanChunk messages.
    """
    if not files:
        yield ScanChunk(files=[], last=True)
        return

    batch: list[File] = []
    batch_bytes = 0

    for rel, content in files:
        msg_size = len(rel.encode()) + len(content)
        if batch and batch_bytes + msg_size > CHUNK_MAX_BYTES:
            yield ScanChunk(files=batch, last=False)
            batch = []
            batch_bytes = 0
        batch.append(File(path=rel, content=content))
        batch_bytes += msg_size

    yield ScanChunk(files=batch, last=True)


@router.post("/format", response_model=FormatOut)
async def format_files(
    body: FormatRequest,
    client: PrimaryClient = Depends(get_primary_client),
) -> FormatOut:
    """Format Ansible files and return diffs."""
    root = validate_project_path(body.project_path)

    files = _discover_files(root)
    try:
        resp = await client.format_stream(_format_chunks(files))
    except Exception as exc:
        logger.exception("Format failed")
        raise HTTPException(status_code=502, detail="Engine error: format failed") from exc

    diffs = [FileDiffOut(path=d.path, diff=d.diff) for d in resp.diffs]
    return FormatOut(diffs=diffs, total=len(diffs))
