"""L015: Strip Jinja delimiters from when clauses."""

from __future__ import annotations

import re

from ruamel.yaml.comments import CommentedMap

from apme_engine.engine.models import ViolationDict

_JINJA_EXPR = re.compile(r"\{\{\s*(.+?)\s*\}\}")


def fix_jinja_when(task: CommentedMap, violation: ViolationDict) -> bool:
    """Replace ``{{ var }}`` in when with bare ``var``.

    Args:
        task: Task CommentedMap to modify in-place.
        violation: Violation dict with line.

    Returns:
        True if a change was applied.
    """
    when_val = task.get("when")
    if not isinstance(when_val, str):
        return False

    new_when = _JINJA_EXPR.sub(r"\1", when_val)

    if new_when == when_val:
        return False

    task["when"] = new_when
    return True
