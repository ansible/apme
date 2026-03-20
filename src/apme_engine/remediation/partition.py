"""Finding partition — routes violations to Tier 1, 2, or 3."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apme_engine.engine.models import ViolationDict
from apme_engine.engine.models import RemediationClass, RemediationResolution, RuleScope
from apme_engine.remediation.registry import TransformRegistry

AI_PROPOSABLE_SCOPES: frozenset[str] = frozenset({RuleScope.TASK, RuleScope.BLOCK})

# Task-scoped rules whose remediation requires cross-file context.
# These are structurally task-level but the fix needs role/taskfile inventory.
CROSS_FILE_RULES: frozenset[str] = frozenset(
    {
        "R111",  # parameterized role import — needs role inventory
        "R112",  # parameterized task import — needs taskfile inventory
    }
)


def _get_scope(violation: ViolationDict) -> str:
    """Extract the scope string from a violation, defaulting to task.

    Args:
        violation: Violation dict, possibly with a ``scope`` field.

    Returns:
        Scope string value (e.g. ``"task"``, ``"play"``).
    """
    raw = violation.get("scope") or RuleScope.TASK
    return raw.value if hasattr(raw, "value") else str(raw)


def normalize_rule_id(rule_id: str) -> str:
    """Strip validator-specific prefixes from a rule ID for registry lookup.

    Args:
        rule_id: Raw rule ID, possibly prefixed (e.g. ``native:L021``).

    Returns:
        Bare rule ID suitable for registry lookup (e.g. ``L021``).
    """
    if rule_id.startswith("native:"):
        rule_id = rule_id[len("native:") :]
    return rule_id


def is_finding_resolvable(violation: ViolationDict, registry: TransformRegistry) -> bool:
    """Return True if the violation has a registered deterministic transform (Tier 1).

    Args:
        violation: Violation dict with rule_id.
        registry: Transform registry to check for rule.

    Returns:
        True if rule_id has a registered transform.
    """
    return normalize_rule_id(str(violation.get("rule_id", ""))) in registry


def partition_violations(
    violations: list[ViolationDict],
    registry: TransformRegistry,
) -> tuple[list[ViolationDict], list[ViolationDict], list[ViolationDict]]:
    """Split violations into (tier1_fixable, tier2_ai, tier3_manual).

    Routing uses scope metadata (ADR-026) instead of hardcoded rule lists:
    - Tier 1: deterministic transform exists in registry.
    - Tier 2: scope is AI-proposable (task/block) and no cross-file constraint.
    - Tier 3: scope is not AI-proposable, or cross-file context required.

    Args:
        violations: List of violation dicts.
        registry: Transform registry for Tier 1 lookup.

    Returns:
        Tuple of (tier1_fixable, tier2_ai, tier3_manual).
    """
    tier1: list[ViolationDict] = []
    tier2: list[ViolationDict] = []
    tier3: list[ViolationDict] = []

    for v in violations:
        bare_id = normalize_rule_id(str(v.get("rule_id", "")))
        if is_finding_resolvable(v, registry):
            tier1.append(v)
        elif bare_id in CROSS_FILE_RULES:
            v["remediation_resolution"] = RemediationResolution.NEEDS_CROSS_FILE
            tier3.append(v)
        elif _get_scope(v) not in AI_PROPOSABLE_SCOPES:
            v["remediation_resolution"] = RemediationResolution.MANUAL
            tier3.append(v)
        elif v.get("ai_proposable", True):
            tier2.append(v)
        else:
            tier3.append(v)

    return tier1, tier2, tier3


def classify_violation(violation: ViolationDict, registry: TransformRegistry) -> RemediationClass:
    """Return remediation class: auto-fixable, ai-candidate, or manual-review.

    Uses scope metadata to determine if AI can propose fixes.

    Args:
        violation: Violation dict with rule_id and scope.
        registry: Transform registry to check for deterministic transforms.

    Returns:
        One of RemediationClass.AUTO_FIXABLE, AI_CANDIDATE, or MANUAL_REVIEW.
    """
    bare_id = normalize_rule_id(str(violation.get("rule_id", "")))
    if is_finding_resolvable(violation, registry):
        return RemediationClass.AUTO_FIXABLE
    if bare_id in CROSS_FILE_RULES:
        return RemediationClass.MANUAL_REVIEW
    if _get_scope(violation) not in AI_PROPOSABLE_SCOPES:
        return RemediationClass.MANUAL_REVIEW
    if violation.get("ai_proposable", True):
        return RemediationClass.AI_CANDIDATE
    return RemediationClass.MANUAL_REVIEW


def add_classification_to_violations(
    violations: list[ViolationDict],
    registry: TransformRegistry,
) -> None:
    """Add remediation_class and remediation_resolution fields to each violation (in place).

    Args:
        violations: List of violation dicts.
        registry: Transform registry for Tier 1 lookup.
    """
    for v in violations:
        v["remediation_class"] = classify_violation(v, registry)
        v["remediation_resolution"] = RemediationResolution.UNRESOLVED


def _to_str_value(val: object, default: str) -> str:
    """Extract the string value from an enum member or fallback to str().

    Args:
        val: Enum member, string, or other value.
        default: Default string if val is falsy.

    Returns:
        The underlying string value.
    """
    if not val:
        return default
    return val.value if hasattr(val, "value") else str(val)


def count_by_remediation_class(violations: list[ViolationDict]) -> dict[str, int]:
    """Count violations by remediation class.

    Args:
        violations: List of violations with remediation_class field.

    Returns:
        Dict with counts keyed by remediation class string value.
    """
    counts: dict[str, int] = {rc.value: 0 for rc in RemediationClass}
    default = RemediationClass.AI_CANDIDATE.value
    for v in violations:
        rc = _to_str_value(v.get("remediation_class"), default)
        if rc in counts:
            counts[rc] += 1
        else:
            counts[default] += 1
    return counts


def count_by_resolution(violations: list[ViolationDict]) -> dict[str, int]:
    """Count violations by remediation resolution.

    Args:
        violations: List of violations with remediation_resolution field.

    Returns:
        Dict with counts keyed by resolution string value.
    """
    default = RemediationResolution.UNRESOLVED.value
    counts: dict[str, int] = {}
    for v in violations:
        res = _to_str_value(v.get("remediation_resolution"), default)
        counts[res] = counts.get(res, 0) + 1
    return counts
