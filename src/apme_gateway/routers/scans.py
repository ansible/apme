"""Scan endpoints — create, list, detail, delete."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from apme_gateway.database import get_session
from apme_gateway.deps import get_primary_client
from apme_gateway.models.schemas import (
    PaginatedResponse,
    ScanCreate,
    ScanDetail,
    ScanIngest,
    ScanSummary,
    ViolationOut,
)
from apme_gateway.services import scan_service
from apme_gateway.services.grpc_client import PrimaryClient

router = APIRouter(prefix="/scans", tags=["scans"])


def _scan_to_summary(s) -> ScanSummary:
    return ScanSummary(
        id=s.id,
        project_name=s.project_name,
        created_at=s.created_at,
        status=s.status,
        scan_type=getattr(s, "scan_type", "scan") or "scan",
        total_violations=s.total_violations,
        error_count=s.error_count,
        warning_count=s.warning_count,
        hint_count=s.hint_count,
        fixed_count=getattr(s, "fixed_count", 0) or 0,
    )


@router.post("", response_model=ScanDetail, status_code=status.HTTP_201_CREATED)
async def create_scan(
    body: ScanCreate,
    session: AsyncSession = Depends(get_session),
    client: PrimaryClient = Depends(get_primary_client),
):
    scan = await scan_service.create_scan(
        session,
        client,
        project_name=body.project_name,
        files=body.files,
        ansible_core_version=body.ansible_core_version,
        collection_specs=body.collection_specs,
    )
    violations = [
        ViolationOut(
            id=v.id,
            rule_id=v.rule_id,
            level=v.level,
            message=v.message,
            file=v.file,
            line=v.line,
            line_end=v.line_end,
            path=v.path,
        )
        for v in (scan.violations if hasattr(scan, "violations") and scan.violations else [])
    ]
    return ScanDetail(
        id=scan.id,
        project_name=scan.project_name,
        created_at=scan.created_at,
        status=scan.status,
        scan_type=getattr(scan, "scan_type", "scan") or "scan",
        total_violations=scan.total_violations,
        error_count=scan.error_count,
        warning_count=scan.warning_count,
        hint_count=scan.hint_count,
        fixed_count=getattr(scan, "fixed_count", 0) or 0,
        violations=violations,
        diagnostics=scan.diagnostics,
        options=scan.options,
    )


@router.post("/ingest", response_model=ScanSummary, status_code=status.HTTP_201_CREATED)
async def ingest_scan(
    body: ScanIngest,
    session: AsyncSession = Depends(get_session),
):
    """Accept pre-computed scan results (e.g. from the CLI) without re-scanning."""
    scan = await scan_service.ingest_scan(
        session,
        project_name=body.project_name,
        scan_type=body.scan_type,
        status=body.status,
        violations=[v.model_dump() for v in body.violations],
        fixed_count=body.fixed_count,
        diagnostics=body.diagnostics,
        request_id=body.request_id,
    )
    return _scan_to_summary(scan)


@router.get("", response_model=PaginatedResponse[ScanSummary])
async def list_scans(
    page: int = 1,
    page_size: int = 20,
    status_filter: str | None = None,
    project: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    scans, total = await scan_service.list_scans(
        session, page=page, page_size=page_size, status=status_filter, project=project
    )
    return PaginatedResponse(
        items=[_scan_to_summary(s) for s in scans],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{scan_id}", response_model=ScanDetail)
async def get_scan(
    scan_id: str,
    session: AsyncSession = Depends(get_session),
):
    scan = await scan_service.get_scan(session, scan_id)
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")
    violations = [
        ViolationOut(
            id=v.id,
            rule_id=v.rule_id,
            level=v.level,
            message=v.message,
            file=v.file,
            line=v.line,
            line_end=v.line_end,
            path=v.path,
        )
        for v in scan.violations
    ]
    return ScanDetail(
        id=scan.id,
        project_name=scan.project_name,
        created_at=scan.created_at,
        status=scan.status,
        scan_type=getattr(scan, "scan_type", "scan") or "scan",
        total_violations=scan.total_violations,
        error_count=scan.error_count,
        warning_count=scan.warning_count,
        hint_count=scan.hint_count,
        fixed_count=getattr(scan, "fixed_count", 0) or 0,
        violations=violations,
        diagnostics=scan.diagnostics,
        options=scan.options,
    )


@router.delete("/{scan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scan(
    scan_id: str,
    session: AsyncSession = Depends(get_session),
):
    deleted = await scan_service.delete_scan(session, scan_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Scan not found")
