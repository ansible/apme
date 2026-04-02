"""M006: Add ignore_unreachable: true when become + ignore_errors is set."""

from __future__ import annotations

from ruamel.yaml.comments import CommentedMap

from apme_engine.engine.models import ViolationDict


def fix_become_unreachable(task: CommentedMap, violation: ViolationDict) -> bool:
    """Add ``ignore_unreachable: true`` to tasks with become + ignore_errors.

    Args:
        task: Task CommentedMap to modify in-place.
        violation: Violation dict with line.

    Returns:
        True if a change was applied.
    """
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
