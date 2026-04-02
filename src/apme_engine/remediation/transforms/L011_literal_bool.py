"""L011: Strip literal true/false/True/False comparisons from when clauses."""

from __future__ import annotations

import re

from ruamel.yaml.comments import CommentedMap

from apme_engine.engine.models import ViolationDict

_REPLACEMENTS = [
    # Equality — simplify to bare variable (or negation)
    (re.compile(r"\b(\w+)\s*==\s*(?:true|True)\b"), r"\1"),
    (re.compile(r"\b(\w+)\s*==\s*(?:false|False)\b"), r"not \1"),
    (re.compile(r"\b(\w+)\s*!=\s*(?:true|True)\b"), r"not \1"),
    (re.compile(r"\b(\w+)\s*!=\s*(?:false|False)\b"), r"\1"),
    # Jinja `is` tests
    (re.compile(r"\b(\w+)\s+is\s+true\b"), r"\1"),
    (re.compile(r"\b(\w+)\s+is\s+false\b"), r"not \1"),
    (re.compile(r"\b(\w+)\s+is\s+not\s+true\b"), r"not \1"),
    (re.compile(r"\b(\w+)\s+is\s+not\s+false\b"), r"\1"),
]


def fix_literal_bool(task: CommentedMap, violation: ViolationDict) -> bool:
    """Remove literal true/false comparisons from when clause.

    Args:
        task: Task CommentedMap to modify in-place.
        violation: Violation dict with line.

    Returns:
        True if a change was applied.
    """
    when_val = task.get("when")
    if not isinstance(when_val, str):
        return False

    new_when = when_val
    for pat, repl in _REPLACEMENTS:
        new_when = pat.sub(repl, new_when)

    if new_when == when_val:
        return False

    task["when"] = new_when
    return True
