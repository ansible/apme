"""Optimistic draft updates and gate-commit stamps (ADR-062 Phase 2)."""

from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apme_gateway.db import get_in_clause_chunk_size
from apme_gateway.db.models import Proposal, Scan
from apme_gateway.proposals.flush import fetch_violations_by_ids, upsert_analytics_increment
from apme_gateway.proposals.grouping import (
    GATE_AI,
    GATE_TIER1,
    SOURCE_AI,
    SOURCE_AI_CANDIDATE,
    SOURCE_DETERMINISTIC,
    _coerce_violation_ids,
    _safe_float,
    _to_int,
    analytics_increments,
    parse_json_list,
    review_status_for_proposal,
    serialize_rule_ids,
    stamp_rule_allowlist,
    violation_accepts_review_status,
)

logger = logging.getLogger(__name__)

_ALLOWED_DRAFT_STATUSES = frozenset({"pending", "approved", "declined", "proposed", "rejected"})


def _gate_for_source(source: str, tier: int) -> str:
    """Return archival gate label for a live proposal source/tier.

    Args:
        source: Proposal source string.
        tier: Numeric remediation tier.

    Returns:
        Gate label (``tier1``, ``ai``, or empty).
    """
    if source in {SOURCE_AI, SOURCE_AI_CANDIDATE} or tier >= 2:
        return GATE_AI
    if source == SOURCE_DETERMINISTIC or tier == 1:
        return GATE_TIER1
    return ""


def _archival_proposal_id(*, file: str, path: str, gate: str, rule_id: str, engine_id: str) -> str:
    """Stable Gateway proposal_id for a live stub (path hash or engine id).

    Args:
        file: Target file path.
        path: Node identity path when known.
        gate: Archival gate label.
        rule_id: Primary rule id.
        engine_id: Live engine proposal id.

    Returns:
        ``prop-{gate}-{digest}`` identifier.
    """
    # Always include rule_id + engine_id so two live proposals that share a
    # node path (different rules / engine ids) do not collapse to one row.
    material = f"{file}:{path}:{rule_id}:{engine_id}" if path else f"{file}:{rule_id}:{engine_id}"
    digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:12]  # noqa: S324
    gate_part = gate or "na"
    return f"prop-{gate_part}-{digest}"


def _normalize_status(status: str) -> str:
    """Map engine proposed/rejected onto Gateway pending/declined.

    Args:
        status: Raw status string from engine or UI.

    Returns:
        Normalized Gateway status.
    """
    lowered = status.lower().strip()
    if lowered == "proposed":
        return "pending"
    if lowered == "rejected":
        return "declined"
    return lowered


async def project_has_draft_proposals(
    db: AsyncSession,
    project_id: str,
    *,
    extra_scan_ids: Sequence[str] = (),
) -> bool:
    """Return True when the project has any proposal with ``draft=1``.

    Live ProposalsReady stubs may sit on a Scan with ``project_id`` still
    NULL (flush-before-attach); pass that operation's ``scan_id`` via
    ``extra_scan_ids``.

    Args:
        db: Active async session.
        project_id: Project UUID.
        extra_scan_ids: Additional scan ids (e.g. active/terminal op stub).

    Returns:
        Whether an interactive draft working set exists.
    """
    linked_scans = select(Scan.scan_id).where(Scan.project_id == project_id)
    extras = [s for s in extra_scan_ids if s]
    if extras:
        scope = or_(Proposal.scan_id.in_(linked_scans), Proposal.scan_id.in_(list(extras)))
    else:
        scope = Proposal.scan_id.in_(linked_scans)
    stmt = select(Proposal.id).where(scope, Proposal.draft == 1).limit(1)
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def abandon_project_drafts(
    db: AsyncSession,
    project_id: str,
    *,
    extra_scan_ids: Sequence[str] = (),
) -> int:
    """Clear interactive drafts so a new remediate can start (opt-in abandon).

    Clears ``draft=1`` and resets ``status`` to ``pending`` on project-linked
    scans so abandoned optimistic decisions cannot later flush into analytics
    or ``review_status``. For unlinked stub scans in ``extra_scan_ids``,
    deletes proposal rows so orphan working sets do not linger.

    Args:
        db: Active async session.
        project_id: Project UUID.
        extra_scan_ids: Operation scan ids that may not yet be project-linked.

    Returns:
        Number of proposal rows cleared or deleted.
    """
    from sqlalchemy import delete  # noqa: PLC0415

    linked_scans = select(Scan.scan_id).where(Scan.project_id == project_id)
    result = await db.execute(
        update(Proposal)
        .where(Proposal.scan_id.in_(linked_scans), Proposal.draft == 1)
        .values(draft=0, status="pending")
    )
    cleared = int(result.rowcount or 0)

    # Only probe the tiny extra_scan_ids set — do not materialize all project scans.
    candidates = [s for s in extra_scan_ids if s]
    if candidates:
        linked_extras = set(
            (
                await db.execute(
                    select(Scan.scan_id).where(
                        Scan.project_id == project_id,
                        Scan.scan_id.in_(list(candidates)),
                    )
                )
            )
            .scalars()
            .all()
        )
        for sid in candidates:
            if sid in linked_extras:
                continue
            result = await db.execute(delete(Proposal).where(Proposal.scan_id == sid))
            cleared += int(result.rowcount or 0)
    return cleared


async def ensure_scan_row(
    db: AsyncSession,
    *,
    scan_id: str,
    project_id: str | None = None,
    scan_type: str = "remediate",
) -> Scan:
    """Ensure a Scan row exists so live proposal stubs can FK to it.

    Creates a placeholder session when needed. ``ReportFixCompleted`` later
    upserts the real session_id and scan counters onto the same row.

    Args:
        db: Active async session.
        scan_id: Engine/operation scan UUID.
        project_id: Optional owning project.
        scan_type: check or remediate.

    Returns:
        Existing or newly created Scan.
    """
    from datetime import datetime  # noqa: PLC0415

    from apme_gateway.db.models import Session  # noqa: PLC0415

    existing = (await db.execute(select(Scan).where(Scan.scan_id == scan_id))).scalar_one_or_none()
    if existing is not None:
        # Attach project when known so draft detection survives registry reaper.
        # link_scan_to_project excludes this scan_id from flush-before-attach.
        if project_id and not existing.project_id:
            existing.project_id = project_id
            await db.flush()
        return existing  # type: ignore[no-any-return]

    now = datetime.now(tz=UTC).isoformat()
    # Full scan_id — truncated prefixes can collide across distinct UUIDs.
    placeholder_session = f"op-{scan_id}"
    sess = (await db.execute(select(Session).where(Session.session_id == placeholder_session))).scalar_one_or_none()
    if sess is None:
        db.add(
            Session(
                session_id=placeholder_session,
                project_path="",
                first_seen=now,
                last_seen=now,
            )
        )
        await db.flush()

    # Attach project_id when known so abandon/draft detection works without
    # relying solely on in-memory OperationRegistry. flush-before-attach
    # excludes this scan_id when linking (ADR-062 Phase 2).
    scan = Scan(
        scan_id=scan_id,
        session_id=placeholder_session,
        project_id=project_id,
        project_path="",
        source="gateway",
        trigger="ui",
        created_at=now,
        scan_type=scan_type,
        total_violations=0,
        auto_fixable=0,
        ai_candidate=0,
        manual_review=0,
        fixed_count=0,
        diagnostics_json="{}",
    )
    db.add(scan)
    await db.flush()
    return scan


@dataclass
class StubPayload:
    """Normalized live proposal payload for upsert matching.

    Attributes:
        raw: Original proposal mapping.
        engine_id: Engine proposal id (match key).
        file: Target file path.
        rule_id: Raw rule id string as received.
        primary_rule: Primary/display rule (never the coupled CSV).
        rule_parts: Parsed rule id tuple.
        path: Node identity path.
        tier: Numeric remediation tier.
        source: Proposal source string.
        gate: Archival gate label.
        status: Normalized status string.
        archival_id: Archival-style proposal id (second match key).
        line_start: First line of the node/finding (0 when unknown).
        line_end: Last line of the node/finding (0 when unknown).
    """

    raw: Mapping[str, Any]
    engine_id: str
    file: str
    rule_id: str
    primary_rule: str
    rule_parts: tuple[str, ...]
    path: str
    tier: int
    source: str
    gate: str
    status: str
    archival_id: str
    line_start: int
    line_end: int


def _prepare_stub_payload(raw: Mapping[str, Any]) -> StubPayload | None:
    """Normalize one live proposal mapping for upsert matching.

    Args:
        raw: Proposal mapping with id/file/rule_id/tier/status/source/….

    Returns:
        Normalized payload including the match keys, or ``None`` when the
        mapping carries no engine proposal id.
    """
    engine_id = str(raw.get("id") or raw.get("engine_proposal_id") or "").strip()
    if not engine_id:
        return None
    file_ = str(raw.get("file") or "")
    rule_id = str(raw.get("rule_id") or "")
    path = str(raw.get("path") or "")
    tier = _to_int(raw.get("tier"), 0)
    source = str(raw.get("source") or (SOURCE_AI if tier >= 2 else SOURCE_DETERMINISTIC))
    gate = str(raw.get("gate") or "") or _gate_for_source(source, tier)
    rule_parts = tuple(p.strip() for p in rule_id.split(",") if p.strip()) or ((rule_id,) if rule_id else ())
    # Proposal.rule_id is the primary/display rule — never the coupled CSV.
    primary_rule = rule_parts[0] if rule_parts else ""
    return StubPayload(
        raw=raw,
        engine_id=engine_id,
        file=file_,
        rule_id=rule_id,
        primary_rule=primary_rule,
        rule_parts=rule_parts,
        path=path,
        tier=tier,
        source=source,
        gate=gate,
        status=_normalize_status(str(raw.get("status") or "pending")),
        archival_id=_archival_proposal_id(
            file=file_,
            path=path,
            gate=gate,
            rule_id=primary_rule or rule_id,
            engine_id=engine_id,
        ),
        line_start=_to_int(raw.get("line_start"), 0),
        line_end=_to_int(raw.get("line_end"), 0),
    )


def _preload_per_query_limit(chunk: int) -> int:
    """Max proposals per preload SELECT while reserving one bind for scan_id.

    Args:
        chunk: SQLite IN-clause bind limit from ``get_in_clause_chunk_size``.

    Returns:
        Maximum proposals per preload batch (``(chunk - 1) // 2``).
    """
    return max(1, (chunk - 1) // 2)


async def upsert_live_proposal_stubs(
    db: AsyncSession,
    *,
    scan_id: str,
    project_id: str | None,
    proposals: Sequence[Mapping[str, Any]],
) -> list[Proposal]:
    """Upsert Gateway proposal rows for live ProposalsReady items.

    Matches existing rows by ``engine_proposal_id`` first, then by
    ``proposal_id``. Creates archival-style ids when inserting.

    Args:
        db: Active async session.
        scan_id: Operation scan UUID.
        project_id: Owning project when known.
        proposals: Mappings with id/file/rule_id/tier/status/source/….

    Returns:
        Upserted ORM Proposal rows.
    """
    await ensure_scan_row(db, scan_id=scan_id, project_id=project_id, scan_type="remediate")
    prepared = [item for item in (_prepare_stub_payload(raw) for raw in proposals) if item is not None]

    # Preloaded queries instead of a SELECT per proposal: match the same
    # (engine_proposal_id, proposal_id) pairs the loop used to fetch singly.
    # Each query binds 2N ids plus 1 for scan_id ==, so per_query*2 + 1 <=
    # chunk (e.g. 449*2 + 1 = 899 <= 900; 450*2 + 1 = 901 would exceed).
    by_engine: dict[str, Proposal] = {}
    by_archival: dict[str, Proposal] = {}
    discarded: set[int] = set()
    if prepared:
        chunk = get_in_clause_chunk_size()
        per_query = _preload_per_query_limit(chunk)
        for start in range(0, len(prepared), per_query):
            batch = prepared[start : start + per_query]
            engine_ids = [item.engine_id for item in batch]
            archival_ids = [item.archival_id for item in batch]
            rows = (
                (
                    await db.execute(
                        select(Proposal).where(
                            Proposal.scan_id == scan_id,
                            or_(
                                Proposal.engine_proposal_id.in_(engine_ids),
                                Proposal.proposal_id.in_(archival_ids),
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )
            engine_groups: defaultdict[str, list[Proposal]] = defaultdict(list)
            archival_groups: defaultdict[str, list[Proposal]] = defaultdict(list)
            for row in rows:
                if row.engine_proposal_id:
                    engine_groups[str(row.engine_proposal_id)].append(row)
                archival_groups[str(row.proposal_id)].append(row)

            for engine_id, dupes in engine_groups.items():
                if len(dupes) > 1:
                    keep = max(dupes, key=lambda row: row.id)
                    for dup in dupes:
                        if dup is not keep:
                            await db.delete(dup)
                            discarded.add(dup.id)
                    logger.warning(
                        "Reconciled %s duplicate live proposals for engine id %s on scan %s",
                        len(dupes) - 1,
                        engine_id,
                        scan_id[:12],
                    )
                    by_engine[engine_id] = keep
                else:
                    by_engine[engine_id] = dupes[0]

            for archival_id, dupes in archival_groups.items():
                if len(dupes) > 1:
                    keep = max(dupes, key=lambda row: row.id)
                    for dup in dupes:
                        if dup is not keep:
                            await db.delete(dup)
                            discarded.add(dup.id)
                    logger.warning(
                        "Reconciled %s duplicate live proposals for archival id %s on scan %s",
                        len(dupes) - 1,
                        archival_id,
                        scan_id[:12],
                    )
                    by_archival[archival_id] = keep
                else:
                    by_archival[archival_id] = dupes[0]

        if discarded:
            by_engine = {k: v for k, v in by_engine.items() if v.id not in discarded}
            by_archival = {k: v for k, v in by_archival.items() if v.id not in discarded}

    out: list[Proposal] = []
    for item in prepared:
        raw = item.raw
        engine_id = item.engine_id
        file_ = item.file
        primary_rule = item.primary_rule
        rule_parts = item.rule_parts
        path = item.path
        tier = item.tier
        source = item.source
        gate = item.gate
        status = item.status
        archival_id = item.archival_id

        # Engine-id hits win. An archival-id hit is only a match when it
        # belongs to the same engine proposal (or has no engine id yet) —
        # otherwise two live proposals would silently merge into one row.
        existing = by_engine.get(engine_id)
        if existing is None:
            archival_hit = by_archival.get(archival_id)
            if archival_hit is not None:
                hit_engine = (archival_hit.engine_proposal_id or "").strip()
                if not hit_engine or hit_engine == engine_id:
                    existing = archival_hit
        if existing is None:
            existing = Proposal(
                scan_id=scan_id,
                proposal_id=archival_id,
                rule_id=primary_rule,
                file=file_,
                tier=tier,
                confidence=_safe_float(raw.get("confidence"), 0.0),
                status=status,
                path=path,
                node_type=str(raw.get("node_type") or ""),
                source=source,
                gate=gate,
                rule_ids_json=serialize_rule_ids(rule_parts),
                violation_ids_json="[]",
                line_start=item.line_start,
                line_end=item.line_end,
                diff_hunk=str(raw.get("diff_hunk") or ""),
                explanation=str(raw.get("explanation") or ""),
                suggestion=str(raw.get("suggestion") or ""),
                analytics_flushed=0,
                engine_proposal_id=engine_id,
                draft=0,
                stamp_rule_ids_json=serialize_rule_ids(rule_parts),
            )
            db.add(existing)
            # Seed the maps so a duplicate engine id later in the same
            # batch updates this row instead of inserting a second one.
            by_engine.setdefault(engine_id, existing)
            by_archival.setdefault(archival_id, existing)
        else:
            existing.engine_proposal_id = engine_id
            existing.file = file_ or existing.file
            if primary_rule:
                existing.rule_id = primary_rule
            existing.path = path or existing.path
            nt = str(raw.get("node_type") or "")
            if nt:
                existing.node_type = nt
            existing.source = source or existing.source
            existing.gate = gate or existing.gate
            existing.diff_hunk = str(raw.get("diff_hunk") or existing.diff_hunk)
            existing.explanation = str(raw.get("explanation") or existing.explanation)
            existing.suggestion = str(raw.get("suggestion") or existing.suggestion)
            # Explicit 0 clears stale spans/scores; None/missing/"" preserves.
            # Truthiness would treat numeric 0 as missing but string "0" as
            # explicit, so test identity and blank strings instead.
            raw_line_start = raw.get("line_start") if "line_start" in raw else None
            if raw_line_start is not None and not (isinstance(raw_line_start, str) and not raw_line_start.strip()):
                existing.line_start = _to_int(raw_line_start, existing.line_start)
            raw_line_end = raw.get("line_end") if "line_end" in raw else None
            if raw_line_end is not None and not (isinstance(raw_line_end, str) and not raw_line_end.strip()):
                existing.line_end = _to_int(raw_line_end, existing.line_end)
            raw_confidence = raw.get("confidence") if "confidence" in raw else None
            if raw_confidence is not None and not (isinstance(raw_confidence, str) and not raw_confidence.strip()):
                existing.confidence = _safe_float(raw_confidence, existing.confidence)
            raw_tier = raw.get("tier") if "tier" in raw else None
            if raw_tier is not None and not (isinstance(raw_tier, str) and not raw_tier.strip()):
                existing.tier = _to_int(raw_tier, existing.tier)
            if rule_parts:
                existing.rule_ids_json = serialize_rule_ids(rule_parts)
                existing.stamp_rule_ids_json = serialize_rule_ids(rule_parts)
            # Do not clobber an in-progress draft status from a re-emit.
            if not existing.draft:
                existing.status = status
        out.append(existing)
    await db.flush()
    return out


async def apply_draft_updates(
    db: AsyncSession,
    *,
    scan_id: str,
    updates: Sequence[Mapping[str, str]],
) -> list[Proposal]:
    """Apply optimistic checkbox status updates without stamping review.

    Args:
        db: Active async session.
        scan_id: Operation scan UUID.
        updates: Mappings with proposal_id (gateway or engine) and status.

    Returns:
        Updated Proposal rows.

    Raises:
        ValueError: Unknown id or illegal status.
    """
    updated: list[Proposal] = []
    for item in updates:
        key = str(item.get("proposal_id") or item.get("id") or "").strip()
        status = _normalize_status(str(item.get("status") or ""))
        if not key:
            raise ValueError("proposal_id is required")
        if status not in _ALLOWED_DRAFT_STATUSES:
            raise ValueError(f"invalid draft status: {status}")
        prop = (
            await db.execute(
                select(Proposal).where(
                    Proposal.scan_id == scan_id,
                    or_(Proposal.proposal_id == key, Proposal.engine_proposal_id == key),
                )
            )
        ).scalar_one_or_none()
        if prop is None:
            raise ValueError(f"proposal not found: {key}")
        prop.status = status
        prop.draft = 1
        updated.append(prop)
    await db.flush()
    return updated


async def commit_gate_decisions(
    db: AsyncSession,
    *,
    scan_id: str,
    project_id: str,
    approved_engine_ids: Sequence[str],
    offered_engine_ids: Sequence[str] | None = None,
) -> int:
    """Mirror omit=reject onto Gateway rows and roll analytics when claimable.

    Does **not** delete proposal rows (publish / new remediate still flush).
    Only rows whose ``engine_proposal_id`` is in ``offered_engine_ids`` (this
    approval round) are updated — prior gate decisions on the same scan are
    left alone. When ``offered_engine_ids`` is omitted, all live-bridged rows
    on the scan are treated as offered (single-gate callers).

    ``review_status`` is stamped only when ``violation_ids_json`` already
    lists violation PKs. Live stubs usually have ``[]`` until
    ``ReportFixCompleted`` rebuilds groups; that path stamps from the
    bridged terminal status (see ``_persist_grouped_proposals``).

    Args:
        db: Active async session.
        scan_id: Operation scan UUID.
        project_id: Project UUID for analytics.
        approved_engine_ids: Engine proposal ids accepted at the gate.
        offered_engine_ids: Engine ids presented in this approval round.

    Returns:
        Number of proposals updated.
    """
    approved = {str(i) for i in approved_engine_ids}
    offered = {str(i) for i in offered_engine_ids} if offered_engine_ids is not None else None
    props = list((await db.execute(select(Proposal).where(Proposal.scan_id == scan_id))).scalars().all())
    if not props:
        return 0

    count = 0
    for prop in props:
        engine_id = (prop.engine_proposal_id or "").strip()
        # Only gate-commit rows offered in this round (have an engine id).
        if not engine_id:
            continue
        if offered is not None and engine_id not in offered:
            continue
        if engine_id in approved:
            prop.status = "approved"
        else:
            prop.status = "declined"
        prop.draft = 0
        count += 1

        if prop.analytics_flushed:
            continue
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
        if not review:
            continue
        stamp_rules = stamp_rule_allowlist(
            stamp_rule_ids_json=prop.stamp_rule_ids_json,
            rule_ids_json=prop.rule_ids_json,
        )
        v_ids = parse_json_list(prop.violation_ids_json)
        int_ids = _coerce_violation_ids(v_ids)
        if not int_ids:
            continue
        for violation in await fetch_violations_by_ids(db, int_ids):
            if violation.review_status is not None:
                continue
            if stamp_rules:
                v_rule = str(getattr(violation, "rule_id", "") or "")
                if v_rule not in stamp_rules:
                    continue
            if not violation_accepts_review_status(prop.source, violation, decision=prop.status):
                continue
            violation.review_status = review

    await db.flush()
    logger.info("Gate-committed %s proposals for scan %s", count, scan_id[:12])
    return count


__all__ = [
    "abandon_project_drafts",
    "apply_draft_updates",
    "commit_gate_decisions",
    "ensure_scan_row",
    "project_has_draft_proposals",
    "upsert_live_proposal_stubs",
]
