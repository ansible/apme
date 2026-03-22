"""Read-only REST API endpoints for the gateway.

All endpoints are GET-only.  Write operations happen exclusively via the
gRPC Reporting servicer (engine push model, ADR-020).  The DELETE endpoint
is provided for scan record housekeeping.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from apme_gateway.api.schemas import (
    HealthStatus,
    LogEntry,
    PaginatedResponse,
    ProposalDetail,
    ScanDetail,
    ScanSummary,
    SessionDetail,
    SessionSummary,
    TopViolation,
    ViolationDetail,
)
from apme_gateway.db import get_session
from apme_gateway.db import queries as q
from apme_gateway.db.models import Scan

router = APIRouter(prefix="/api/v1")


@router.get("/health")  # type: ignore[untyped-decorator]
async def health() -> HealthStatus:
    """Check gateway health including database connectivity.

    Returns:
        HealthStatus with overall and component statuses.
    """
    try:
        async with get_session() as db:
            await q.session_count(db)
        return HealthStatus(status="ok", database="ok")
    except Exception:
        return HealthStatus(status="degraded", database="unavailable")


@router.get("/sessions")  # type: ignore[untyped-decorator]
async def list_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse:
    """List sessions ordered by most recently seen.

    Args:
        limit: Page size.
        offset: Row offset.

    Returns:
        Paginated list of sessions.
    """
    async with get_session() as db:
        total = await q.session_count(db)
        rows = await q.list_sessions(db, limit=limit, offset=offset)
    items = [
        SessionSummary(
            session_id=s.session_id,
            project_path=s.project_path,
            first_seen=s.first_seen,
            last_seen=s.last_seen,
        )
        for s in rows
    ]
    return PaginatedResponse(total=total, limit=limit, offset=offset, items=items)


@router.get("/sessions/{session_id}")  # type: ignore[untyped-decorator]
async def get_session_detail(session_id: str) -> SessionDetail:
    """Fetch a session and its scans.

    Args:
        session_id: Deterministic session hash.

    Returns:
        Session with embedded scan list.

    Raises:
        HTTPException: 404 if session not found.
    """
    async with get_session() as db:
        sess = await q.get_session(db, session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionDetail(
        session_id=sess.session_id,
        project_path=sess.project_path,
        first_seen=sess.first_seen,
        last_seen=sess.last_seen,
        scans=[_scan_to_summary(s) for s in sess.scans],
    )


@router.get("/scans")  # type: ignore[untyped-decorator]
async def list_scans(
    session_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse:
    """List scans, optionally filtered by session.

    Args:
        session_id: Optional session filter.
        limit: Page size.
        offset: Row offset.

    Returns:
        Paginated list of scans.
    """
    async with get_session() as db:
        total = await q.scan_count(db, session_id=session_id)
        rows = await q.list_scans(db, session_id=session_id, limit=limit, offset=offset)
    items = [_scan_to_summary(s) for s in rows]
    return PaginatedResponse(total=total, limit=limit, offset=offset, items=items)


@router.get("/scans/{scan_id}")  # type: ignore[untyped-decorator]
async def get_scan_detail(scan_id: str) -> ScanDetail:
    """Fetch a scan with violations, proposals, and logs.

    Args:
        scan_id: UUID of the scan.

    Returns:
        Full scan detail.

    Raises:
        HTTPException: 404 if scan not found.
    """
    async with get_session() as db:
        scan = await q.get_scan(db, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    return ScanDetail(
        scan_id=scan.scan_id,
        session_id=scan.session_id,
        project_path=scan.project_path,
        source=scan.source,
        created_at=scan.created_at,
        scan_type=scan.scan_type,
        total_violations=scan.total_violations,
        auto_fixable=scan.auto_fixable,
        ai_candidate=scan.ai_candidate,
        manual_review=scan.manual_review,
        diagnostics_json=scan.diagnostics_json,
        violations=[
            ViolationDetail(
                id=v.id,
                rule_id=v.rule_id,
                level=v.level,
                message=v.message,
                file=v.file,
                line=v.line,
                path=v.path,
                remediation_class=v.remediation_class,
                scope=v.scope,
            )
            for v in scan.violations
        ],
        proposals=[
            ProposalDetail(
                id=p.id,
                proposal_id=p.proposal_id,
                rule_id=p.rule_id,
                file=p.file,
                tier=p.tier,
                confidence=p.confidence,
                status=p.status,
            )
            for p in scan.proposals
        ],
        logs=[
            LogEntry(
                id=lg.id,
                message=lg.message,
                phase=lg.phase,
                progress=lg.progress,
                level=lg.level,
            )
            for lg in scan.logs
        ],
    )


@router.delete("/scans/{scan_id}", status_code=204)  # type: ignore[untyped-decorator]
async def delete_scan(scan_id: str) -> None:
    """Delete a scan and its related data.

    Args:
        scan_id: UUID of the scan to delete.

    Raises:
        HTTPException: 404 if scan not found.
    """
    async with get_session() as db:
        deleted = await q.delete_scan(db, scan_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Scan not found")


@router.get("/violations/top")  # type: ignore[untyped-decorator]
async def top_violations(
    limit: int = Query(default=20, ge=1, le=100),
) -> list[TopViolation]:
    """Return the most frequently violated rules across all scans.

    Args:
        limit: Maximum number of rules to return.

    Returns:
        List of rules sorted by violation count descending.
    """
    async with get_session() as db:
        rows = await q.top_violations(db, limit=limit)
    return [TopViolation(rule_id=rule_id, count=count) for rule_id, count in rows]


def _scan_to_summary(scan: Scan) -> ScanSummary:
    """Convert an ORM Scan to a ScanSummary response model.

    Args:
        scan: ORM Scan instance.

    Returns:
        Pydantic ScanSummary.
    """
    return ScanSummary(
        scan_id=scan.scan_id,
        session_id=scan.session_id,
        project_path=scan.project_path,
        source=scan.source,
        created_at=scan.created_at,
        scan_type=scan.scan_type,
        total_violations=scan.total_violations,
        auto_fixable=scan.auto_fixable,
        ai_candidate=scan.ai_candidate,
        manual_review=scan.manual_review,
    )
