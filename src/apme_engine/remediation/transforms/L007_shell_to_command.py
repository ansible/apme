"""L007: Replace ansible.builtin.shell with ansible.builtin.command when no shell features."""

from __future__ import annotations

from apme_engine.engine.yaml_utils import FormattedYAML
from apme_engine.remediation.registry import TransformResult
from apme_engine.remediation.transforms._helpers import find_task_at_line, get_module_key, rename_key


_SHELL_CHARS = ("|", "&&", "||", ";", ">", ">>", "<", "$(", "`", "*", "?")

_SHELL_TO_COMMAND = {
    "ansible.builtin.shell": "ansible.builtin.command",
    "ansible.legacy.shell": "ansible.legacy.command",
    "shell": "ansible.builtin.command",
}


def _uses_shell_features(cmd: str) -> bool:
    return any(ch in cmd for ch in _SHELL_CHARS)


def fix_shell_to_command(content: str, violation: dict) -> TransformResult:
    """Replace shell with command when the command string uses no shell features."""
    yaml = FormattedYAML(typ="rt", pure=True, version=(1, 1))

    try:
        data = yaml.load(content)
    except Exception:
        return TransformResult(content=content, applied=False)

    line = violation.get("line", 0)
    if isinstance(line, (list, tuple)):
        line = line[0] if line else 0
    task = find_task_at_line(data, line)
    if task is None:
        return TransformResult(content=content, applied=False)

    module_key = get_module_key(task)
    if module_key is None or module_key not in _SHELL_TO_COMMAND:
        return TransformResult(content=content, applied=False)

    module_args = task.get(module_key)
    cmd = ""
    if isinstance(module_args, str):
        cmd = module_args
    elif isinstance(module_args, dict):
        cmd = module_args.get("cmd", "")

    if cmd and _uses_shell_features(cmd):
        return TransformResult(content=content, applied=False)

    new_key = _SHELL_TO_COMMAND[module_key]
    rename_key(task, module_key, new_key)

    return TransformResult(content=yaml.dumps(data), applied=True)
