"""L013: Add changed_when: false to command/shell/raw tasks missing it."""

from __future__ import annotations

from apme_engine.engine.models import ViolationDict
from apme_engine.remediation.structured import StructuredFile
from apme_engine.remediation.transforms._helpers import get_module_key, violation_line_to_int

_CMD_MODULES = frozenset(
    {
        "ansible.builtin.command",
        "ansible.builtin.shell",
        "ansible.builtin.raw",
        "ansible.legacy.command",
        "ansible.legacy.shell",
        "ansible.legacy.raw",
        "command",
        "shell",
        "raw",
    }
)


def fix_changed_when(sf: StructuredFile, violation: ViolationDict) -> bool:
    """Add ``changed_when: false`` to command/shell/raw tasks.

    This is a conservative default — the task may actually change state,
    but the user should audit and adjust the condition.

    Args:
        sf: Parsed YAML file to modify in-place.
        violation: Violation dict with line.

    Returns:
        True if a change was applied.
    """
    task = sf.find_task(violation_line_to_int(violation))
    if task is None:
        return False

    module_key = get_module_key(task)
    if module_key is None or module_key not in _CMD_MODULES:
        return False

    if "changed_when" in task:
        return False

    module_args = task.get(module_key)
    if isinstance(module_args, dict) and ("creates" in module_args or "removes" in module_args):
        return False

    task["changed_when"] = False
    return True
