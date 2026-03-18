"""L015: Strip Jinja delimiters from when clauses."""

from __future__ import annotations

import re

from apme_engine.engine.models import ViolationDict
from apme_engine.remediation.structured import StructuredFile
from apme_engine.remediation.transforms._helpers import violation_line_to_int

_JINJA_EXPR = re.compile(r"\{\{\s*(.+?)\s*\}\}")


def fix_jinja_when(sf: StructuredFile, violation: ViolationDict) -> bool:
    """Replace ``{{ var }}`` in when with bare ``var``.

    Args:
        sf: Parsed YAML file to modify in-place.
        violation: Violation dict with line.

    Returns:
        True if a change was applied.
    """
    task = sf.find_task(violation_line_to_int(violation))
    if task is None:
        return False

    when_val = task.get("when")
    if not isinstance(when_val, str):
        return False

    new_when = _JINJA_EXPR.sub(r"\1", when_val)

    if new_when == when_val:
        return False

    task["when"] = new_when
    return True
