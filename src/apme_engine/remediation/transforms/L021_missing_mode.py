"""L021: Add explicit mode to file/copy/template tasks missing one."""

from __future__ import annotations

from apme_engine.engine.yaml_utils import FormattedYAML
from apme_engine.remediation.registry import TransformResult
from apme_engine.remediation.transforms._helpers import find_task_at_line, get_module_key


_FILE_MODULES = frozenset({
    "ansible.builtin.copy", "ansible.builtin.file", "ansible.builtin.template",
    "ansible.builtin.lineinfile", "ansible.builtin.replace",
    "ansible.builtin.unarchive", "ansible.builtin.synchronize",
    "ansible.legacy.copy", "ansible.legacy.file", "ansible.legacy.template",
    "copy", "file", "template", "lineinfile", "replace",
    "synchronize", "unarchive", "assemble",
})


def fix_missing_mode(content: str, violation: dict) -> TransformResult:
    """Add mode: '0644' to file-related tasks that lack an explicit mode."""
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
    if module_key is None or module_key not in _FILE_MODULES:
        return TransformResult(content=content, applied=False)

    module_args = task.get(module_key)

    if isinstance(module_args, dict):
        if "mode" in module_args:
            return TransformResult(content=content, applied=False)
        module_args["mode"] = "0644"
    else:
        if "mode" not in task:
            task["mode"] = "0644"
        else:
            return TransformResult(content=content, applied=False)

    return TransformResult(content=yaml.dumps(data), applied=True)
