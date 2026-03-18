"""M006: Add ignore_unreachable: true when become + ignore_errors is set."""

from __future__ import annotations

from apme_engine.engine.models import ViolationDict
from apme_engine.remediation.structured import StructuredFile
from apme_engine.remediation.transforms._helpers import violation_line_to_int


def fix_become_unreachable(sf: StructuredFile, violation: ViolationDict) -> bool:
    """Add ``ignore_unreachable: true`` to tasks with become + ignore_errors.

    Args:
        sf: Parsed YAML file to modify in-place.
        violation: Violation dict with line.

    Returns:
        True if a change was applied.
    """
    task = sf.find_task(violation_line_to_int(violation))
    if task is None:
        return False

    if "ignore_unreachable" in task:
        return False

    if not (task.get("become") and task.get("ignore_errors")):
        return False

    items = list(task.items())
    task.clear()
    for k, v in items:
        task[k] = v
        if k == "ignore_errors":
            task["ignore_unreachable"] = True

    return True
