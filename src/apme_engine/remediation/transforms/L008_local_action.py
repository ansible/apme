"""L008: Replace local_action with delegate_to: localhost."""

from __future__ import annotations

from ruamel.yaml.comments import CommentedMap

from apme_engine.engine.yaml_utils import FormattedYAML
from apme_engine.remediation.registry import TransformResult
from apme_engine.remediation.transforms._helpers import find_task_at_line


def fix_local_action(content: str, violation: dict) -> TransformResult:
    """Convert local_action to the module key + delegate_to: localhost."""
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

    la_value = task.get("local_action")
    if la_value is None:
        return TransformResult(content=content, applied=False)

    if isinstance(la_value, str):
        parts = la_value.split(None, 1)
        module_name = parts[0]
        free_form = parts[1] if len(parts) > 1 else None

        del task["local_action"]
        if free_form:
            task[module_name] = free_form
        else:
            task[module_name] = CommentedMap()
        task["delegate_to"] = "localhost"

    elif isinstance(la_value, CommentedMap):
        module_name = la_value.pop("module", None)
        if not module_name:
            return TransformResult(content=content, applied=False)

        del task["local_action"]
        task[module_name] = la_value if la_value else CommentedMap()
        task["delegate_to"] = "localhost"

    else:
        return TransformResult(content=content, applied=False)

    return TransformResult(content=yaml.dumps(data), applied=True)
