"""Bounded argspec assessment metadata, independent of displayed findings."""

from __future__ import annotations

import json
from collections.abc import Mapping, Set

ARGSPEC_RULE_IDS = frozenset({"L058", "L059"})
ARGSPEC_COVERAGE_KEY = "argspec_coverage_v1"
_MAX_COVERAGE_BYTES = 256 * 1024
_MAX_COVERAGE_TASKS = 4096


def encode_argspec_coverage(unassessed: Mapping[str, Set[str]] | None) -> str:
    """Encode excluded task/rule pairs, falling back conservatively on overflow.

    Args:
        unassessed: Task IDs mapped to rules that could not assess every context,
            or None when the validator could not establish coverage.

    Returns:
        Bounded JSON metadata; ``*`` protects all tasks if the bound is exceeded.
    """
    fallback = json.dumps({"*": sorted(ARGSPEC_RULE_IDS)})
    if unassessed is None or len(unassessed) > _MAX_COVERAGE_TASKS:
        return fallback
    total = 0
    entries: dict[str, list[str]] = {}
    for node_id, rules in unassessed.items():
        # JSON escapes non-ASCII characters, so character counts bound bytes.
        total += len(node_id) * 6 + 32
        if not node_id or total > _MAX_COVERAGE_BYTES:
            return fallback
        entries[node_id] = sorted(rules & ARGSPEC_RULE_IDS)
    encoded = json.dumps(entries)
    return encoded if len(encoded) <= _MAX_COVERAGE_BYTES else fallback


def decode_argspec_coverage(value: str | None) -> dict[str, frozenset[str]]:
    """Read assessment metadata, treating missing or invalid data as unassessed.

    Args:
        value: The validator diagnostics metadata value, before finding filters.

    Returns:
        Excluded task/rule pairs, or all argspec tasks for an invalid signal.
    """
    fallback = {"*": ARGSPEC_RULE_IDS}
    if value is None or len(value) > _MAX_COVERAGE_BYTES:
        return fallback
    try:
        data = json.loads(value)
    except (ValueError, RecursionError):
        return fallback
    if not isinstance(data, dict) or len(data) > _MAX_COVERAGE_TASKS:
        return fallback
    result: dict[str, frozenset[str]] = {}
    for node_id, rules in data.items():
        if (
            not isinstance(node_id, str)
            or not node_id
            or not isinstance(rules, list)
            or len(rules) > len(ARGSPEC_RULE_IDS)
            or any(not isinstance(rule, str) or rule not in ARGSPEC_RULE_IDS for rule in rules)
        ):
            return fallback
        result[node_id] = frozenset(rules)
    return result
