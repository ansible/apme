"""Query functions for the REST API layer."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import cast

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apme_gateway.db.models import (
    GalaxyServer,
    Project,
    Proposal,
    Rule,
    RuleOverride,
    Scan,
    ScanCollection,
    ScanGraph,
    ScanLog,
    ScanManifest,
    ScanPatch,
    ScanPythonPackage,
    Session,
    Violation,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Project queries (ADR-037)
# ---------------------------------------------------------------------------

_SEVERITY_WEIGHTS: dict[str, int] = {
    "critical": 20,
    "error": 10,
    "high": 6,
    "medium": 3,
    "low": 1,
    "info": 0,
}

_HEALTH_DECAY_RATE = 150


def compute_health_score(violations: list[Violation]) -> int:
    """Compute a 0-100 health score from a set of violations.

    Uses exponential decay so the score degrades gradually rather than
    immediately hitting zero for any non-trivial project.

    Args:
        violations: Violation rows from a single scan.

    Returns:
        Integer score clamped to 0-100.
    """
    if not violations:
        return 100
    penalty = sum(_SEVERITY_WEIGHTS.get(v.level, 1) for v in violations)
    return max(0, round(100 * math.exp(-penalty / _HEALTH_DECAY_RATE)))


async def create_project(
    db: AsyncSession,
    *,
    project_id: str,
    name: str,
    repo_url: str,
    branch: str = "main",
) -> Project:
    """Insert a new project.

    Args:
        db: Active async database session.
        project_id: UUID hex for the project.
        name: Display label.
        repo_url: SCM clone URL.
        branch: Target branch.

    Returns:
        The newly created Project.
    """
    now = datetime.now(tz=timezone.utc).isoformat()
    project = Project(id=project_id, name=name, repo_url=repo_url, branch=branch, created_at=now)
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project


async def list_projects(
    db: AsyncSession,
    *,
    sort_by: str = "created_at",
    order: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> list[Project]:
    """Return projects with pagination and sorting.

    Args:
        db: Active async database session.
        sort_by: Column name to sort by.
        order: ``asc`` or ``desc``.
        limit: Maximum rows.
        offset: Rows to skip.

    Returns:
        List of Project objects.
    """
    _ALLOWED_SORT = {"created_at", "name", "health_score"}
    col_name = sort_by if sort_by in _ALLOWED_SORT else "created_at"
    col = getattr(Project, col_name)
    order_clause = col.asc() if order == "asc" else col.desc()
    stmt = select(Project).order_by(order_clause).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_project(db: AsyncSession, project_id: str) -> Project | None:
    """Fetch a single project with its scans eagerly loaded.

    Args:
        db: Active async database session.
        project_id: UUID of the project.

    Returns:
        Project with scans or None.
    """
    stmt = select(Project).where(Project.id == project_id).options(selectinload(Project.scans))
    result = await db.execute(stmt)
    return cast("Project | None", result.scalar_one_or_none())


async def resolve_project(db: AsyncSession, id_or_name: str) -> Project | None:
    """Fetch a project by UUID or by unique name.

    Tries ``id`` first; falls back to ``name`` if no match.

    Args:
        db: Active async database session.
        id_or_name: Project UUID hex **or** unique display name.

    Returns:
        Project with scans or None.
    """
    proj = await get_project(db, id_or_name)
    if proj is not None:
        return proj
    stmt = select(Project).where(Project.name == id_or_name).options(selectinload(Project.scans))
    result = await db.execute(stmt)
    return cast("Project | None", result.scalar_one_or_none())


async def update_project(db: AsyncSession, project_id: str, **fields: str) -> Project | None:
    """Partial-update a project.

    Args:
        db: Active async database session.
        project_id: UUID or name of the project.
        **fields: Column-value pairs to update.

    Returns:
        Updated Project or None if not found.
    """
    project = await resolve_project(db, project_id)
    if project is None:
        return None
    for key, value in fields.items():
        if value is not None and hasattr(project, key):
            setattr(project, key, value)
    await db.commit()
    await db.refresh(project)
    return project


async def delete_project(db: AsyncSession, project_id: str) -> bool:
    """Delete a project and cascade to its scans.

    Args:
        db: Active async database session.
        project_id: UUID or name of the project.

    Returns:
        True if the project existed and was deleted.
    """
    project = await resolve_project(db, project_id)
    if project is None:
        return False
    await db.delete(project)
    await db.commit()
    return True


async def project_count(db: AsyncSession) -> int:
    """Return total number of projects.

    Args:
        db: Active async database session.

    Returns:
        Row count.
    """
    result = await db.execute(select(func.count()).select_from(Project))
    return int(result.scalar_one())


async def project_scans(
    db: AsyncSession,
    project_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[Scan]:
    """Return scans for a project ordered by creation time.

    Args:
        db: Active async database session.
        project_id: UUID of the project.
        limit: Maximum rows.
        offset: Rows to skip.

    Returns:
        List of Scan objects.
    """
    stmt = (
        select(Scan)
        .options(selectinload(Scan.project))
        .where(Scan.project_id == project_id)
        .order_by(Scan.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def project_scan_count(db: AsyncSession, project_id: str) -> int:
    """Return total scans for a project.

    Args:
        db: Active async database session.
        project_id: UUID of the project.

    Returns:
        Row count.
    """
    stmt = select(func.count()).select_from(Scan).where(Scan.project_id == project_id)
    result = await db.execute(stmt)
    return int(result.scalar_one())


async def project_violations(
    db: AsyncSession,
    project_id: str,
    *,
    severity: str | None = None,
    rule_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Violation]:
    """Return violations for a project's latest scan, with optional filters.

    Uses the latest scan regardless of ``scan_type`` because both check and
    remediate runs produce a complete violation snapshot of the current project
    state (ADR-039).  After remediation the remaining violations *are* the
    current state.

    Args:
        db: Active async database session.
        project_id: UUID of the project.
        severity: Optional severity filter (error, warning, info).
        rule_id: Optional rule_id filter.
        limit: Maximum rows.
        offset: Rows to skip.

    Returns:
        List of Violation objects.
    """
    latest_scan_stmt = (
        select(Scan.scan_id).where(Scan.project_id == project_id).order_by(Scan.created_at.desc()).limit(1)
    )
    latest_result = await db.execute(latest_scan_stmt)
    latest_scan_id = latest_result.scalar_one_or_none()
    if latest_scan_id is None:
        return []

    stmt = select(Violation).where(Violation.scan_id == latest_scan_id)
    if severity is not None:
        stmt = stmt.where(Violation.level == severity)
    if rule_id is not None:
        stmt = stmt.where(Violation.rule_id == rule_id)
    stmt = stmt.order_by(Violation.id).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def project_severity_breakdown(
    db: AsyncSession,
    project_id: str,
) -> dict[str, int]:
    """Count violations by severity for a project's latest scan (no row limit).

    Uses the latest scan regardless of ``scan_type`` — see
    :func:`project_violations` for rationale.

    Args:
        db: Active async database session.
        project_id: UUID of the project.

    Returns:
        Dict mapping severity level to count.
    """
    latest_scan_stmt = (
        select(Scan.scan_id).where(Scan.project_id == project_id).order_by(Scan.created_at.desc()).limit(1)
    )
    latest_result = await db.execute(latest_scan_stmt)
    latest_scan_id = latest_result.scalar_one_or_none()
    if latest_scan_id is None:
        return {}

    stmt = select(Violation.level, func.count()).where(Violation.scan_id == latest_scan_id).group_by(Violation.level)
    result = await db.execute(stmt)
    return {row[0]: row[1] for row in result.all()}


async def project_trend(
    db: AsyncSession,
    project_id: str,
    *,
    limit: int = 20,
) -> list[Scan]:
    """Return scans for a project ordered chronologically (trend chart).

    Args:
        db: Active async database session.
        project_id: UUID of the project.
        limit: Maximum data points.

    Returns:
        List of Scan objects ordered by created_at ascending.
    """
    stmt = select(Scan).where(Scan.project_id == project_id).order_by(Scan.created_at.asc()).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def project_top_violations(
    db: AsyncSession,
    project_id: str,
    *,
    limit: int = 10,
) -> list[tuple[str, int]]:
    """Return the most frequent rule violations for a project's latest scan.

    Uses the latest scan regardless of ``scan_type`` — see
    :func:`project_violations` for rationale.

    Args:
        db: Active async database session.
        project_id: UUID of the project.
        limit: Maximum rules.

    Returns:
        List of (rule_id, count) tuples.
    """
    latest_scan_stmt = (
        select(Scan.scan_id).where(Scan.project_id == project_id).order_by(Scan.created_at.desc()).limit(1)
    )
    latest_result = await db.execute(latest_scan_stmt)
    latest_scan_id = latest_result.scalar_one_or_none()
    if latest_scan_id is None:
        return []

    stmt = (
        select(Violation.rule_id, func.count().label("cnt"))
        .where(Violation.scan_id == latest_scan_id)
        .group_by(Violation.rule_id)
        .order_by(func.count().desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return [(row[0], row[1]) for row in result.all()]


async def link_scan_to_project(
    db: AsyncSession,
    scan_id: str,
    project_id: str,
    trigger: str = "ui",
    scan_type: str | None = None,
) -> bool:
    """Associate a scan record with a project after completion.

    Called by the project WebSocket handler once the gRPC reporting servicer
    has persisted the scan row (which defaults to ``project_id=None``).

    Args:
        db: Active async database session.
        scan_id: UUID of the scan to update.
        project_id: UUID of the owning project.
        trigger: Origin of the scan (``ui`` or ``playground``).
        scan_type: Override the scan_type (``check`` or ``remediate``).
            The engine always reports via ``ReportFixCompleted`` which
            sets ``remediate``; the gateway knows the actual intent.

    Returns:
        True if the scan row was found and updated, False otherwise.
    """
    stmt = select(Scan).where(Scan.scan_id == scan_id)
    result = await db.execute(stmt)
    scan = result.scalar_one_or_none()
    if scan is None:
        logger.warning(
            "link_scan_to_project: scan row %s not found for project %s",
            scan_id,
            project_id,
        )
        return False
    scan.project_id = project_id
    scan.trigger = trigger
    if scan_type is not None:
        scan.scan_type = scan_type
        if scan_type == "check":
            scan.fixed_count = 0
    await db.commit()
    return True


async def update_project_health(db: AsyncSession, project_id: str) -> int:
    """Recompute and persist health score from the latest scan.

    Uses the latest scan regardless of ``scan_type`` — see
    :func:`project_violations` for rationale.  After remediation the
    health score should improve to reflect the project's current state.

    Args:
        db: Active async database session.
        project_id: UUID of the project.

    Returns:
        The updated health score.
    """
    latest_scan_stmt = (
        select(Scan)
        .where(Scan.project_id == project_id)
        .order_by(Scan.created_at.desc())
        .options(selectinload(Scan.violations))
        .limit(1)
    )
    result = await db.execute(latest_scan_stmt)
    latest = result.scalar_one_or_none()
    score = compute_health_score(latest.violations) if latest else 100

    await db.execute(Project.__table__.update().where(Project.id == project_id).values(health_score=score))
    await db.commit()
    return score


async def dashboard_summary(db: AsyncSession) -> dict[str, object]:
    """Aggregate statistics across all projects (ADR-037).

    Args:
        db: Active async database session.

    Returns:
        Dict with total_projects, total_scans, total_violations,
        current_violations, current_fixable, total_remediated (``total_fixed`` key for API),
        avg_health_score.
    """
    total_projects = await project_count(db)
    total_scans_result = await db.execute(select(func.count()).select_from(Scan).where(Scan.project_id.is_not(None)))
    total_scans = cast(int, total_scans_result.scalar_one())

    violation_result = await db.execute(
        select(func.coalesce(func.sum(Scan.total_violations), 0)).where(Scan.project_id.is_not(None))
    )
    total_violations = cast(int, violation_result.scalar_one())

    latest_scan = (
        select(
            Scan.project_id,
            func.max(Scan.created_at).label("max_created"),
        )
        .where(Scan.project_id.is_not(None))
        .group_by(Scan.project_id)
        .subquery()
    )
    current_viol_result = await db.execute(
        select(func.coalesce(func.sum(Scan.total_violations), 0)).join(
            latest_scan,
            (Scan.project_id == latest_scan.c.project_id) & (Scan.created_at == latest_scan.c.max_created),
        )
    )
    current_violations = cast(int, current_viol_result.scalar_one())

    current_fixable_result = await db.execute(
        select(func.coalesce(func.sum(Scan.auto_fixable), 0)).join(
            latest_scan,
            (Scan.project_id == latest_scan.c.project_id) & (Scan.created_at == latest_scan.c.max_created),
        )
    )
    current_fixable = cast(int, current_fixable_result.scalar_one())

    current_ai_result = await db.execute(
        select(func.coalesce(func.sum(Scan.ai_candidate), 0)).join(
            latest_scan,
            (Scan.project_id == latest_scan.c.project_id) & (Scan.created_at == latest_scan.c.max_created),
        )
    )
    current_ai_candidates = cast(int, current_ai_result.scalar_one())

    fixed_result = await db.execute(
        select(func.coalesce(func.sum(Scan.fixed_count), 0)).where(
            Scan.project_id.is_not(None), Scan.scan_type == "remediate"
        )
    )
    total_fixed = cast(int, fixed_result.scalar_one())

    avg_result = await db.execute(select(func.avg(Project.health_score)))
    avg_raw = avg_result.scalar_one()
    avg_health = round(float(avg_raw)) if avg_raw is not None else 0

    return {
        "total_projects": total_projects,
        "total_scans": total_scans,
        "total_violations": total_violations,
        "current_violations": current_violations,
        "current_fixable": current_fixable,
        "current_ai_candidates": current_ai_candidates,
        "total_fixed": total_fixed,
        "avg_health_score": avg_health,
    }


async def project_rankings(
    db: AsyncSession,
    *,
    sort_by: str = "health_score",
    order: str = "desc",
    limit: int = 10,
) -> list[dict[str, object]]:
    """Return projects ranked by the specified metric (ADR-037).

    Args:
        db: Active async database session.
        sort_by: Ranking metric — health_score, violation_count, scan_count,
            or last_scanned_at.
        order: ``asc`` or ``desc``.
        limit: Maximum rows.

    Returns:
        List of ranking dicts with id, name, health_score, total_violations,
        scan_count, last_scanned_at, days_since_last_scan.
    """
    projects = await list_projects(db, sort_by="created_at", order="asc", limit=500, offset=0)

    rankings: list[dict[str, object]] = []
    now = datetime.now(tz=timezone.utc)
    for proj in projects:
        scans = await project_scans(db, proj.id, limit=1, offset=0)
        sc = await project_scan_count(db, proj.id)
        latest = scans[0] if scans else None
        last_scanned = latest.created_at if latest else None
        days_since: int | None = None
        if last_scanned:
            try:
                scanned_dt = datetime.fromisoformat(last_scanned)
                days_since = (now - scanned_dt).days
            except ValueError:
                days_since = None

        rankings.append(
            {
                "id": proj.id,
                "name": proj.name,
                "health_score": proj.health_score,
                "total_violations": latest.total_violations if latest else 0,
                "scan_count": sc,
                "last_scanned_at": last_scanned,
                "days_since_last_scan": days_since,
            }
        )

    sort_key_map: dict[str, str] = {
        "health_score": "health_score",
        "violation_count": "total_violations",
        "scan_count": "scan_count",
        "last_scanned_at": "days_since_last_scan",
    }
    key_field = sort_key_map.get(sort_by, "health_score")
    reverse = order == "desc"

    def _sort_key(r: dict[str, object]) -> object:
        val = r.get(key_field)
        if val is None:
            return math.inf if not reverse else -math.inf
        return val

    rankings.sort(key=_sort_key, reverse=reverse)  # type: ignore[arg-type]
    return rankings[:limit]


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
    stmt = (
        select(Session)
        .where(Session.session_id == session_id)
        .options(selectinload(Session.scans).selectinload(Scan.project))
    )
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
    stmt = select(Scan).options(selectinload(Scan.project)).order_by(Scan.created_at.desc())
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
            selectinload(Scan.project),
            selectinload(Scan.violations),
            selectinload(Scan.proposals),
            selectinload(Scan.logs),
            selectinload(Scan.patches),
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
    return int(result.scalar_one())


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
    return int(result.scalar_one())


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


async def remediation_rates(db: AsyncSession, *, limit: int = 20) -> list[tuple[str, int]]:
    """Return the most frequently violated rules in remediate-type runs.

    Args:
        db: Active async database session.
        limit: Maximum rules to return.

    Returns:
        List of (rule_id, count) tuples sorted descending.
    """
    stmt = (
        select(Violation.rule_id, func.count().label("cnt"))
        .join(Scan, Violation.scan_id == Scan.scan_id)
        .where(Scan.scan_type == "remediate")
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


async def update_ai_counts(
    db: AsyncSession,
    scan_id: str,
    *,
    ai_proposed: int = 0,
    ai_declined: int = 0,
    ai_accepted: int = 0,
) -> None:
    """Set AI proposal breakdown counts on a scan row.

    Args:
        db: Active async database session.
        scan_id: UUID of the scan to update.
        ai_proposed: Number of proposals the AI offered.
        ai_declined: Number of violations the AI could not fix.
        ai_accepted: Number of proposals the user approved.
    """
    stmt = select(Scan).where(Scan.scan_id == scan_id)
    result = await db.execute(stmt)
    scan = result.scalar_one_or_none()
    if scan is None:
        return
    scan.ai_proposed = ai_proposed
    scan.ai_declined = ai_declined
    scan.ai_accepted = ai_accepted
    if scan.scan_type == "remediate":
        scan.fixed_count = scan.auto_fixable + ai_accepted
    else:
        scan.fixed_count = 0
    await db.commit()


async def store_patches(
    db: AsyncSession,
    scan_id: str,
    patches: list[dict[str, str]],
) -> None:
    """Persist per-file diffs for a scan.

    Args:
        db: Active async database session.
        scan_id: UUID of the owning scan.
        patches: List of dicts with ``file`` and ``diff`` keys.
    """
    for p in patches:
        db.add(ScanPatch(scan_id=scan_id, file=p["file"], diff=p["diff"]))
    await db.commit()


# ---------------------------------------------------------------------------
# Dependency manifest queries (ADR-040)
# ---------------------------------------------------------------------------


async def _latest_scan_id_for_project(db: AsyncSession, project_id: str) -> str | None:
    """Return the scan_id of the most recent scan for a project.

    Args:
        db: Active async database session.
        project_id: UUID of the project.

    Returns:
        Scan UUID or None if no scans exist.
    """
    stmt = select(Scan.scan_id).where(Scan.project_id == project_id).order_by(Scan.created_at.desc()).limit(1)
    result = await db.execute(stmt)
    return cast("str | None", result.scalar_one_or_none())


async def project_dependencies(
    db: AsyncSession,
    project_id: str,
) -> tuple[ScanManifest | None, list[ScanCollection], list[ScanPythonPackage]]:
    """Return the full dependency manifest for a project's latest scan.

    Args:
        db: Active async database session.
        project_id: UUID of the project.

    Returns:
        Tuple of (manifest, collections, python_packages).
    """
    scan_id = await _latest_scan_id_for_project(db, project_id)
    if scan_id is None:
        return None, [], []

    manifest_stmt = select(ScanManifest).where(ScanManifest.scan_id == scan_id)
    manifest_result = await db.execute(manifest_stmt)
    manifest = manifest_result.scalar_one_or_none()

    coll_stmt = select(ScanCollection).where(ScanCollection.scan_id == scan_id).order_by(ScanCollection.fqcn)
    coll_result = await db.execute(coll_stmt)
    collections = list(coll_result.scalars().all())

    pkg_stmt = select(ScanPythonPackage).where(ScanPythonPackage.scan_id == scan_id).order_by(ScanPythonPackage.name)
    pkg_result = await db.execute(pkg_stmt)
    packages = list(pkg_result.scalars().all())

    return manifest, collections, packages


async def all_collections(
    db: AsyncSession,
    *,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, object]]:
    """Return all collections across projects with usage counts.

    Aggregates from each project's latest scan to avoid counting
    historical scans.

    Args:
        db: Active async database session.
        limit: Maximum rows.
        offset: Rows to skip.

    Returns:
        List of dicts with fqcn, version, source, project_count.
    """
    latest_scans = (
        select(
            Scan.scan_id,
            Scan.project_id,
            func.row_number().over(partition_by=Scan.project_id, order_by=Scan.created_at.desc()).label("rn"),
        )
        .where(Scan.project_id.is_not(None))
        .subquery()
    )
    latest = select(latest_scans.c.scan_id).where(latest_scans.c.rn == 1).subquery()

    most_recent = (
        select(
            ScanCollection.fqcn,
            ScanCollection.version,
            ScanCollection.source,
            func.row_number().over(partition_by=ScanCollection.fqcn, order_by=Scan.created_at.desc()).label("rn"),
        )
        .join(Scan, ScanCollection.scan_id == Scan.scan_id)
        .where(ScanCollection.scan_id.in_(select(latest.c.scan_id)))
        .subquery()
    )

    cnt = (
        select(
            ScanCollection.fqcn,
            func.count(func.distinct(Scan.project_id)).label("project_count"),
        )
        .join(Scan, ScanCollection.scan_id == Scan.scan_id)
        .where(ScanCollection.scan_id.in_(select(latest.c.scan_id)))
        .group_by(ScanCollection.fqcn)
        .subquery()
    )

    stmt = (
        select(
            most_recent.c.fqcn,
            most_recent.c.version,
            most_recent.c.source,
            cnt.c.project_count,
        )
        .join(cnt, most_recent.c.fqcn == cnt.c.fqcn)
        .where(most_recent.c.rn == 1)
        .order_by(cnt.c.project_count.desc(), most_recent.c.fqcn)
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    return [
        {
            "fqcn": row.fqcn,
            "version": row.version,
            "source": row.source,
            "project_count": row.project_count,
        }
        for row in result.all()
    ]


async def collection_detail(
    db: AsyncSession,
    fqcn: str,
) -> dict[str, object] | None:
    """Return details for a specific collection across projects.

    Args:
        db: Active async database session.
        fqcn: Fully-qualified collection name.

    Returns:
        Dict with fqcn, versions, source, projects or None if not found.
    """
    latest_scans = (
        select(
            Scan.scan_id,
            Scan.project_id,
            func.row_number().over(partition_by=Scan.project_id, order_by=Scan.created_at.desc()).label("rn"),
        )
        .where(Scan.project_id.is_not(None))
        .subquery()
    )
    latest = select(latest_scans.c.scan_id).where(latest_scans.c.rn == 1).subquery()

    stmt = (
        select(
            ScanCollection.version,
            ScanCollection.source,
            Scan.project_id,
            Project.name.label("project_name"),
            Project.health_score,
        )
        .join(Scan, ScanCollection.scan_id == Scan.scan_id)
        .join(Project, Scan.project_id == Project.id)
        .where(
            ScanCollection.fqcn == fqcn,
            ScanCollection.scan_id.in_(select(latest.c.scan_id)),
        )
    )
    result = await db.execute(stmt)
    rows = result.all()
    if not rows:
        return None

    versions = sorted({r.version for r in rows if r.version})
    sources = sorted({r.source for r in rows if r.source})
    projects = [
        {
            "id": r.project_id,
            "name": r.project_name,
            "health_score": r.health_score,
            "version": r.version,
        }
        for r in rows
    ]
    return {
        "fqcn": fqcn,
        "versions": versions,
        "source": sources[0] if sources else "unknown",
        "project_count": len(projects),
        "projects": projects,
    }


async def collection_projects(
    db: AsyncSession,
    fqcn: str,
) -> list[dict[str, object]]:
    """Return projects that depend on a specific collection.

    Args:
        db: Active async database session.
        fqcn: Fully-qualified collection name.

    Returns:
        List of dicts with project id, name, and collection version.
    """
    latest_scans = (
        select(
            Scan.scan_id,
            Scan.project_id,
            func.row_number().over(partition_by=Scan.project_id, order_by=Scan.created_at.desc()).label("rn"),
        )
        .where(Scan.project_id.is_not(None))
        .subquery()
    )
    latest = select(latest_scans.c.scan_id).where(latest_scans.c.rn == 1).subquery()

    stmt = (
        select(
            Project.id.label("project_id"),
            Project.name.label("project_name"),
            Project.health_score,
            ScanCollection.version,
        )
        .join(Scan, ScanCollection.scan_id == Scan.scan_id)
        .join(Project, Scan.project_id == Project.id)
        .where(
            ScanCollection.fqcn == fqcn,
            ScanCollection.scan_id.in_(select(latest.c.scan_id)),
        )
    )
    result = await db.execute(stmt)
    return [
        {
            "id": r.project_id,
            "name": r.project_name,
            "health_score": r.health_score,
            "collection_version": r.version,
        }
        for r in result.all()
    ]


async def all_python_packages(
    db: AsyncSession,
    *,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, object]]:
    """Return all Python packages across projects with usage counts.

    Args:
        db: Active async database session.
        limit: Maximum rows.
        offset: Rows to skip.

    Returns:
        List of dicts with name, version, project_count.
    """
    latest_scans = (
        select(
            Scan.scan_id,
            Scan.project_id,
            func.row_number().over(partition_by=Scan.project_id, order_by=Scan.created_at.desc()).label("rn"),
        )
        .where(Scan.project_id.is_not(None))
        .subquery()
    )
    latest = select(latest_scans.c.scan_id).where(latest_scans.c.rn == 1).subquery()

    most_recent = (
        select(
            ScanPythonPackage.name,
            ScanPythonPackage.version,
            func.row_number().over(partition_by=ScanPythonPackage.name, order_by=Scan.created_at.desc()).label("rn"),
        )
        .join(Scan, ScanPythonPackage.scan_id == Scan.scan_id)
        .where(ScanPythonPackage.scan_id.in_(select(latest.c.scan_id)))
        .subquery()
    )

    cnt = (
        select(
            ScanPythonPackage.name,
            func.count(func.distinct(Scan.project_id)).label("project_count"),
        )
        .join(Scan, ScanPythonPackage.scan_id == Scan.scan_id)
        .where(ScanPythonPackage.scan_id.in_(select(latest.c.scan_id)))
        .group_by(ScanPythonPackage.name)
        .subquery()
    )

    stmt = (
        select(
            most_recent.c.name,
            most_recent.c.version,
            cnt.c.project_count,
        )
        .join(cnt, most_recent.c.name == cnt.c.name)
        .where(most_recent.c.rn == 1)
        .order_by(cnt.c.project_count.desc(), most_recent.c.name)
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    return [
        {
            "name": row.name,
            "version": row.version,
            "project_count": row.project_count,
        }
        for row in result.all()
    ]


async def python_package_detail(
    db: AsyncSession,
    name: str,
) -> dict[str, object] | None:
    """Return details for a specific Python package across projects.

    Args:
        db: Active async database session.
        name: PyPI package name.

    Returns:
        Dict with name, versions, projects or None if not found.
    """
    latest_scans = (
        select(
            Scan.scan_id,
            Scan.project_id,
            func.row_number().over(partition_by=Scan.project_id, order_by=Scan.created_at.desc()).label("rn"),
        )
        .where(Scan.project_id.is_not(None))
        .subquery()
    )
    latest = select(latest_scans.c.scan_id).where(latest_scans.c.rn == 1).subquery()

    stmt = (
        select(
            ScanPythonPackage.version,
            Scan.project_id,
            Project.name.label("project_name"),
            Project.health_score,
        )
        .join(Scan, ScanPythonPackage.scan_id == Scan.scan_id)
        .join(Project, Scan.project_id == Project.id)
        .where(
            ScanPythonPackage.name == name,
            ScanPythonPackage.scan_id.in_(select(latest.c.scan_id)),
        )
    )
    result = await db.execute(stmt)
    rows = result.all()
    if not rows:
        return None

    versions = sorted({r.version for r in rows if r.version})
    projects = [
        {
            "id": r.project_id,
            "name": r.project_name,
            "health_score": r.health_score,
            "package_version": r.version,
        }
        for r in rows
    ]
    return {
        "name": name,
        "versions": versions,
        "project_count": len(projects),
        "projects": projects,
    }


# ---------------------------------------------------------------------------
# Galaxy server settings (ADR-045)
# ---------------------------------------------------------------------------


async def list_galaxy_servers(db: AsyncSession) -> list[GalaxyServer]:
    """Return all globally configured Galaxy servers ordered by name.

    Args:
        db: Async database session.

    Returns:
        List of GalaxyServer rows.
    """
    result = await db.execute(select(GalaxyServer).order_by(GalaxyServer.name))
    return list(result.scalars().all())


async def get_galaxy_server(db: AsyncSession, server_id: int) -> GalaxyServer | None:
    """Fetch a single Galaxy server by ID.

    Args:
        db: Async database session.
        server_id: Primary key.

    Returns:
        GalaxyServer or None if not found.
    """
    result: GalaxyServer | None = await db.get(GalaxyServer, server_id)
    return result


async def create_galaxy_server(
    db: AsyncSession,
    *,
    name: str,
    url: str,
    token: str = "",
    auth_url: str = "",
) -> GalaxyServer:
    """Insert a new Galaxy server definition.

    Args:
        db: Async database session.
        name: Short label.
        url: Base API URL.
        token: API token (may be empty).
        auth_url: SSO endpoint (may be empty).

    Returns:
        The newly created GalaxyServer row.
    """
    now = datetime.now(tz=timezone.utc).isoformat()
    server = GalaxyServer(
        name=name,
        url=url,
        token=token,
        auth_url=auth_url,
        created_at=now,
        updated_at=now,
    )
    db.add(server)
    await db.commit()
    await db.refresh(server)
    return server


async def update_galaxy_server(
    db: AsyncSession,
    server_id: int,
    **fields: str,
) -> GalaxyServer | None:
    """Update mutable fields on a Galaxy server.

    Args:
        db: Async database session.
        server_id: Primary key.
        **fields: Column values to update (name, url, token, auth_url).

    Returns:
        Updated GalaxyServer or None if not found.
    """
    server: GalaxyServer | None = await db.get(GalaxyServer, server_id)
    if server is None:
        return None
    allowed = {"name", "url", "token", "auth_url"}
    for key, value in fields.items():
        if key in allowed:
            setattr(server, key, value)
    server.updated_at = datetime.now(tz=timezone.utc).isoformat()
    await db.commit()
    await db.refresh(server)
    return server


async def delete_galaxy_server(db: AsyncSession, server_id: int) -> bool:
    """Delete a Galaxy server by ID.

    Args:
        db: Async database session.
        server_id: Primary key.

    Returns:
        True if a row was deleted, False if not found.
    """
    server = await db.get(GalaxyServer, server_id)
    if server is None:
        return False
    await db.delete(server)
    await db.commit()
    return True


# ---------------------------------------------------------------------------
# Rule catalog queries (ADR-041)
# ---------------------------------------------------------------------------


async def list_rules(
    db: AsyncSession,
    *,
    category: str | None = None,
    source: str | None = None,
    enabled_only: bool = False,
) -> list[Rule]:
    """List registered rules with optional filters.

    Args:
        db: Async database session.
        category: Filter by category (lint, modernize, risk, policy, secrets, infrastructure).
        source: Filter by validator source (native, opa, ansible, gitleaks).
        enabled_only: If True, only return enabled rules.

    Returns:
        List of Rule ORM objects with overrides eagerly loaded.
    """
    stmt = select(Rule).options(selectinload(Rule.overrides))
    if category:
        stmt = stmt.where(Rule.category == category)
    if source:
        stmt = stmt.where(Rule.source == source)
    if enabled_only:
        stmt = stmt.where(Rule.enabled.is_(True))
    stmt = stmt.order_by(Rule.rule_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_rule(db: AsyncSession, rule_id: str) -> Rule | None:
    """Get a single rule by ID with its overrides.

    Args:
        db: Async database session.
        rule_id: Rule identifier.

    Returns:
        Rule with overrides loaded, or None.
    """
    stmt = select(Rule).options(selectinload(Rule.overrides)).where(Rule.rule_id == rule_id)
    result = await db.execute(stmt)
    row: Rule | None = result.scalar_one_or_none()
    return row


async def get_rule_override(db: AsyncSession, rule_id: str) -> RuleOverride | None:
    """Get the override for a specific rule.

    Args:
        db: Async database session.
        rule_id: Rule identifier.

    Returns:
        RuleOverride or None.
    """
    stmt = select(RuleOverride).where(RuleOverride.rule_id == rule_id)
    result = await db.execute(stmt)
    row: RuleOverride | None = result.scalar_one_or_none()
    return row


async def upsert_rule_override(
    db: AsyncSession,
    rule_id: str,
    *,
    severity_override: int | None = None,
    enabled_override: bool | None = None,
    enforced: bool = False,
) -> RuleOverride:
    """Create or update an override for a rule.

    Args:
        db: Async database session.
        rule_id: Rule identifier (must exist in rules table).
        severity_override: Overridden severity, or None for no override.
        enabled_override: Overridden enabled state, or None for no override.
        enforced: Whether to ignore inline apme:ignore annotations.

    Returns:
        The created or updated RuleOverride.
    """
    now = datetime.now(tz=timezone.utc).isoformat()
    existing = await get_rule_override(db, rule_id)
    if existing is not None:
        existing.severity_override = severity_override
        existing.enabled_override = enabled_override
        existing.enforced = enforced
        existing.updated_at = now
        await db.commit()
        return existing

    override = RuleOverride(
        rule_id=rule_id,
        severity_override=severity_override,
        enabled_override=enabled_override,
        enforced=enforced,
        updated_at=now,
    )
    db.add(override)
    await db.commit()
    return override


async def delete_rule_override(db: AsyncSession, rule_id: str) -> bool:
    """Remove an override, reverting the rule to defaults.

    Args:
        db: Async database session.
        rule_id: Rule identifier.

    Returns:
        True if an override was deleted, False if none existed.
    """
    existing = await get_rule_override(db, rule_id)
    if existing is None:
        return False
    await db.delete(existing)
    await db.commit()
    return True


async def list_rules_with_resolved_config(
    db: AsyncSession,
) -> list[dict[str, object]]:
    """Return all rules with their resolved configuration (default + override).

    Used by the Gateway when building ``rule_configs`` for scan requests.

    Args:
        db: Async database session.

    Returns:
        List of dicts with rule_id, severity, enabled, enforced.
    """
    rules = await list_rules(db)
    configs: list[dict[str, object]] = []
    for r in rules:
        override = r.overrides[0] if r.overrides else None
        severity = (
            override.severity_override if (override and override.severity_override is not None) else r.default_severity
        )
        enabled = override.enabled_override if (override and override.enabled_override is not None) else r.enabled
        enforced = override.enforced if override else False
        configs.append(
            {
                "rule_id": r.rule_id,
                "severity": severity,
                "enabled": enabled,
                "enforced": enforced,
            }
        )
    return configs


async def get_rule_stats(db: AsyncSession) -> dict[str, object]:
    """Return summary stats about the rule catalog.

    Args:
        db: Async database session.

    Returns:
        Dict with total, by_category, by_source, override_count.
    """
    rules = await list_rules(db)
    by_category: dict[str, int] = {}
    by_source: dict[str, int] = {}
    override_count = 0
    for r in rules:
        by_category[r.category] = by_category.get(r.category, 0) + 1
        by_source[r.source] = by_source.get(r.source, 0) + 1
        if r.overrides:
            override_count += 1
    return {
        "total": len(rules),
        "by_category": by_category,
        "by_source": by_source,
        "override_count": override_count,
    }


# ---------------------------------------------------------------------------
# ContentGraph queries
# ---------------------------------------------------------------------------


async def project_graph(db: AsyncSession, project_id: str) -> ScanGraph | None:
    """Return the ContentGraph JSON for a project's latest scan.

    Args:
        db: Active async database session.
        project_id: UUID of the project.

    Returns:
        ScanGraph row or None if no graph exists.
    """
    scan_id = await _latest_scan_id_for_project(db, project_id)
    if scan_id is None:
        return None
    stmt = select(ScanGraph).where(ScanGraph.scan_id == scan_id)
    result = await db.execute(stmt)
    return cast("ScanGraph | None", result.scalar_one_or_none())
