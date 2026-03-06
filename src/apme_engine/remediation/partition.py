"""Finding partition — routes violations to Tier 1, 2, or 3."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apme_engine.remediation.registry import TransformRegistry


def is_finding_resolvable(violation: dict, registry: TransformRegistry) -> bool:
    """Return True if the violation has a registered deterministic transform (Tier 1)."""
    return violation.get("rule_id", "") in registry


def partition_violations(
    violations: list[dict],
    registry: TransformRegistry,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Split violations into (tier1_fixable, tier2_ai, tier3_manual).

    Tier 1: deterministic transform exists in registry.
    Tier 2: no transform, but ai_proposable (default True if not set).
    Tier 3: no transform, ai_proposable explicitly False.
    """
    tier1: list[dict] = []
    tier2: list[dict] = []
    tier3: list[dict] = []

    for v in violations:
        if is_finding_resolvable(v, registry):
            tier1.append(v)
        elif v.get("ai_proposable", True):
            tier2.append(v)
        else:
            tier3.append(v)

    return tier1, tier2, tier3
