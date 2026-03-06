"""L046: Convert free-form command/shell/raw to dict with cmd key."""

from __future__ import annotations

from ruamel.yaml.comments import CommentedMap

from apme_engine.engine.yaml_utils import FormattedYAML
from apme_engine.remediation.registry import TransformResult
from apme_engine.remediation.transforms._helpers import find_task_at_line, get_module_key


_FREE_FORM_MODULES = frozenset({
    "ansible.builtin.command", "ansible.builtin.shell", "ansible.builtin.raw",
    "ansible.builtin.script",
    "ansible.legacy.command", "ansible.legacy.shell", "ansible.legacy.raw",
    "command", "shell", "raw", "script",
})


def fix_free_form(content: str, violation: dict) -> TransformResult:
    """Convert ``command: echo hi`` to ``command: { cmd: echo hi }``."""
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
    if module_key is None or module_key not in _FREE_FORM_MODULES:
        return TransformResult(content=content, applied=False)

    module_args = task.get(module_key)
    if not isinstance(module_args, str):
        return TransformResult(content=content, applied=False)

    new_args = CommentedMap()
    new_args["cmd"] = module_args
    task[module_key] = new_args

    return TransformResult(content=yaml.dumps(data), applied=True)
