"""L025: Capitalize task/play name."""

from __future__ import annotations

from apme_engine.engine.models import ViolationDict
from apme_engine.remediation.structured import StructuredFile
from apme_engine.remediation.transforms._helpers import violation_line_to_int


def fix_name_casing(sf: StructuredFile, violation: ViolationDict) -> bool:
    """Capitalize the first letter of a task or play name.

    Args:
        sf: Parsed YAML file to modify in-place.
        violation: Violation dict with line.

    Returns:
        True if a change was applied.
    """
    task = sf.find_task(violation_line_to_int(violation), violation)
    if task is None:
        return False

    name = task.get("name")
    if not isinstance(name, str) or not name:
        return False

    if name[0].isupper():
        return False

    task["name"] = name[0].upper() + name[1:]
    return True
