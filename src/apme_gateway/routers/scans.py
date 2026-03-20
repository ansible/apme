"""Scan CRUD endpoints: POST, GET list, GET detail, DELETE."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from apme_gateway.deps import get_db, get_primary_client
from apme_gateway.models.schemas import (
    ScanCreate,
    ScanListItem,
    ScanListOut,
    ScanOut,
    ScanSummaryOut,
    ViolationOut,
)
from apme_gateway.services.grpc_client import PrimaryClient
from apme_gateway.services.scan_service import (
    delete_scan,
    get_scan,
    list_scans,
    trigger_scan,
    wait_for_scan,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["scans"])


@router.post("/scans", response_model=ScanOut, status_code=201)
async def initiate_scan(
    body: ScanCreate,
    client: PrimaryClient = Depends(get_primary_client),
    session: AsyncSession = Depends(get_db),
) -> ScanOut:
    """Initiate a scan against a local directory or repository URL.

    Triggers Primary's ScanStream, then polls the DB until the event
    subscriber has persisted the result (single-writer pattern).
    """
    try:
        scan_id = await trigger_scan(
            project_path=body.project_path,
            client=client,
            ansible_core_version=body.ansible_core_version,
            collection_specs=body.collection_specs or None,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Scan failed")
        raise HTTPException(status_code=502, detail=f"Engine error: {exc}") from exc

    scan = await wait_for_scan(session, scan_id)
    if scan is None:
        raise HTTPException(
            status_code=504,
            detail=f"Scan {scan_id} completed but event subscriber did not persist within timeout",
        )

    return _scan_to_out(scan)


@router.get("/scans", response_model=ScanListOut)
async def list_scans_endpoint(
    page: int = 1,
    page_size: int = 20,
    session: AsyncSession = Depends(get_db),
) -> ScanListOut:
    """List scan history, paginated."""
    scans, total = await list_scans(session, page=page, page_size=page_size)
    items = []
    for s in scans:
        secrets = errors = warnings = infos = hints = fixed = 0
        for v in s.violations:
            if v.rule_id.startswith("SEC"):
                secrets += 1
                continue
            lv = v.level.lower()
            if lv in ("very_high", "high", "error", "fatal"):
                errors += 1
            elif lv in ("medium", "low", "warning", "warn"):
                warnings += 1
            elif lv in ("very_low", "info"):
                infos += 1
            else:
                hints += 1
        if s.summary_json:
            try:
                summ = json.loads(s.summary_json)
                fixed = int(summ.get("auto_fixable", 0))
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
        items.append(
            ScanListItem(
                id=s.id,
                project_path=s.project_path,
                created_at=s.created_at,
                status=s.status,
                total_violations=s.total_violations,
                source=getattr(s, "source", "engine"),
                secrets=secrets,
                errors=errors,
                warnings=warnings,
                infos=infos,
                hints=hints,
                fixed=fixed,
            ),
        )
    return ScanListOut(items=items, total=total, page=page, page_size=page_size)


@router.get("/scans/{scan_id}", response_model=ScanOut)
async def get_scan_endpoint(
    scan_id: str,
    session: AsyncSession = Depends(get_db),
) -> ScanOut:
    """Get a single scan result with violations."""
    scan = await get_scan(session, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    return _scan_to_out(scan)


@router.delete("/scans/{scan_id}", status_code=204)
async def delete_scan_endpoint(
    scan_id: str,
    session: AsyncSession = Depends(get_db),
) -> None:
    """Delete a scan record and its associated data."""
    deleted = await delete_scan(session, scan_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Scan not found")


def _scan_to_out(scan: object) -> ScanOut:
    """Convert a Scan ORM object to a ScanOut schema.

    Args:
        scan: Scan ORM instance.

    Returns:
        Pydantic ScanOut model.
    """
    from apme_gateway.models.tables import Scan as ScanModel

    s: ScanModel = scan  # type: ignore[assignment]
    violations = [
        ViolationOut(
            id=v.id,
            rule_id=v.rule_id,
            level=v.level,
            message=v.message,
            file=v.file,
            line=v.line,
            path=v.path,
        )
        for v in s.violations
    ]

    diag = None
    if s.diagnostics:
        try:
            diag = json.loads(s.diagnostics)
        except json.JSONDecodeError:
            diag = None

    summary = None
    if violations:
        auto = sum(1 for v in violations if v.rule_id.startswith(("L-", "M-")))
        ai = sum(1 for v in violations if v.rule_id.startswith("R-"))
        manual = len(violations) - auto - ai
        summary = ScanSummaryOut(
            total=len(violations),
            auto_fixable=auto,
            ai_candidate=ai,
            manual_review=manual,
        )

    return ScanOut(
        id=s.id,
        project_path=s.project_path,
        created_at=s.created_at,
        status=s.status,
        total_violations=s.total_violations,
        violations=violations,
        diagnostics=diag,
        summary=summary,
    )
