"""M009: Convert with_items/with_dict/etc to loop:."""

from __future__ import annotations

from apme_engine.engine.yaml_utils import FormattedYAML
from apme_engine.remediation.registry import TransformResult
from apme_engine.remediation.transforms._helpers import find_task_at_line


_WITH_SIMPLE = frozenset({
    "with_items", "with_list", "with_flattened",
})


def fix_with_to_loop(content: str, violation: dict) -> TransformResult:
    """Convert simple ``with_items`` to ``loop:``.

    Only handles the straightforward cases (with_items, with_list,
    with_flattened -> loop).  More complex with_* forms (with_dict,
    with_subelements) need manual review or AI.
    """
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

    with_key = violation.get("with_key", "")

    if with_key in _WITH_SIMPLE and with_key in task:
        value = task.pop(with_key)
        task["loop"] = value
        return TransformResult(content=yaml.dumps(data), applied=True)

    for k in list(_WITH_SIMPLE):
        if k in task:
            value = task.pop(k)
            task["loop"] = value
            return TransformResult(content=yaml.dumps(data), applied=True)

    return TransformResult(content=content, applied=False)
