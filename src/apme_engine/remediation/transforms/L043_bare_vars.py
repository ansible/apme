"""L043: Rewrite deprecated bare variable references in loop directives."""

from __future__ import annotations

import re

from apme_engine.engine.models import ViolationDict
from apme_engine.remediation.structured import StructuredFile
from apme_engine.remediation.transforms._helpers import violation_line_to_int

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


def fix_bare_vars(sf: StructuredFile, violation: ViolationDict) -> bool:
    """Wrap bare variable references in Jinja delimiters: ``foo`` -> ``{{ foo }}``.

    Args:
        sf: Parsed YAML file to modify in-place.
        violation: Violation dict with line.

    Returns:
        True if a change was applied.
    """
    task = sf.find_task(violation_line_to_int(violation))
    if task is None:
        return False

    applied = False
    for key in _LOOP_KEYS:
        val = task.get(key)
        if isinstance(val, str) and _BARE_VAR.match(val):
            task[key] = "{{ " + val + " }}"
            applied = True

    return applied
