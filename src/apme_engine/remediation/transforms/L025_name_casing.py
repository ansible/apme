"""L025: Capitalize task/play name."""

from __future__ import annotations

from ruamel.yaml.comments import CommentedMap

from apme_engine.engine.models import ViolationDict


def fix_name_casing(task: CommentedMap, violation: ViolationDict) -> bool:
    """Capitalize the first letter of a task or play name.

    Args:
        task: Task CommentedMap to modify in-place.
        violation: Violation dict with line.

    Returns:
        True if a change was applied.
    """
    name = task.get("name")
    if not isinstance(name, str) or not name:
        return False

    sep = " | "
    if sep in name:
        idx = name.rfind(sep) + len(sep)
        prefix_part = name[:idx]
        rest = name[idx:]
        if not rest or rest[0].isupper():
            return False
        task["name"] = prefix_part + rest[0].upper() + rest[1:]
        return True

    if name[0].isupper():
        return False

    task["name"] = name[0].upper() + name[1:]
    return True
