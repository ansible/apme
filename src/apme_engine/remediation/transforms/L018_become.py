"""L018: Add become: true when become_user is set without become."""

from __future__ import annotations

from apme_engine.engine.models import ViolationDict
from apme_engine.remediation.structured import StructuredFile
from apme_engine.remediation.transforms._helpers import violation_line_to_int


def fix_become(sf: StructuredFile, violation: ViolationDict) -> bool:
    """Add ``become: true`` when ``become_user`` is set.

    Args:
        sf: Parsed YAML file to modify in-place.
        violation: Violation dict with line.

    Returns:
        True if a change was applied.
    """
    task = sf.find_task(violation_line_to_int(violation), violation)
    if task is None:
        return False

    if "become_user" not in task:
        return False

    if "become" in task:
        return False

    items = list(task.items())
    task.clear()
    for k, v in items:
        task[k] = v
        if k == "become_user":
            task["become"] = True

    return True
