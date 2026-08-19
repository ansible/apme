"""L007: Replace ansible.builtin.shell with ansible.builtin.command when no shell features."""

from __future__ import annotations

from ruamel.yaml.comments import CommentedMap

from apme_engine.engine.models import ViolationDict
from apme_engine.remediation.transforms._helpers import get_module_key, rename_key

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


def _extract_command_string(module_args: object) -> str:
    """Return the inspectable command string from shell module arguments.

    Prefers free-form / ``cmd`` over ``argv``.  Returns empty when the
    command cannot be inspected, in which case the transform must not
    convert shell to command.

    Args:
        module_args: Scalar command string or argument mapping.

    Returns:
        Command string, or empty if none is available.
    """
    if isinstance(module_args, str):
        return module_args
    if not isinstance(module_args, dict):
        return ""
    cmd = module_args.get("cmd", "")
    if isinstance(cmd, str) and cmd:
        return cmd
    argv = module_args.get("argv")
    if isinstance(argv, list) and argv:
        return " ".join(str(part) for part in argv)
    return ""


def fix_shell_to_command(task: CommentedMap, violation: ViolationDict) -> bool:
    """Replace shell with command when the command string uses no shell features.

    Inspects free-form, ``cmd``, and ``argv``.  Refuses to convert when the
    command cannot be inspected or contains shell metacharacters.

    Args:
        task: Task CommentedMap to modify in-place.
        violation: Violation dict with line.

    Returns:
        True if a change was applied.
    """
    module_key = get_module_key(task)
    if module_key is None or module_key not in _SHELL_TO_COMMAND:
        return False

    module_args = task.get(module_key)
    cmd = _extract_command_string(module_args)
    if not cmd or _uses_shell_features(cmd):
        return False

    new_key = _SHELL_TO_COMMAND[module_key]
    rename_key(task, module_key, new_key)
    return True
