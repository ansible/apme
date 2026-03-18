"""L007: Replace ansible.builtin.shell with ansible.builtin.command when no shell features."""

from __future__ import annotations

from apme_engine.engine.models import ViolationDict
from apme_engine.remediation.structured import StructuredFile
from apme_engine.remediation.transforms._helpers import (
    get_module_key,
    rename_key,
    violation_line_to_int,
)

_SHELL_CHARS = ("|", "&&", "||", ";", ">", ">>", "<", "$(", "`", "*", "?")

_SHELL_TO_COMMAND = {
    "ansible.builtin.shell": "ansible.builtin.command",
    "ansible.legacy.shell": "ansible.legacy.command",
    "shell": "ansible.builtin.command",
}


def _uses_shell_features(cmd: str) -> bool:
    """Check if command string uses shell features (pipes, redirects, etc).

    Args:
        cmd: Command string to check.

    Returns:
        True if cmd contains shell-specific characters.
    """
    return any(ch in cmd for ch in _SHELL_CHARS)


def fix_shell_to_command(sf: StructuredFile, violation: ViolationDict) -> bool:
    """Replace shell with command when the command string uses no shell features.

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
    if module_key is None or module_key not in _SHELL_TO_COMMAND:
        return False

    module_args = task.get(module_key)
    cmd = ""
    if isinstance(module_args, str):
        cmd = module_args
    elif isinstance(module_args, dict):
        cmd = module_args.get("cmd", "")

    if cmd and _uses_shell_features(cmd):
        return False

    new_key = _SHELL_TO_COMMAND[module_key]
    rename_key(task, module_key, new_key)
    return True
