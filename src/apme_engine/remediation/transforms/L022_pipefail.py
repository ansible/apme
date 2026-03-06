"""L022: Prepend 'set -o pipefail &&' to shell commands with pipes."""

from __future__ import annotations

from apme_engine.engine.yaml_utils import FormattedYAML
from apme_engine.remediation.registry import TransformResult
from apme_engine.remediation.transforms._helpers import find_task_at_line, get_module_key


_SHELL_MODULES = frozenset({
    "ansible.builtin.shell", "ansible.legacy.shell", "shell",
})


def fix_pipefail(content: str, violation: dict) -> TransformResult:
    """Prepend ``set -o pipefail &&`` to a piped shell command."""
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
    if module_key is None or module_key not in _SHELL_MODULES:
        return TransformResult(content=content, applied=False)

    module_args = task.get(module_key)

    if isinstance(module_args, str):
        if "|" not in module_args or "pipefail" in module_args:
            return TransformResult(content=content, applied=False)
        task[module_key] = "set -o pipefail && " + module_args

    elif isinstance(module_args, dict):
        cmd = module_args.get("cmd", "")
        executable = module_args.get("executable", "")
        if "|" not in cmd:
            return TransformResult(content=content, applied=False)
        if "pipefail" in cmd or "pipefail" in executable:
            return TransformResult(content=content, applied=False)
        module_args["cmd"] = "set -o pipefail && " + cmd

    else:
        return TransformResult(content=content, applied=False)

    return TransformResult(content=yaml.dumps(data), applied=True)
