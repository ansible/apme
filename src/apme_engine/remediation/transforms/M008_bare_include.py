"""M008: Replace bare include: with include_tasks: (or import_tasks:)."""

from __future__ import annotations

from apme_engine.engine.yaml_utils import FormattedYAML
from apme_engine.remediation.registry import TransformResult
from apme_engine.remediation.transforms._helpers import find_task_at_line, rename_key


def fix_bare_include(content: str, violation: dict) -> TransformResult:
    """Replace ``include:`` with ``ansible.builtin.include_tasks:``."""
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

    if "include" not in task:
        return TransformResult(content=content, applied=False)

    rename_key(task, "include", "ansible.builtin.include_tasks")

    return TransformResult(content=yaml.dumps(data), applied=True)
