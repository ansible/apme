"""Canonical rule-ID normalization (leaf module, no graph/remediation deps).

Single home for :func:`normalize_rule_id` so ledger keys (graph), partition
routing (remediation), and catalog audit (daemon) agree without a
``graph -> remediation -> graph`` import cycle.
"""

from __future__ import annotations


def normalize_rule_id(rule_id: str) -> str:
    """Strip validator-specific prefixes from a rule ID for registry lookup.

    Historically native violations were prefixed with ``native:``. That prefix
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
