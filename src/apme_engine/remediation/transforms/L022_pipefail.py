"""L022: Prepend 'set -o pipefail &&' to shell commands with pipes."""

from __future__ import annotations

from ruamel.yaml.comments import CommentedMap

from apme_engine.engine.models import ViolationDict
from apme_engine.remediation.transforms._helpers import get_module_key

_SHELL_MODULES = frozenset(
    {
        "ansible.builtin.shell",
        "ansible.legacy.shell",
        "shell",
    }
)


def fix_pipefail(task: CommentedMap, violation: ViolationDict) -> bool:
    """Prepend ``set -o pipefail &&`` to a piped shell command.

    Args:
        task: Task CommentedMap to modify in-place.
        violation: Violation dict with line.

    Returns:
        True if a change was applied.
    """
    module_key = get_module_key(task)
    if module_key is None or module_key not in _SHELL_MODULES:
        return False

    module_args = task.get(module_key)

    if isinstance(module_args, str):
        if "|" not in module_args or "pipefail" in module_args:
            return False
        task[module_key] = "set -o pipefail && " + module_args

    elif isinstance(module_args, dict):
        cmd = module_args.get("cmd", "")
        executable = module_args.get("executable", "")
        if "|" not in cmd:
            return False
        if "pipefail" in cmd or "pipefail" in executable:
            return False
        module_args["cmd"] = "set -o pipefail && " + cmd

    else:
        return False

    return True
