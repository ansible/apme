"""L009: Rewrite empty-string comparisons in when to truthiness tests."""

from __future__ import annotations

import re

from ruamel.yaml.comments import CommentedMap

from apme_engine.engine.models import ViolationDict

_PATTERNS = [
    (re.compile(r'\b(\w+)\s*==\s*""'), r"\1 | length == 0"),
    (re.compile(r"\b(\w+)\s*==\s*''"), r"\1 | length == 0"),
    (re.compile(r'\b(\w+)\s*!=\s*""'), r"\1 | length > 0"),
    (re.compile(r"\b(\w+)\s*!=\s*''"), r"\1 | length > 0"),
]


def fix_empty_string(task: CommentedMap, violation: ViolationDict) -> bool:
    """Replace `var == ""` with `var | length == 0` and similar.

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
    for pat, repl in _PATTERNS:
        new_when = pat.sub(repl, new_when)

    if new_when == when_val:
        return False

    task["when"] = new_when
    return True
