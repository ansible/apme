"""L015: Strip Jinja delimiters from when clauses."""

from __future__ import annotations

import re

from apme_engine.engine.yaml_utils import FormattedYAML
from apme_engine.remediation.registry import TransformResult
from apme_engine.remediation.transforms._helpers import find_task_at_line

_JINJA_EXPR = re.compile(r"\{\{\s*(.+?)\s*\}\}")


def fix_jinja_when(content: str, violation: dict) -> TransformResult:
    """Replace ``{{ var }}`` in when with bare ``var``."""
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

    when_val = task.get("when")
    if not isinstance(when_val, str):
        return TransformResult(content=content, applied=False)

    new_when = _JINJA_EXPR.sub(r"\1", when_val)

    if new_when == when_val:
        return TransformResult(content=content, applied=False)

    task["when"] = new_when
    return TransformResult(content=yaml.dumps(data), applied=True)
