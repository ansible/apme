"""Finding partition — routes violations to Tier 1, 2, or 3.

Also identifies AI-reviewable rules — contextual findings where AI
can assess whether the violation is a true or false positive (e.g.
R108 privilege escalation that may be legitimately required).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apme_engine.engine.models import ViolationDict
from apme_engine.engine.models import RemediationClass, RemediationResolution, RuleScope
from apme_engine.graph.scanner import DISABLED_BY_DEFAULT_GRAPH_RULE_IDS
from apme_engine.remediation.registry import TransformRegistry

AI_PROPOSABLE_SCOPES: frozenset[str] = frozenset({RuleScope.TASK, RuleScope.BLOCK})

# Task-scoped rules whose remediation is unsafe at a single node.
# Node-local AI would change the producer (module, set_fact key, register
# shape) without rewriting readers — often silently, because the old name
# remains defined as a role default. These need project-level context
# (ADR-027) or a complete usage index before they can be auto-fixed.
CROSS_FILE_RULES: frozenset[str] = frozenset(
    {
        "R111",  # parameterized role import — needs role inventory
        "R112",  # parameterized task import — needs taskfile inventory
        "L006",  # command→module (e.g. cat→slurp) changes register result shape
        "L080",  # __ prefix on set_fact keys — consumers keep the old name
    }
)

AI_REVIEWABLE_RULES: frozenset[str] = frozenset(
    {
        "R108",  # privilege escalation — often legitimate for service management
        "R103",  # external command execution — may be necessary
        "R104",  # package install in task — may be intentional
        "R101",  # become user without become — could be inherited
        "R105",  # environment variable manipulation — may be required
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

    Historically native violations were prefixed with ``native:``.  That prefix
    is no longer added at the source, but this function remains for backward
    compatibility with any persisted data that still carries it.

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

    Cross-file / data-flow rules are never Tier 1, even if a caller registers
    a node-local transform. Those rewrites are unsafe without project context.

    Args:
        violation: Violation dict with rule_id.
        registry: Transform registry to check for rule.

    Returns:
        True if rule_id has a registered transform and is not cross-file.
    """
    bare_id = normalize_rule_id(str(violation.get("rule_id", "")))
    if bare_id in CROSS_FILE_RULES:
        return False
    return bare_id in registry


def partition_violations(
    violations: list[ViolationDict],
    registry: TransformRegistry,
) -> tuple[list[ViolationDict], list[ViolationDict], list[ViolationDict]]:
    """Split violations into (tier1_fixable, tier2_ai, tier3_manual).

    Routing uses scope metadata (ADR-026) instead of hardcoded rule lists:
    - Cross-file / data-flow rules (``CROSS_FILE_RULES``) are always Tier 3.
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
        sev = str(v.get("severity") or "").lower()
        if sev == "info":
            v["remediation_resolution"] = RemediationResolution.INFORMATIONAL
            tier3.append(v)
            continue
        if bare_id in CROSS_FILE_RULES:
            v["remediation_resolution"] = RemediationResolution.NEEDS_CROSS_FILE
            tier3.append(v)
        elif is_finding_resolvable(v, registry):
            tier1.append(v)
        elif _get_scope(v) not in AI_PROPOSABLE_SCOPES:
            v["remediation_resolution"] = RemediationResolution.MANUAL
            tier3.append(v)
        elif v.get("ai_proposable", True):
            tier2.append(v)
        else:
            tier3.append(v)

    return tier1, tier2, tier3


def classify_violation(violation: ViolationDict) -> RemediationClass:
    """Classify a *remaining* violation as ai-candidate or manual-review.

    This is called **after** the convergence loop.  Violations that were
    fixed during convergence are classified separately as AUTO_FIXABLE by
    the caller — remaining violations are never AUTO_FIXABLE because the
    convergence loop already tried all deterministic transforms.  What
    remains is either AI-proposable or requires manual review.

    Args:
        violation: Violation dict with rule_id and scope.

    Returns:
        One of RemediationClass.AI_CANDIDATE or MANUAL_REVIEW.
    """
    sev = str(violation.get("severity") or "").lower()
    if sev == "info":
        return RemediationClass.MANUAL_REVIEW
    bare_id = normalize_rule_id(str(violation.get("rule_id", "")))
    if bare_id in CROSS_FILE_RULES:
        return RemediationClass.MANUAL_REVIEW
    if _get_scope(violation) not in AI_PROPOSABLE_SCOPES:
        return RemediationClass.MANUAL_REVIEW
    if violation.get("ai_proposable", True):
        return RemediationClass.AI_CANDIDATE
    return RemediationClass.MANUAL_REVIEW


def add_classification_to_violations(
    violations: list[ViolationDict],
) -> None:
    """Add ``remediation_class`` to each *remaining* violation.

    Called after the convergence loop on violations that were **not** fixed.
    These are classified as ``AI_CANDIDATE`` or ``MANUAL_REVIEW`` — never
    ``AUTO_FIXABLE`` (the loop already tried all deterministic transforms).

    ``remediation_resolution`` is **not** set here — it is stamped by
    :meth:`~apme_engine.engine.content_graph.ContentGraph.query_violations`
    based on the authoritative ``ViolationRecord.status``.

    Args:
        violations: List of remaining violation dicts.
    """
    for v in violations:
        v["remediation_class"] = classify_violation(v)


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

    Disabled-by-default audit/debug graph rules (for example R402/R404) are
    excluded so opt-in informational findings do not inflate user-facing
    remediation totals.

    Args:
        violations: List of violations with remediation_class field.

    Returns:
        Dict with counts keyed by remediation class string value.
    """
    counts: dict[str, int] = {rc.value: 0 for rc in RemediationClass}
    default = RemediationClass.AI_CANDIDATE.value
    for v in violations:
        bare_id = normalize_rule_id(str(v.get("rule_id", "")))
        if bare_id in DISABLED_BY_DEFAULT_GRAPH_RULE_IDS:
            continue
        rc = _to_str_value(v.get("remediation_class"), default)
        if rc in counts:
            counts[rc] += 1
        else:
            counts[default] += 1
    return counts


def is_ai_reviewable(violation: ViolationDict) -> bool:
    """Return True if the violation can be AI-validated for true/false positive.

    AI-reviewable rules are contextual findings where the AI can assess
    whether the flagged behavior is legitimate (e.g. R108 privilege
    escalation on a service management task).

    Args:
        violation: Violation dict with rule_id.

    Returns:
        True if rule_id is in the AI-reviewable set.
    """
    bare_id = normalize_rule_id(str(violation.get("rule_id", "")))
    return bare_id in AI_REVIEWABLE_RULES


def count_by_resolution(violations: list[ViolationDict]) -> dict[str, int]:
    """Count violations by remediation resolution.

    Disabled-by-default audit/debug graph rules (for example R402/R404) are
    excluded so opt-in informational findings do not inflate user-facing totals.

    Args:
        violations: List of violations with remediation_resolution field.

    Returns:
        Dict with counts keyed by resolution string value.
    """
    default = RemediationResolution.UNRESOLVED.value
    counts: dict[str, int] = {}
    for v in violations:
        bare_id = normalize_rule_id(str(v.get("rule_id", "")))
        if bare_id in DISABLED_BY_DEFAULT_GRAPH_RULE_IDS:
            continue
        res = _to_str_value(v.get("remediation_resolution"), default)
        counts[res] = counts.get(res, 0) + 1
    return counts
