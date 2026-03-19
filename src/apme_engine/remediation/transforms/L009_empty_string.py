"""L009: Rewrite empty-string comparisons in when to truthiness tests."""

from __future__ import annotations

import re

from apme_engine.engine.models import ViolationDict
from apme_engine.remediation.structured import StructuredFile
from apme_engine.remediation.transforms._helpers import violation_line_to_int

_PATTERNS = [
    (re.compile(r'\b(\w+)\s*==\s*""'), r"\1 | length == 0"),
    (re.compile(r"\b(\w+)\s*==\s*''"), r"\1 | length == 0"),
    (re.compile(r'\b(\w+)\s*!=\s*""'), r"\1 | length > 0"),
    (re.compile(r"\b(\w+)\s*!=\s*''"), r"\1 | length > 0"),
]


def fix_empty_string(sf: StructuredFile, violation: ViolationDict) -> bool:
    """Replace `var == ""` with `var | length == 0` and similar.

    Args:
        sf: Parsed YAML file to modify in-place.
        violation: Violation dict with line.

    Returns:
        True if a change was applied.
    """
    task = sf.find_task(violation_line_to_int(violation), violation)
    if task is None:
        return False

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
