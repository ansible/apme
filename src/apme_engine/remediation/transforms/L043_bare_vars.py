"""L043: Rewrite deprecated bare variable references in loop directives."""

from __future__ import annotations

import re

from ruamel.yaml.comments import CommentedMap

from apme_engine.engine.models import ViolationDict

_BARE_VAR = re.compile(r"^([a-zA-Z_]\w*)$")

_LOOP_KEYS = (
    "with_items",
    "with_dict",
    "with_fileglob",
    "with_subelements",
    "with_sequence",
    "with_nested",
    "with_first_found",
)


def fix_bare_vars(task: CommentedMap, violation: ViolationDict) -> bool:
    """Wrap bare variable references in Jinja delimiters: ``foo`` -> ``{{ foo }}``.

    Args:
        task: Task CommentedMap to modify in-place.
        violation: Violation dict with line.

    Returns:
        True if a change was applied.
    """
    applied = False
    for key in _LOOP_KEYS:
        val = task.get(key)
        if isinstance(val, str) and _BARE_VAR.match(val):
            task[key] = "{{ " + val + " }}"
            applied = True

    return applied
