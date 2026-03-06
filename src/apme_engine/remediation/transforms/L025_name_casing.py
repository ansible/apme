"""L025: Capitalize task/play name."""

from __future__ import annotations

from apme_engine.engine.yaml_utils import FormattedYAML
from apme_engine.remediation.registry import TransformResult
from apme_engine.remediation.transforms._helpers import find_task_at_line


def fix_name_casing(content: str, violation: dict) -> TransformResult:
    """Capitalize the first letter of a task or play name."""
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

    name = task.get("name")
    if not isinstance(name, str) or not name:
        return TransformResult(content=content, applied=False)

    if name[0].isupper():
        return TransformResult(content=content, applied=False)

    task["name"] = name[0].upper() + name[1:]
    return TransformResult(content=yaml.dumps(data), applied=True)
