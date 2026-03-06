"""L018: Add become: true when become_user is set without become."""

from __future__ import annotations

from apme_engine.engine.yaml_utils import FormattedYAML
from apme_engine.remediation.registry import TransformResult
from apme_engine.remediation.transforms._helpers import find_task_at_line


def fix_become(content: str, violation: dict) -> TransformResult:
    """Add ``become: true`` when ``become_user`` is set."""
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

    if "become_user" not in task:
        return TransformResult(content=content, applied=False)

    if "become" in task:
        return TransformResult(content=content, applied=False)

    items = list(task.items())
    task.clear()
    for k, v in items:
        task[k] = v
        if k == "become_user":
            task["become"] = True

    return TransformResult(content=yaml.dumps(data), applied=True)
