"""Reporting ingest endpoint — accepts ScanCompleted events from Primary.

Primary emits these events after every scan completion (ADR-020). This allows
CLI-initiated scans to appear in the web dashboard without the scan having
been initiated through the gateway.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from apme_gateway.deps import get_db
from apme_gateway.models.tables import Scan, Violation

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["reports"])


class ViolationEvent(BaseModel):
    """A single violation in a ScanCompleted event."""

    rule_id: str
    level: str
    message: str
    file: str
    line: int | None = None
    path: str = ""


class ScanCompletedEvent(BaseModel):
    """Event emitted by Primary after every scan completion (ADR-020).

    Primary POSTs this to the gateway so all scan results — CLI, web, CI —
    appear in the dashboard.
    """

    scan_id: str
    project_path: str
    violations: list[ViolationEvent] = Field(default_factory=list)
    diagnostics: dict[str, object] | None = None
    summary: dict[str, object] | None = None
    source: str = "cli"


@router.post("/reports", status_code=201)
async def ingest_scan_completed(
    event: ScanCompletedEvent,
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Persist a ScanCompleted event from Primary.

    Idempotent: if a scan with the same ID already exists (e.g. gateway-
    initiated scan), the event is silently ignored.
    """
    existing = await session.get(Scan, event.scan_id)
    if existing is not None:
        return {"status": "exists", "scan_id": event.scan_id}

    import json

    now = datetime.now(tz=timezone.utc).isoformat()
    diag_json = json.dumps(event.diagnostics) if event.diagnostics else None

    scan = Scan(
        id=event.scan_id,
        project_path=event.project_path,
        created_at=now,
        status="completed",
        total_violations=len(event.violations),
        diagnostics=diag_json,
    )

    violations_orm = [
        Violation(
            id=uuid.uuid4().hex,
            scan_id=event.scan_id,
            rule_id=v.rule_id,
            level=v.level,
            message=v.message,
            file=v.file,
            line=v.line,
            path=v.path,
        )
        for v in event.violations
    ]

    session.add(scan)
    session.add_all(violations_orm)
    await session.commit()

    logger.info(
        "Ingested ScanCompleted: scan_id=%s violations=%d source=%s",
        event.scan_id,
        len(event.violations),
        event.source,
    )
    return {"status": "created", "scan_id": event.scan_id}
