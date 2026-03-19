"""Fix pipeline endpoints — trigger and monitor fix jobs.

Fix jobs are long-running: the gateway stores job state in-memory and
exposes a WebSocket for real-time progress.  A production deployment would
use a task queue (e.g., Celery, arq) but the in-memory store is sufficient
for single-pod deployments.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from apme_gateway.deps import get_primary_client
from apme_gateway.models.schemas import FixCreate, FixJobStatus
from apme_gateway.services.grpc_client import PrimaryClient

router = APIRouter(prefix="/fix", tags=["fix"])

_jobs: dict[str, dict[str, Any]] = {}


@router.post("", response_model=FixJobStatus, status_code=202)
async def create_fix_job(
    body: FixCreate,
    client: PrimaryClient = Depends(get_primary_client),
):
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "status": "queued",
        "progress": [],
        "result": None,
        "body": body,
    }
    asyncio.create_task(_run_fix(job_id, body, client))
    return FixJobStatus(job_id=job_id, status="queued")


async def _run_fix(job_id: str, body: FixCreate, client: PrimaryClient) -> None:
    """Run format → scan → report pipeline in background."""
    job = _jobs[job_id]
    try:
        job["status"] = "running"
        job["progress"].append({"phase": "format", "status": "running"})

        await client.format_files(body.files)
        job["progress"][-1]["status"] = "completed"

        job["progress"].append({"phase": "scan", "status": "running"})
        resp = await client.scan(
            body.files,
            body.project_name,
            ansible_core_version=body.ansible_core_version,
            collection_specs=body.collection_specs,
        )
        job["progress"][-1]["status"] = "completed"
        job["progress"][-1]["violations"] = len(resp.violations)

        job["status"] = "completed"
        job["result"] = {
            "total_violations": len(resp.violations),
            "scan_id": resp.scan_id,
        }
    except Exception as exc:
        job["status"] = "failed"
        job["progress"].append({"phase": "error", "detail": str(exc)})


@router.get("/{job_id}", response_model=FixJobStatus)
async def get_fix_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Job not found")
    return FixJobStatus(
        job_id=job_id,
        status=job["status"],
        progress=job["progress"],
        result=job["result"],
    )


@router.websocket("/{job_id}/stream")
async def fix_stream(websocket: WebSocket, job_id: str):
    """Stream fix job progress events over WebSocket."""
    await websocket.accept()
    seen = 0
    try:
        while True:
            job = _jobs.get(job_id)
            if not job:
                await websocket.send_json({"event": "error", "detail": "job not found"})
                break
            progress = job["progress"]
            for event in progress[seen:]:
                await websocket.send_json(event)
            seen = len(progress)
            if job["status"] in ("completed", "failed"):
                await websocket.send_json({"event": "done", "status": job["status"], "result": job["result"]})
                break
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
