"""L021: Add explicit mode to file/copy/template tasks missing one."""

from __future__ import annotations

from ruamel.yaml.comments import CommentedMap

from apme_engine.engine.models import ViolationDict
from apme_engine.remediation.transforms._helpers import get_module_key

_FILE_MODULES = frozenset(
    {
        "ansible.builtin.copy",
        "ansible.builtin.file",
        "ansible.builtin.template",
        "ansible.builtin.lineinfile",
        "ansible.builtin.replace",
        "ansible.builtin.unarchive",
        "ansible.builtin.synchronize",
        "ansible.legacy.copy",
        "ansible.legacy.file",
        "ansible.legacy.template",
        "copy",
        "file",
        "template",
        "lineinfile",
        "replace",
        "synchronize",
        "unarchive",
        "assemble",
    }
)


def fix_missing_mode(task: CommentedMap, violation: ViolationDict) -> bool:
    """Add mode: '0644' to file-related tasks that lack an explicit mode.

    Args:
        task: Task CommentedMap to modify in-place.
        violation: Violation dict with line.

    Returns:
        True if a change was applied.
    """
    module_key = get_module_key(task)
    if module_key is None or module_key not in _FILE_MODULES:
        return False

    module_args = task.get(module_key)

    if isinstance(module_args, dict):
        if "mode" in module_args:
            return False
        module_args["mode"] = "0644"
    else:
        if "mode" not in task:
            task["mode"] = "0644"
        else:
            return False

    return True
