"""M008: Replace bare include: with include_tasks: (or import_tasks:)."""

from __future__ import annotations

from apme_engine.engine.models import ViolationDict
from apme_engine.remediation.structured import StructuredFile
from apme_engine.remediation.transforms._helpers import rename_key, violation_line_to_int


def fix_bare_include(sf: StructuredFile, violation: ViolationDict) -> bool:
    """Replace ``include:`` with ``ansible.builtin.include_tasks:``.

    Args:
        sf: Parsed YAML file to modify in-place.
        violation: Violation dict with line.

    Returns:
        True if a change was applied.
    """
    task = sf.find_task(violation_line_to_int(violation))
    if task is None:
        return False

    if "include" not in task:
        return False

    rename_key(task, "include", "ansible.builtin.include_tasks")
    return True
