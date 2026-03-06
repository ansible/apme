"""M006: Add ignore_unreachable: true when become + ignore_errors is set."""

from __future__ import annotations

from apme_engine.engine.yaml_utils import FormattedYAML
from apme_engine.remediation.registry import TransformResult
from apme_engine.remediation.transforms._helpers import find_task_at_line


def fix_become_unreachable(content: str, violation: dict) -> TransformResult:
    """Add ``ignore_unreachable: true`` to tasks with become + ignore_errors."""
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

    if "ignore_unreachable" in task:
        return TransformResult(content=content, applied=False)

    if not (task.get("become") and task.get("ignore_errors")):
        return TransformResult(content=content, applied=False)

    items = list(task.items())
    task.clear()
    for k, v in items:
        task[k] = v
        if k == "ignore_errors":
            task["ignore_unreachable"] = True

    return TransformResult(content=yaml.dumps(data), applied=True)
