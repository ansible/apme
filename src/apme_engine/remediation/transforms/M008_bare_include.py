"""M008: Replace bare include: with include_tasks: (or import_tasks:)."""

from __future__ import annotations

from ruamel.yaml.comments import CommentedMap

from apme_engine.engine.models import ViolationDict
from apme_engine.remediation.transforms._helpers import rename_key


def fix_bare_include(task: CommentedMap, violation: ViolationDict) -> bool:
    """Replace ``include:`` with ``ansible.builtin.include_tasks:``.

    Args:
        task: Task CommentedMap to modify in-place.
        violation: Violation dict with line.

    Returns:
        True if a change was applied.
    """
    if "include" not in task:
        return False

    rename_key(task, "include", "ansible.builtin.include_tasks")
    return True
