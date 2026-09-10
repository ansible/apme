"""Gateway proposal working-set helpers (ADR-062).

Group engine violations into node-grained approval units and manage
ephemeral proposal retention / analytics flush.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Proto RemediationClass numeric values (apme.v1.common).
_RC_AUTO_FIXABLE = 1
_RC_AI_CANDIDATE = 2
_RC_MANUAL_REVIEW = 3

SOURCE_DETERMINISTIC = "deterministic"
SOURCE_AI = "ai"
SOURCE_AI_CANDIDATE = "ai-candidate"
SOURCE_OUTCOME = "outcome"

GATE_TIER1 = "tier1"
GATE_AI = "ai"

REVIEW_DETERMINISTIC_APPROVED = "deterministic_approved"
REVIEW_DETERMINISTIC_DECLINED = "deterministic_declined"
REVIEW_AI_APPROVED = "ai_approved"
REVIEW_AI_DECLINED = "ai_declined"

_STATUS_ACCEPTED = frozenset({"approved", "accepted"})
_STATUS_DECLINED = frozenset({"declined", "rejected"})


@dataclass(frozen=True)
class GroupedProposal:
    """In-memory approval unit produced by :func:`group_violations`.

    Attributes:
        proposal_id: Stable id within a scan.
        rule_id: Primary/display rule id.
        rule_ids: All rule ids on this node.
        violation_ids: Linked violation primary keys (may be empty when rebuilt).
        file: Relative file path.
        path: Node identity path (empty for path-less singletons).
        line_start: First line of the finding/node.
        tier: 1 for deterministic, 2 for AI-oriented.
        source: deterministic, ai, ai-candidate, or outcome.
        gate: tier1, ai, or empty.
        status: pending / approved / declined / etc.
        confidence: Confidence score.
        original_yaml: Before YAML when available.
        fixed_yaml: After YAML when available.
        diff_hunk: Optional unified diff (usually empty at FixCompleted).
        explanation: Optional AI explanation.
        suggestion: Optional suggestion text.
        coupled: True when more than one rule id is present.
        stamp_rule_ids: When non-empty, only these rule ids may receive
            ``review_status`` stamps (set from matched outcome rule list).
        node_type: ContentGraph NodeType value (task, block, play, …).
        line_end: Last line of the finding/node (0 when unknown).
    """

    proposal_id: str
    rule_id: str
    rule_ids: tuple[str, ...]
    violation_ids: tuple[int, ...]
    file: str
    path: str
    line_start: int
    tier: int
    source: str
    gate: str
    status: str = "pending"
    confidence: float = 0.0
    original_yaml: str = ""
    fixed_yaml: str = ""
    diff_hunk: str = ""
    explanation: str = ""
    suggestion: str = ""
    coupled: bool = False
    stamp_rule_ids: tuple[str, ...] = ()
    node_type: str = ""
    line_end: int = 0


@dataclass
class _Bucket:
    """Mutable grouping bucket before freezing into GroupedProposal.

    Attributes:
        items: Violation-like mappings in this bucket.
        key: Stable group key for the bucket.
    """

    items: list[Mapping[str, Any]] = field(default_factory=list)
    key: str = ""


def _to_int(value: object, default: int = 0) -> int:
    """Coerce JSON-ish line values to int, mapping unknowns to default.

    Clamps results at ``>= 0`` since line numbers and tiers are never
    negative.

    Args:
        value: Raw line value (int, float, numeric string, bool, None, …).
        default: Fallback when coercion fails.

    Returns:
        Coerced integer clamped at ``>= 0``, or clamped ``default``.
    """
    fallback = max(0, default)
    if isinstance(value, bool):
        logger.debug("Falling back line/tier value %r to %r", value, fallback)
        return fallback
    if isinstance(value, int):
        if value < 0:
            logger.debug("Clamping negative line/tier value %r to 0", value)
            return 0
        return value
    if isinstance(value, float):
        if value.is_integer():
            coerced = int(value)
            if coerced < 0:
                logger.debug("Clamping negative line/tier value %r to 0", value)
                return 0
            return coerced
        logger.debug("Falling back line/tier value %r to %r", value, fallback)
        return fallback
    if isinstance(value, str):
        text = value.strip()
        if not text:
            logger.debug("Falling back line/tier value %r to %r", value, fallback)
            return fallback
        try:
            coerced_str = int(text)
            if coerced_str < 0:
                logger.debug("Clamping negative line/tier value %r to 0", value)
                return 0
            return coerced_str
        except ValueError:
            try:
                parsed = float(text)
            except ValueError:
                logger.debug("Falling back line/tier value %r to %r", value, fallback)
                return fallback
            if parsed.is_integer():
                coerced_float = int(parsed)
                if coerced_float < 0:
                    logger.debug("Clamping negative line/tier value %r to 0", value)
                    return 0
                return coerced_float
            logger.debug("Falling back line/tier value %r to %r", value, fallback)
            return fallback
    if value is None:
        logger.debug("Falling back line/tier value %r to %r", value, fallback)
        return fallback
    logger.debug("Falling back line/tier value %r to %r", value, fallback)
    return fallback


def _parse_violation_id(value: object) -> int | None:
    """Parse a violation primary key, rejecting bools and non-integral values.

    Reuses :func:`_to_int` validation so ``True`` maps to invalid (not 1)
    and ``12.9``/``"12.5"`` map to invalid (not truncated to 12), unlike
    raw ``int()``. Failures return ``None`` instead of a default so callers
    skip invalid ids rather than inventing id 0.

    Args:
        value: Raw id value (int, float, numeric string, bool, None, …).

    Returns:
        Positive integer id, or ``None`` when invalid or non-positive.
    """
    coerced = _to_int(value)
    return coerced if coerced > 0 else None


def _coerce_violation_ids(values: Sequence[object]) -> list[int]:
    """Filter raw violation id values down to valid positive PKs.

    Args:
        values: Raw id values from JSON columns or duck-typed objects.

    Returns:
        Sorted list of valid positive integer ids.
    """
    return sorted(parsed for v in values if (parsed := _parse_violation_id(v)) is not None)


def _safe_float(value: object, default: float = 0.0) -> float:
    """Coerce JSON-ish confidence values to float, clamped to 0..1.

    Mirrors the draft stub coercion semantics so outcome overlays tolerate
    numeric strings without raising.

    Args:
        value: Raw confidence value (int, float, numeric string, bool, None, …).
        default: Fallback when coercion fails.

    Returns:
        Coerced float in ``[0.0, 1.0]``, or clamped ``default``.
    """
    clamped_default = min(1.0, max(0.0, default))
    if isinstance(value, bool):
        logger.debug("Falling back confidence value %r to %r", value, clamped_default)
        return clamped_default
    if isinstance(value, int):
        coerced_int = float(value)
        if coerced_int < 0.0:
            logger.debug("Clamping confidence value %r to 0.0", value)
            return 0.0
        if coerced_int > 1.0:
            logger.debug("Clamping confidence value %r to 1.0", value)
            return 1.0
        return coerced_int
    if isinstance(value, float):
        if not math.isfinite(value):
            logger.debug("Falling back confidence value %r to %r", value, clamped_default)
            return clamped_default
        if value < 0.0:
            logger.debug("Clamping confidence value %r to 0.0", value)
            return 0.0
        if value > 1.0:
            logger.debug("Clamping confidence value %r to 1.0", value)
            return 1.0
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            logger.debug("Falling back confidence value %r to %r", value, clamped_default)
            return clamped_default
        try:
            coerced_str = float(text)
        except ValueError:
            logger.debug("Falling back confidence value %r to %r", value, clamped_default)
            return clamped_default
        if not math.isfinite(coerced_str):
            logger.debug("Falling back confidence value %r to %r", value, clamped_default)
            return clamped_default
        if coerced_str < 0.0:
            logger.debug("Clamping confidence value %r to 0.0", value)
            return 0.0
        if coerced_str > 1.0:
            logger.debug("Clamping confidence value %r to 1.0", value)
            return 1.0
        return coerced_str
    if value is None:
        logger.debug("Falling back confidence value %r to %r", value, clamped_default)
        return clamped_default
    logger.debug("Falling back confidence value %r to %r", value, clamped_default)
    return clamped_default


def _as_mapping(v: object) -> Mapping[str, Any]:
    """Normalize ORM / dict / object with attributes into a mapping view.

    Args:
        v: Violation ORM row, dict, or attribute-bearing object.

    Returns:
        Mapping of grouping fields.
    """
    if isinstance(v, Mapping):
        coerced = dict(v)
        for field in ("line", "node_line_start", "line_end", "node_line_end", "remediation_class"):
            if field in coerced:
                coerced[field] = _to_int(coerced[field])
        return coerced
    return {
        "id": getattr(v, "id", None),
        "rule_id": getattr(v, "rule_id", "") or "",
        "file": getattr(v, "file", "") or "",
        "path": getattr(v, "path", "") or "",
        "line": _to_int(getattr(v, "line", None)),
        "node_line_start": _to_int(getattr(v, "node_line_start", 0)),
        "line_end": _to_int(getattr(v, "line_end", 0)),
        "node_line_end": _to_int(getattr(v, "node_line_end", 0)),
        "remediation_class": _to_int(getattr(v, "remediation_class", 0)),
        "original_yaml": getattr(v, "original_yaml", "") or "",
        "fixed_yaml": getattr(v, "fixed_yaml", "") or "",
        "node_type": getattr(v, "node_type", "") or "",
        "review_status": getattr(v, "review_status", None),
    }


def _class_lane(item: Mapping[str, Any]) -> str:
    """Return grouping lane so mixed rem_class paths do not share a proposal.

    Args:
        item: Violation-like mapping.

    Returns:
        ``tier1``, ``ai``, or ``other``.
    """
    rem_class = _to_int(item.get("remediation_class"))
    if rem_class == _RC_AI_CANDIDATE:
        return "ai"
    if rem_class == _RC_AUTO_FIXABLE or str(item.get("fixed_yaml") or "").strip():
        return "tier1"
    return "other"


def _group_key(item: Mapping[str, Any], index: int) -> str:
    """Return bucket key: node path + class lane, or singleton identity.

    Args:
        item: Violation-like mapping.
        index: Position among input violations (singleton fallback).

    Returns:
        Stable bucket key string.
    """
    path = str(item.get("path") or "").strip()
    lane = _class_lane(item)
    if path:
        return f"path:{path}:lane:{lane}"
    rule_id = str(item.get("rule_id") or "")
    file_path = str(item.get("file") or "")
    line = item.get("line")
    vid = item.get("id")
    if vid is not None:
        return f"singleton:id:{vid}"
    return f"singleton:{file_path}:{rule_id}:{line}:{index}"


def _classify(items: Sequence[Mapping[str, Any]]) -> tuple[str, str, int]:
    """Derive source, gate, and tier from remediation classes in the bucket.

    Args:
        items: Violation-like mappings in one group.

    Returns:
        ``(source, gate, tier)``.
    """
    classes = {_to_int(i.get("remediation_class")) for i in items}
    has_fixed = any(str(i.get("fixed_yaml") or "").strip() for i in items)
    # Prefer remediation_class over fixed_yaml so an AI-candidate row with
    # leftover fixed text is not mislabeled as Tier 1 deterministic.
    if _RC_AUTO_FIXABLE in classes:
        return SOURCE_DETERMINISTIC, GATE_TIER1, 1
    if _RC_AI_CANDIDATE in classes:
        return SOURCE_AI_CANDIDATE, GATE_AI, 2
    if has_fixed:
        return SOURCE_DETERMINISTIC, GATE_TIER1, 1
    return SOURCE_OUTCOME, "", 0


def _status_from_review(review_status: str | None) -> str:
    """Map durable review_status onto proposal status for historical rebuild.

    Args:
        review_status: Granular review enum or None.

    Returns:
        Proposal status string (approved, declined, or pending).
    """
    if review_status in {REVIEW_DETERMINISTIC_APPROVED, REVIEW_AI_APPROVED}:
        return "approved"
    if review_status in {REVIEW_DETERMINISTIC_DECLINED, REVIEW_AI_DECLINED}:
        return "declined"
    return "pending"


def _proposal_id(gate: str, key: str) -> str:
    """Build a stable proposal id from gate + group key.

    Args:
        gate: Gate label (tier1, ai, or empty).
        key: Grouping bucket key.

    Returns:
        Stable ``prop-…`` identifier.
    """
    digest = hashlib.sha256(key.encode()).hexdigest()[:12]
    gate_part = gate or "na"
    return f"prop-{gate_part}-{digest}"


def _build_diff_hunk(file_path: str, before: str, after: str) -> str:
    """Build a minimal unified diff when both sides are present.

    Args:
        file_path: Relative file path for diff headers.
        before: Original YAML text.
        after: Fixed YAML text.

    Returns:
        Unified diff string, or empty when either side is missing/equal.
    """
    if not before or not after:
        return ""
    if before == after:
        return ""
    import difflib  # noqa: PLC0415

    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{file_path or 'file'}",
            tofile=f"b/{file_path or 'file'}",
        )
    )


def group_violations(
    violations: Sequence[object],
    *,
    include_diff: bool = False,
) -> list[GroupedProposal]:
    """Group violations into node-grained approval units (ADR-062).

    Args:
        violations: ORM rows, mappings, or attribute objects with violation fields.
        include_diff: When True, compute ``diff_hunk`` from original/fixed yaml.

    Returns:
        Sorted list of :class:`GroupedProposal` (by file, path, proposal_id).
    """
    buckets: dict[str, _Bucket] = {}
    for idx, raw in enumerate(violations):
        item = _as_mapping(raw)
        key = _group_key(item, idx)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = _Bucket(key=key)
            buckets[key] = bucket
        bucket.items.append(item)

    proposals: list[GroupedProposal] = []
    for key, bucket in buckets.items():
        items = bucket.items
        rule_ids = tuple(sorted({str(i.get("rule_id") or "") for i in items if i.get("rule_id")}))
        if not rule_ids:
            rule_ids = ("",)
        violation_ids = tuple(_coerce_violation_ids([i.get("id") for i in items]))

        source, gate, tier = _classify(items)
        # Keys are path:{path}:lane:{lane} or singleton:…
        path = ""
        if key.startswith("path:"):
            rest = key[len("path:") :]
            path = rest.rsplit(":lane:", 1)[0] if ":lane:" in rest else rest
        file_path = str(items[0].get("file") or "")
        # Select span from the SAME item so start/end cannot mismatch across
        # grouped rows. Prefer the first nonzero node span, else the first
        # int-like line with its same-item end. Keep line_end=0 unknown.
        # Violations predate line_end storage, so grouped historical rows
        # keep 0 until violation storage also carries it; live items that
        # already carry line_end preserve it here.
        line_start = 0
        line_end = 0
        for i in items:
            node_start = _to_int(i.get("node_line_start"))
            if node_start:
                line_start = node_start
                line_end = _to_int(i.get("line_end")) or _to_int(i.get("node_line_end"))
                break
        else:
            for i in items:
                coerced_line = _to_int(i.get("line"))
                if coerced_line:
                    line_start = coerced_line
                    line_end = _to_int(i.get("line_end")) or _to_int(i.get("node_line_end"))
                    break

        original = next((str(i.get("original_yaml") or "") for i in items if i.get("original_yaml")), "")
        fixed = next((str(i.get("fixed_yaml") or "") for i in items if i.get("fixed_yaml")), "")
        node_type = next(
            (str(i.get("node_type") or "") for i in items if i.get("node_type")),
            "",
        )
        review_statuses = {i.get("review_status") for i in items if i.get("review_status")}
        status = "pending"
        if len(review_statuses) == 1:
            only = next(iter(review_statuses))
            status = _status_from_review(str(only) if only is not None else None)
        elif any(s in {REVIEW_DETERMINISTIC_APPROVED, REVIEW_AI_APPROVED} for s in review_statuses):
            # Mixed — prefer approved if any approved (should be rare).
            status = "approved"
        elif review_statuses and all(s in {REVIEW_DETERMINISTIC_DECLINED, REVIEW_AI_DECLINED} for s in review_statuses):
            # Mixed decline labels (e.g. ai_declined + deterministic_declined)
            # still mean a terminal decline for historical rebuild.
            status = "declined"

        # Do NOT invent approved from fixed_yaml alone on rebuild: that would
        # fabricate durable review when review_status is still NULL (e.g. after
        # a check wiped nothing but stamps never ran). Ingest still promotes
        # non-interactive Tier 1 via replace_scan_proposals / outcome overlay.

        pid = _proposal_id(gate, key)
        diff_hunk = _build_diff_hunk(file_path, original, fixed) if include_diff else ""
        proposals.append(
            GroupedProposal(
                proposal_id=pid,
                rule_id=rule_ids[0],
                rule_ids=rule_ids,
                violation_ids=violation_ids,
                file=file_path,
                path=path,
                line_start=line_start,
                line_end=line_end,
                tier=tier,
                source=source,
                gate=gate,
                status=status,
                original_yaml=original,
                fixed_yaml=fixed,
                diff_hunk=diff_hunk,
                coupled=len(rule_ids) > 1,
                node_type=node_type,
            )
        )

    proposals.sort(key=lambda p: (p.file, p.path, p.proposal_id))
    return proposals


def merge_outcomes(
    proposals: Sequence[GroupedProposal],
    outcomes: Sequence[object],
) -> list[GroupedProposal]:
    """Overlay engine ProposalOutcome status/confidence onto grouped proposals.

    Matching prefers ``proposal_id``, then a one-shot ``(file, rule_id)``
    claim so duplicate outcomes for the same file+rule do not overwrite each
    other or stamp every matching proposal with the last outcome.

    Args:
        proposals: Grouped proposals from :func:`group_violations`.
        outcomes: Proto or objects with proposal_id, rule_id, file, status, etc.

    Returns:
        New list of proposals with outcome fields applied where matched.
    """
    by_id: dict[str, object] = {}
    by_file_rule: dict[tuple[str, str], deque[object]] = {}
    for raw in outcomes:
        oid = str(getattr(raw, "proposal_id", "") or "")
        rule_id_raw = str(getattr(raw, "rule_id", "") or "")
        file_path = str(getattr(raw, "file", "") or "")
        if oid:
            by_id[oid] = raw
        # Engine coupled outcomes use comma-joined rule_id (e.g. "L007,L013").
        rule_parts = [p.strip() for p in rule_id_raw.split(",") if p.strip()]
        if file_path and rule_parts:
            for part in rule_parts:
                by_file_rule.setdefault((file_path, part), deque()).append(raw)

    claimed_ids: set[int] = set()
    merged: list[GroupedProposal] = []
    for prop in proposals:
        outcome = by_id.get(prop.proposal_id)
        if outcome is None:
            for rid in prop.rule_ids:
                queue = by_file_rule.get((prop.file, rid))
                if not queue:
                    continue
                # Claim the next unused outcome for this file+rule pair (O(1)).
                while queue:
                    candidate = queue.popleft()
                    cand_id = id(candidate)
                    if cand_id in claimed_ids:
                        continue
                    claimed_ids.add(cand_id)
                    outcome = candidate
                    break
                if outcome is not None:
                    break
        if outcome is None:
            merged.append(prop)
            continue
        claimed_ids.add(id(outcome))
        status = str(getattr(outcome, "status", "") or prop.status)
        if status == "rejected":
            status = "declined"
        # Defensive coercion: outcomes may carry JSON-ish strings ("12", "0.9",
        # "12.0"). _safe_float/_to_int fall back to the grouped value instead
        # of raising and aborting the whole merge.
        confidence = _safe_float(getattr(outcome, "confidence", prop.confidence), prop.confidence)
        tier = _to_int(getattr(outcome, "tier", prop.tier), prop.tier)
        source = prop.source
        gate = prop.gate
        # Outcome tier wins: keep source/gate aligned with tier so analytics
        # and review_status stamping do not see tier=2 + deterministic/tier1.
        if tier >= 2:
            source = SOURCE_AI
            gate = GATE_AI
        # Scope stamps to the outcome's rule list so mixed AUTO_FIXABLE lanes
        # (Tier-1 + post-apply AI fixes) do not all receive ai_approved.
        outcome_rule_raw = str(getattr(outcome, "rule_id", "") or "")
        outcome_rules = tuple(p.strip() for p in outcome_rule_raw.split(",") if p.strip())
        stamp_rules = outcome_rules if outcome_rules else prop.rule_ids
        # Keep proposal_id gate prefix aligned with post-overlay gate.
        proposal_id = prop.proposal_id
        if gate and "-tier1-" in proposal_id and gate == GATE_AI:
            proposal_id = proposal_id.replace("-tier1-", f"-{gate}-", 1)
        elif gate and "-na-" in proposal_id and gate == GATE_AI:
            proposal_id = proposal_id.replace("-na-", f"-{gate}-", 1)
        merged.append(
            GroupedProposal(
                proposal_id=proposal_id,
                rule_id=prop.rule_id,
                rule_ids=prop.rule_ids,
                violation_ids=prop.violation_ids,
                file=prop.file,
                path=prop.path,
                line_start=prop.line_start,
                line_end=_to_int(getattr(outcome, "line_end", prop.line_end), prop.line_end) or prop.line_end,
                tier=tier or prop.tier,
                source=source,
                gate=gate,
                status=status or prop.status,
                confidence=confidence,
                original_yaml=prop.original_yaml,
                fixed_yaml=prop.fixed_yaml,
                diff_hunk=prop.diff_hunk,
                node_type=prop.node_type,
                explanation=prop.explanation,
                suggestion=prop.suggestion,
                coupled=prop.coupled,
                stamp_rule_ids=stamp_rules,
            )
        )
    return merged


def rule_group_fingerprint(rule_ids: Sequence[str]) -> str:
    """Return a stable multi-rule group key (e.g. ``L007+L013``).

    Args:
        rule_ids: Rule identifiers on a coupled proposal.

    Returns:
        Sorted ``+``-joined fingerprint string.
    """
    return "+".join(sorted(r for r in rule_ids if r))


def decision_delta(status: str) -> tuple[int, int]:
    """Return ``(accepted_delta, declined_delta)`` for a proposal status.

    Args:
        status: Proposal status string.

    Returns:
        Increments to apply; ``(0, 0)`` when not a terminal decision.
    """
    normalized = status.lower()
    if normalized in _STATUS_ACCEPTED:
        return (1, 0)
    if normalized in _STATUS_DECLINED:
        return (0, 1)
    return (0, 0)


def analytics_increments(proposal: GroupedProposal | Mapping[str, Any]) -> list[dict[str, Any]]:
    """Expand one proposal decision into per-rule and per-group analytics rows.

    Args:
        proposal: Grouped proposal or mapping with rule_ids/source/status/etc.

    Returns:
        List of increment dicts ready for upsert (may be empty if undecided).
    """
    if isinstance(proposal, GroupedProposal):
        status = proposal.status
        rule_ids = list(proposal.rule_ids)
        source = proposal.source
        gate = proposal.gate
        tier = proposal.tier
        coupled = proposal.coupled
    else:
        status = str(proposal.get("status") or "")
        raw_ids = proposal.get("rule_ids") or []
        if isinstance(raw_ids, str):
            try:
                raw_ids = json.loads(raw_ids)
            except json.JSONDecodeError:
                raw_ids = [proposal.get("rule_id") or ""]
        rule_ids = [str(r) for r in raw_ids if r]
        if not rule_ids and proposal.get("rule_id"):
            rule_ids = [str(proposal["rule_id"])]
        source = str(proposal.get("source") or SOURCE_OUTCOME)
        gate = str(proposal.get("gate") or "")
        tier = _to_int(proposal.get("tier"))
        coupled = bool(proposal.get("coupled")) or len(rule_ids) > 1

    # Normalize analytics source to deterministic|ai for both input shapes.
    if source == SOURCE_AI_CANDIDATE:
        source = SOURCE_AI
    if source == SOURCE_OUTCOME:
        source = SOURCE_DETERMINISTIC if tier <= 1 else SOURCE_AI

    accepted, declined = decision_delta(status)
    if accepted == 0 and declined == 0:
        return []

    analytics_source = SOURCE_DETERMINISTIC if source == SOURCE_DETERMINISTIC else SOURCE_AI
    rows: list[dict[str, Any]] = []
    coupled_flag = 1 if coupled else 0
    for rule_id in rule_ids:
        rows.append(
            {
                "rule_id": rule_id,
                "source": analytics_source,
                "gate": gate,
                "tier": tier,
                "coupled": coupled_flag,
                "is_group": 0,
                "accepted_delta": accepted,
                "declined_delta": declined,
            }
        )
    if coupled and len(rule_ids) > 1:
        rows.append(
            {
                "rule_id": rule_group_fingerprint(rule_ids),
                "source": analytics_source,
                "gate": gate,
                "tier": tier,
                "coupled": 1,
                "is_group": 1,
                "accepted_delta": accepted,
                "declined_delta": declined,
            }
        )
    return rows


def review_status_for_proposal(source: str, status: str) -> str | None:
    """Map proposal source+status to durable violation review_status.

    Args:
        source: Proposal source.
        status: Proposal status.

    Returns:
        review_status string or None when not a terminal decision.
    """
    accepted, declined = decision_delta(status)
    is_ai = source in {SOURCE_AI, SOURCE_AI_CANDIDATE}
    if accepted:
        return REVIEW_AI_APPROVED if is_ai else REVIEW_DETERMINISTIC_APPROVED
    if declined:
        return REVIEW_AI_DECLINED if is_ai else REVIEW_DETERMINISTIC_DECLINED
    return None


def violation_accepts_review_status(
    source: str,
    violation: object,
    *,
    decision: str | None = None,
) -> bool:
    """Return True when ``violation`` may receive a proposal's review_status.

    Mixed path buckets can include Tier 1 fixed rows, AI-candidate rows, and
    manual-review rows. Stamping one proposal decision onto every linked
    violation would corrupt durable ``review_status`` for the wrong class.

    After interactive AI sessions the engine rewrites rem_class: approved
    fixes become AUTO_FIXABLE and remaining rows become MANUAL_REVIEW. When
    ``decision`` is a terminal AI accept/decline, allow those post-apply
    shapes so stamps are not silently dropped. Callers must still scope
    stamps to outcome rule ids (see ``stamp_rule_ids``) so Tier-1 rows in
    a shared AUTO_FIXABLE lane are not labeled ``ai_*``.

    Args:
        source: Proposal source (deterministic / ai / ai-candidate / outcome).
        violation: Violation ORM row or mapping-like object.
        decision: Optional proposal status (approved / declined / …).

    Returns:
        Whether this violation is compatible with the proposal source.
    """
    if isinstance(violation, Mapping):
        fixed = str(violation.get("fixed_yaml", "") or "").strip()
        rem_class = _to_int(violation.get("remediation_class"))
    else:
        fixed = str(getattr(violation, "fixed_yaml", "") or "").strip()
        rem_class = _to_int(getattr(violation, "remediation_class", 0))
    is_ai_source = source in {SOURCE_AI, SOURCE_AI_CANDIDATE}
    accepted, declined = decision_delta(decision or "")
    if is_ai_source:
        if rem_class == _RC_AI_CANDIDATE:
            return True
        # Post-apply shapes after Engine rem_class rewrite.
        if accepted and rem_class == _RC_AUTO_FIXABLE and fixed:
            return True
        return bool(declined and rem_class == _RC_MANUAL_REVIEW and not fixed)
    if source in {SOURCE_DETERMINISTIC, SOURCE_OUTCOME}:
        # Deterministic / thin-outcome decisions apply to auto-fixable /
        # fixed rows only (outcome maps to deterministic_* review labels).
        return rem_class == _RC_AUTO_FIXABLE or bool(fixed)
    # Unknown source: never stamp — deny by default.
    return False


def serialize_rule_ids(rule_ids: Sequence[str]) -> str:
    """JSON-encode rule id list for ORM storage.

    Args:
        rule_ids: Rule identifiers.

    Returns:
        JSON array string.
    """
    return json.dumps(list(rule_ids))


def serialize_violation_ids(violation_ids: Sequence[int]) -> str:
    """JSON-encode violation id list for ORM storage.

    Args:
        violation_ids: Violation primary keys.

    Returns:
        JSON array string.
    """
    return json.dumps(list(violation_ids))


def parse_json_list(raw: str) -> list[Any]:
    """Parse a JSON list column; return empty list on failure.

    Args:
        raw: JSON text from a DB column.

    Returns:
        Parsed list, or ``[]`` if missing/invalid.
    """
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def stamp_rule_allowlist(*, stamp_rule_ids_json: str | None, rule_ids_json: str | None) -> set[str]:
    """Rules allowed to receive ``review_status`` stamps.

    Empty ``stamp_rule_ids_json`` falls back to the proposal's full
    ``rule_ids_json`` (``Proposal.stamp_rule_ids_json`` contract). When both
    are empty, returns an empty set (callers treat that as no rule filter).

    Args:
        stamp_rule_ids_json: Optional stamp-scope JSON array.
        rule_ids_json: Full proposal rule-id JSON array.

    Returns:
        Set of rule id strings to allow when non-empty.
    """
    stamp = {str(r) for r in parse_json_list(stamp_rule_ids_json or "[]") if r}
    if stamp:
        return stamp
    return {str(r) for r in parse_json_list(rule_ids_json or "[]") if r}
