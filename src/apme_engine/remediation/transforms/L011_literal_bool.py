"""L011: Strip literal true/false/True/False comparisons from when clauses."""

from __future__ import annotations

import re

from apme_engine.engine.yaml_utils import FormattedYAML
from apme_engine.remediation.registry import TransformResult
from apme_engine.remediation.transforms._helpers import find_task_at_line

_REPLACEMENTS = [
    # Equality — simplify to bare variable (or negation)
    (re.compile(r"\b(\w+)\s*==\s*(?:true|True)\b"), r"\1"),
    (re.compile(r"\b(\w+)\s*==\s*(?:false|False)\b"), r"not \1"),
    (re.compile(r"\b(\w+)\s*!=\s*(?:true|True)\b"), r"not \1"),
    (re.compile(r"\b(\w+)\s*!=\s*(?:false|False)\b"), r"\1"),
    # Jinja `is` tests
    (re.compile(r"\b(\w+)\s+is\s+true\b"), r"\1"),
    (re.compile(r"\b(\w+)\s+is\s+false\b"), r"not \1"),
    (re.compile(r"\b(\w+)\s+is\s+not\s+true\b"), r"not \1"),
    (re.compile(r"\b(\w+)\s+is\s+not\s+false\b"), r"\1"),
]


def fix_literal_bool(content: str, violation: dict) -> TransformResult:
    """Remove literal true/false comparisons from when clause."""
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
    for pat, repl in _REPLACEMENTS:
        new_when = pat.sub(repl, new_when)

    if new_when == when_val:
        return TransformResult(content=content, applied=False)

    task["when"] = new_when
    return TransformResult(content=yaml.dumps(data), applied=True)
