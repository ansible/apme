"""Flush ephemeral proposals into durable analytics (ADR-062)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import ColumnElement, delete, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from apme_gateway.db.models import Proposal, ProposalRuleAnalytics, Scan, Violation
from apme_gateway.proposals.grouping import (
    analytics_increments,
    parse_json_list,
    review_status_for_proposal,
    violation_accepts_review_status,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


async def upsert_analytics_increment(
    db: AsyncSession,
    *,
    project_id: str,
    rule_id: str,
    source: str,
    gate: str,
    tier: int,
    coupled: int,
    is_group: int,
    accepted_delta: int,
    declined_delta: int,
) -> None:
    """Atomically add deltas to a proposal_rule_analytics row.

    Uses SQLite ``ON CONFLICT DO UPDATE`` so concurrent flushes cannot both
    insert the same unique key or lose increments.

    Args:
        db: Active async session.
        project_id: Project UUID or empty string.
        rule_id: Rule id or group fingerprint.
        source: deterministic or ai.
        gate: tier1, ai, or empty.
        tier: Numeric tier.
        coupled: 0 or 1.
        is_group: 0 or 1.
        accepted_delta: Accept increment.
        declined_delta: Decline increment.
    """
    now = _now_iso()
    stmt = sqlite_insert(ProposalRuleAnalytics).values(
        project_id=project_id,
        rule_id=rule_id,
        source=source,
        gate=gate,
        tier=tier,
        coupled=coupled,
        is_group=is_group,
        accepted_count=accepted_delta,
        declined_count=declined_delta,
        updated_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            "project_id",
            "rule_id",
            "source",
            "gate",
            "tier",
            "coupled",
            "is_group",
        ],
        set_={
            "accepted_count": ProposalRuleAnalytics.accepted_count + accepted_delta,
            "declined_count": ProposalRuleAnalytics.declined_count + declined_delta,
            "updated_at": now,
        },
    )
    await db.execute(stmt)


async def flush_proposals_for_project(db: AsyncSession, project_id: str) -> int:
    """Roll unflushed project proposals into analytics and delete them.

    Idempotent: proposals with ``analytics_flushed=1`` are deleted without
    double-counting. Undecided (pending) proposals do not increment analytics.

    Concurrent callers claim each proposal with a compare-and-set update so
    the same row cannot be counted twice. Analytics writes use an atomic
    upsert.

    Also applies ``review_status`` on linked violations when a terminal
    decision is present and review_status is still null (e.g. non-interactive
    Tier 1 auto-approve inferred from status), filtered so mixed-node
    buckets do not stamp the wrong class onto unrelated violations.

    Args:
        db: Active async session.
        project_id: Project UUID.

    Returns:
        Number of proposal rows deleted.
    """
    scan_ids_stmt = select(Scan.scan_id).where(Scan.project_id == project_id)
    return await _flush_proposals(
        db,
        project_id=project_id,
        props_filter=Proposal.scan_id.in_(scan_ids_stmt),
        log_label=f"project {project_id[:12]}",
    )


async def flush_proposals_for_scan(db: AsyncSession, scan_id: str, *, project_id: str = "") -> int:
    """Roll one scan's proposals into analytics and delete them (publish flush).

    Used by PR/publish so publishing activity A cannot wipe an open remediate
    working set on activity B for the same project.

    Args:
        db: Active async session.
        scan_id: Scan whose proposals to flush.
        project_id: Project UUID for analytics rows (empty when unknown).

    Returns:
        Number of proposal rows deleted.
    """
    return await _flush_proposals(
        db,
        project_id=project_id,
        props_filter=Proposal.scan_id == scan_id,
        log_label=f"scan {scan_id[:12]}",
    )


async def _flush_proposals(
    db: AsyncSession,
    *,
    project_id: str,
    props_filter: ColumnElement[bool],
    log_label: str,
) -> int:
    """Shared claim → analytics → stamp → delete loop for a proposal filter.

    Args:
        db: Active async session.
        project_id: Project UUID for analytics (may be empty).
        props_filter: SQLAlchemy boolean clause selecting Proposal rows.
        log_label: Short label for the info log.

    Returns:
        Number of proposal rows deleted.
    """
    props_stmt = select(Proposal).where(props_filter)
    proposals = list((await db.execute(props_stmt)).scalars().all())
    if not proposals:
        return 0

    for prop in proposals:
        if prop.analytics_flushed:
            continue
        # Claim this row so a concurrent flush cannot double-count it.
        claim = await db.execute(
            update(Proposal).where(Proposal.id == prop.id, Proposal.analytics_flushed == 0).values(analytics_flushed=1)
        )
        if not claim.rowcount:
            continue

        rule_ids = parse_json_list(prop.rule_ids_json)
        increments = analytics_increments(
            {
                "status": prop.status,
                "rule_id": prop.rule_id,
                "rule_ids": rule_ids,
                "source": prop.source,
                "gate": prop.gate,
                "tier": prop.tier,
                "coupled": len(rule_ids) > 1,
            }
        )
        for inc in increments:
            await upsert_analytics_increment(
                db,
                project_id=project_id,
                rule_id=str(inc["rule_id"]),
                source=str(inc["source"]),
                gate=str(inc["gate"]),
                tier=int(inc["tier"]),
                coupled=int(inc["coupled"]),
                is_group=int(inc["is_group"]),
                accepted_delta=int(inc["accepted_delta"]),
                declined_delta=int(inc["declined_delta"]),
            )

        review = review_status_for_proposal(prop.source, prop.status)
        if review:
            v_ids = parse_json_list(prop.violation_ids_json)
            int_ids = [int(v) for v in v_ids if str(v).isdigit() or isinstance(v, int)]
            if int_ids:
                v_stmt = select(Violation).where(Violation.id.in_(int_ids))
                for violation in (await db.execute(v_stmt)).scalars().all():
                    if violation.review_status is not None:
                        continue
                    if not violation_accepts_review_status(prop.source, violation, decision=prop.status):
                        continue
                    violation.review_status = review

    deleted = await db.execute(delete(Proposal).where(props_filter))
    count = int(deleted.rowcount or 0)
    logger.info("Flushed %s proposals for %s", count, log_label)
    return count


async def discard_scan_proposals(db: AsyncSession, scan_id: str) -> int:
    """Delete proposals for a check scan without analytics or review stamps.

    Check FixCompleted still emits fixed_yaml "would fix" rows; persisting
    them as approved Tier 1 would invent durable review. When the gateway
    learns the scan is a check, drop the working set and clear any
    auto-stamped ``review_status`` on that scan's violations.

    Args:
        db: Active async session.
        scan_id: Check scan UUID.

    Returns:
        Number of proposal rows deleted.
    """
    await db.execute(
        update(Violation)
        .where(Violation.scan_id == scan_id, Violation.review_status.is_not(None))
        .values(review_status=None)
    )
    deleted = await db.execute(delete(Proposal).where(Proposal.scan_id == scan_id))
    count = int(deleted.rowcount or 0)
    if count:
        logger.info("Discarded %s check-scan proposals for %s", count, scan_id[:12])
    return count


async def replace_scan_proposals(
    db: AsyncSession,
    *,
    scan_id: str,
    proposals: Sequence[object],
) -> None:
    """Delete existing proposals for ``scan_id`` and insert ``proposals``.

    Args:
        db: Active async session.
        scan_id: Target scan.
        proposals: :class:`~apme_gateway.proposals.grouping.GroupedProposal` objects.
    """
    from apme_gateway.proposals.grouping import (  # noqa: PLC0415
        GroupedProposal,
        serialize_rule_ids,
        serialize_violation_ids,
    )

    typed = [p for p in proposals if isinstance(p, GroupedProposal)]
    if proposals and not typed:
        # Refuse to wipe the working set when the caller passed items that
        # are not GroupedProposal (programming error / empty after filter).
        logger.error(
            "replace_scan_proposals: refusing delete for scan %s — %s items, 0 GroupedProposal",
            scan_id,
            len(proposals),
        )
        return

    await db.execute(delete(Proposal).where(Proposal.scan_id == scan_id))
    for prop in typed:
        # Non-interactive deterministic with fixed yaml → approved for analytics.
        status = prop.status
        if status == "pending" and prop.source == "deterministic" and prop.fixed_yaml:
            status = "approved"
        db.add(
            Proposal(
                scan_id=scan_id,
                proposal_id=prop.proposal_id,
                rule_id=prop.rule_id,
                file=prop.file,
                tier=prop.tier,
                confidence=prop.confidence,
                status=status,
                path=prop.path,
                source=prop.source,
                gate=prop.gate,
                rule_ids_json=serialize_rule_ids(prop.rule_ids),
                violation_ids_json=serialize_violation_ids(prop.violation_ids),
                line_start=prop.line_start,
                diff_hunk=prop.diff_hunk,
                explanation=prop.explanation,
                suggestion=prop.suggestion,
                analytics_flushed=0,
            )
        )


def proposal_to_detail_dict(prop: Proposal | object) -> dict[str, object]:
    """Build a dict suitable for ProposalDetail construction.

    Args:
        prop: ORM Proposal or GroupedProposal-like object.

    Returns:
        Field mapping including additive ADR-062 keys.
    """
    if isinstance(prop, Proposal):
        rule_ids = parse_json_list(prop.rule_ids_json)
        violation_ids = parse_json_list(prop.violation_ids_json)
        return {
            "id": prop.id,
            "proposal_id": prop.proposal_id,
            "rule_id": prop.rule_id,
            "file": prop.file,
            "tier": prop.tier,
            "confidence": prop.confidence,
            "status": prop.status,
            "path": prop.path,
            "source": prop.source,
            "gate": prop.gate,
            "rule_ids": [str(r) for r in rule_ids],
            "violation_ids": [int(v) for v in violation_ids if str(v).isdigit() or isinstance(v, int)],
            "line_start": prop.line_start,
            "diff_hunk": prop.diff_hunk,
            "explanation": prop.explanation,
            "suggestion": prop.suggestion,
        }
    # GroupedProposal / duck-typed
    rule_ids = list(getattr(prop, "rule_ids", ()) or ())
    violation_ids = list(getattr(prop, "violation_ids", ()) or ())
    return {
        "id": 0,
        "proposal_id": getattr(prop, "proposal_id", ""),
        "rule_id": getattr(prop, "rule_id", ""),
        "file": getattr(prop, "file", ""),
        "tier": int(getattr(prop, "tier", 0) or 0),
        "confidence": float(getattr(prop, "confidence", 0.0) or 0.0),
        "status": getattr(prop, "status", "pending"),
        "path": getattr(prop, "path", ""),
        "source": getattr(prop, "source", "outcome"),
        "gate": getattr(prop, "gate", ""),
        "rule_ids": [str(r) for r in rule_ids],
        "violation_ids": [int(v) for v in violation_ids],
        "line_start": int(getattr(prop, "line_start", 0) or 0),
        "diff_hunk": getattr(prop, "diff_hunk", "") or "",
        "explanation": getattr(prop, "explanation", "") or "",
        "suggestion": getattr(prop, "suggestion", "") or "",
    }


# Re-export for tests / callers.
__all__ = [
    "discard_scan_proposals",
    "flush_proposals_for_project",
    "flush_proposals_for_scan",
    "proposal_to_detail_dict",
    "replace_scan_proposals",
    "upsert_analytics_increment",
]
