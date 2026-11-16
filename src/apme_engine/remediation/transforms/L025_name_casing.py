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
        capitalized = prefix_part + rest[0].upper() + rest[1:] if rest else name[0].upper() + name[1:]
    else:
        capitalized = name[0].upper() + name[1:]

    if capitalized == name:
        return False

    task["name"] = capitalized
    return True
