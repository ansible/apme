"""L012: Replace state: latest with state: present."""

from __future__ import annotations

from apme_engine.engine.yaml_utils import FormattedYAML
from apme_engine.remediation.registry import TransformResult
from apme_engine.remediation.transforms._helpers import find_task_at_line, get_module_key


def fix_latest(content: str, violation: dict) -> TransformResult:
    """Replace ``state: latest`` with ``state: present``."""
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
    if module_key is None:
        return TransformResult(content=content, applied=False)

    module_args = task.get(module_key)
    if isinstance(module_args, dict) and module_args.get("state") == "latest":
        module_args["state"] = "present"
        return TransformResult(content=yaml.dumps(data), applied=True)

    return TransformResult(content=content, applied=False)
