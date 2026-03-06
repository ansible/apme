"""L043: Rewrite deprecated bare variable references in loop directives."""

from __future__ import annotations

import re

from apme_engine.engine.yaml_utils import FormattedYAML
from apme_engine.remediation.registry import TransformResult
from apme_engine.remediation.transforms._helpers import find_task_at_line

_BARE_VAR = re.compile(r"^([a-zA-Z_]\w*)$")

_LOOP_KEYS = ("with_items", "with_dict", "with_fileglob", "with_subelements",
              "with_sequence", "with_nested", "with_first_found")


def fix_bare_vars(content: str, violation: dict) -> TransformResult:
    """Wrap bare variable references in Jinja delimiters: ``foo`` -> ``{{ foo }}``."""
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

    applied = False
    for key in _LOOP_KEYS:
        val = task.get(key)
        if isinstance(val, str) and _BARE_VAR.match(val):
            task[key] = "{{ " + val + " }}"
            applied = True

    if not applied:
        return TransformResult(content=content, applied=False)

    return TransformResult(content=yaml.dumps(data), applied=True)
