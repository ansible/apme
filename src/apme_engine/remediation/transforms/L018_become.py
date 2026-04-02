"""L018: Add become: true when become_user is set without become."""

from __future__ import annotations

from ruamel.yaml.comments import CommentedMap

from apme_engine.engine.models import ViolationDict


def fix_become(task: CommentedMap, violation: ViolationDict) -> bool:
    """Add ``become: true`` when ``become_user`` is set.

    Args:
        task: Task CommentedMap to modify in-place.
        violation: Violation dict with line.

    Returns:
        True if a change was applied.
    """
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
