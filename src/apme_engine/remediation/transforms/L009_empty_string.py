"""L009: Rewrite empty-string comparisons in when to truthiness tests."""

from __future__ import annotations

import re

from apme_engine.engine.yaml_utils import FormattedYAML
from apme_engine.remediation.registry import TransformResult
from apme_engine.remediation.transforms._helpers import find_task_at_line


_PATTERNS = [
    (re.compile(r'\b(\w+)\s*==\s*""'), r"\1 | length == 0"),
    (re.compile(r"\b(\w+)\s*==\s*''"), r"\1 | length == 0"),
    (re.compile(r'\b(\w+)\s*!=\s*""'), r"\1 | length > 0"),
    (re.compile(r"\b(\w+)\s*!=\s*''"), r"\1 | length > 0"),
]


def fix_empty_string(content: str, violation: dict) -> TransformResult:
    """Replace `var == ""` with `var | length == 0` and similar."""
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

    new_when = when_val
    for pat, repl in _PATTERNS:
        new_when = pat.sub(repl, new_when)

    if new_when == when_val:
        return TransformResult(content=content, applied=False)

    task["when"] = new_when
    return TransformResult(content=yaml.dumps(data), applied=True)
