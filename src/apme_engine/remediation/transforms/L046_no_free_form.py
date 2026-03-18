"""L046: Convert free-form command/shell/raw to dict with cmd key."""

from __future__ import annotations

from ruamel.yaml.comments import CommentedMap

from apme_engine.engine.models import ViolationDict
from apme_engine.remediation.structured import StructuredFile
from apme_engine.remediation.transforms._helpers import get_module_key, violation_line_to_int

_FREE_FORM_MODULES = frozenset(
    {
        "ansible.builtin.command",
        "ansible.builtin.shell",
        "ansible.builtin.raw",
        "ansible.builtin.script",
        "ansible.legacy.command",
        "ansible.legacy.shell",
        "ansible.legacy.raw",
        "command",
        "shell",
        "raw",
        "script",
    }
)


def fix_free_form(sf: StructuredFile, violation: ViolationDict) -> bool:
    """Convert ``command: echo hi`` to ``command: { cmd: echo hi }``.

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
    if module_key is None or module_key not in _FREE_FORM_MODULES:
        return False

    module_args = task.get(module_key)
    if not isinstance(module_args, str):
        return False

    new_args = CommentedMap()
    new_args["cmd"] = module_args
    task[module_key] = new_args

    return True
