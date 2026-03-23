"""Read-only query functions for the REST API layer."""

from __future__ import annotations

from typing import cast

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apme_gateway.db.models import Proposal, Scan, ScanLog, Session, Violation


async def list_sessions(db: AsyncSession, *, limit: int = 50, offset: int = 0) -> list[Session]:
    """Return sessions ordered by most recently seen.

    Args:
        db: Active async database session.
        limit: Maximum rows to return.
        offset: Number of rows to skip.

    Returns:
        List of Session objects.
    """
    stmt = select(Session).order_by(Session.last_seen.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_session(db: AsyncSession, session_id: str) -> Session | None:
    """Fetch a single session with its scans eagerly loaded.

    Args:
        db: Active async database session.
        session_id: The deterministic session hash.

    Returns:
        Session with scans or None.
    """
    stmt = select(Session).where(Session.session_id == session_id).options(selectinload(Session.scans))
    result = await db.execute(stmt)
    return cast("Session | None", result.scalar_one_or_none())


async def list_scans(
    db: AsyncSession,
    *,
    session_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Scan]:
    """Return scans ordered by creation time, optionally filtered by session.

    Args:
        db: Active async database session.
        session_id: Optional filter to a single session.
        limit: Maximum rows to return.
        offset: Number of rows to skip.

    Returns:
        List of Scan objects.
    """
    stmt = select(Scan).order_by(Scan.created_at.desc())
    if session_id is not None:
        stmt = stmt.where(Scan.session_id == session_id)
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_scan(db: AsyncSession, scan_id: str) -> Scan | None:
    """Fetch a single scan with violations, proposals, and logs.

    Args:
        db: Active async database session.
        scan_id: The UUID of the scan.

    Returns:
        Scan with related objects or None.
    """
    stmt = (
        select(Scan)
        .where(Scan.scan_id == scan_id)
        .options(
            selectinload(Scan.violations),
            selectinload(Scan.proposals),
            selectinload(Scan.logs),
        )
    )
    result = await db.execute(stmt)
    return cast("Scan | None", result.scalar_one_or_none())


async def top_violations(db: AsyncSession, *, limit: int = 20) -> list[tuple[str, int]]:
    """Return the most frequently triggered rule IDs across all scans.

    Args:
        db: Active async database session.
        limit: Maximum rules to return.

    Returns:
        List of (rule_id, count) tuples sorted descending by count.
    """
    stmt = (
        select(Violation.rule_id, func.count().label("cnt"))
        .group_by(Violation.rule_id)
        .order_by(func.count().desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return [(row[0], row[1]) for row in result.all()]


async def delete_scan(db: AsyncSession, scan_id: str) -> bool:
    """Delete a scan and its related rows (cascade).

    Args:
        db: Active async database session.
        scan_id: The UUID of the scan to delete.

    Returns:
        True if the scan existed and was deleted.
    """
    scan = await get_scan(db, scan_id)
    if scan is None:
        return False
    await db.delete(scan)
    await db.commit()
    return True


async def session_count(db: AsyncSession) -> int:
    """Return total number of sessions.

    Args:
        db: Active async database session.

    Returns:
        Row count.
    """
    result = await db.execute(select(func.count()).select_from(Session))
    return cast(int, result.scalar_one())


async def scan_count(db: AsyncSession, *, session_id: str | None = None) -> int:
    """Return total number of scans, optionally filtered by session.

    Args:
        db: Active async database session.
        session_id: Optional session filter.

    Returns:
        Row count.
    """
    stmt = select(func.count()).select_from(Scan)
    if session_id is not None:
        stmt = stmt.where(Scan.session_id == session_id)
    result = await db.execute(stmt)
    return cast(int, result.scalar_one())


async def get_scan_logs(db: AsyncSession, scan_id: str) -> list[ScanLog]:
    """Return logs for a specific scan.

    Args:
        db: Active async database session.
        scan_id: The UUID of the scan.

    Returns:
        List of ScanLog entries.
    """
    stmt = select(ScanLog).where(ScanLog.scan_id == scan_id).order_by(ScanLog.id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_proposals(db: AsyncSession, scan_id: str) -> list[Proposal]:
    """Return proposals for a specific scan.

    Args:
        db: Active async database session.
        scan_id: The UUID of the scan.

    Returns:
        List of Proposal entries.
    """
    stmt = select(Proposal).where(Proposal.scan_id == scan_id).order_by(Proposal.id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def session_trend(db: AsyncSession, session_id: str) -> list[Scan]:
    """Return scans for a session ordered by creation time (for trend charts).

    Args:
        db: Active async database session.
        session_id: Session to query.

    Returns:
        List of Scan objects ordered chronologically.
    """
    stmt = select(Scan).where(Scan.session_id == session_id).order_by(Scan.created_at)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def fix_rates(db: AsyncSession, *, limit: int = 20) -> list[tuple[str, int]]:
    """Return the most frequently violated rules in fix-type scans.

    Args:
        db: Active async database session.
        limit: Maximum rules to return.

    Returns:
        List of (rule_id, count) tuples sorted descending.
    """
    stmt = (
        select(Violation.rule_id, func.count().label("cnt"))
        .join(Scan, Violation.scan_id == Scan.scan_id)
        .where(Scan.scan_type == "fix")
        .group_by(Violation.rule_id)
        .order_by(func.count().desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return [(row[0], row[1]) for row in result.all()]


async def ai_acceptance(db: AsyncSession) -> list[tuple[str, int, int, int, float]]:
    """Return per-rule AI proposal acceptance statistics.

    Args:
        db: Active async database session.

    Returns:
        List of (rule_id, approved, rejected, pending, avg_confidence) tuples.
    """
    approved_expr = case((Proposal.status == "approved", 1), else_=0)
    rejected_expr = case((Proposal.status == "rejected", 1), else_=0)
    pending_expr = case((Proposal.status == "pending", 1), else_=0)
    stmt = (
        select(
            Proposal.rule_id,
            func.sum(approved_expr).label("approved"),
            func.sum(rejected_expr).label("rejected"),
            func.sum(pending_expr).label("pending"),
            func.avg(Proposal.confidence).label("avg_conf"),
        )
        .group_by(Proposal.rule_id)
        .order_by(func.count().desc())
    )
    result = await db.execute(stmt)
    return [
        (
            row[0],
            int(row[1] or 0),
            int(row[2] or 0),
            int(row[3] or 0),
            float(row[4] or 0.0),
        )
        for row in result.all()
    ]
