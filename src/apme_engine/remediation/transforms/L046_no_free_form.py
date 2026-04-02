"""L046: Convert free-form module args to dict form.

Handles two cases:
- Command-family modules (command/shell/raw/script): ``cmd: <value>``
- Other modules with key=value strings: parsed into a YAML mapping
"""

from __future__ import annotations

import re

from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from apme_engine.engine.models import ViolationDict
from apme_engine.remediation.transforms._helpers import get_module_key

_YAML_UNSAFE_RE_CHARS = frozenset(":{}[]|>&*!%#`@,")

_COMMAND_MODULES = frozenset(
    {
        "ansible.builtin.command",
        "ansible.builtin.shell",
        "ansible.builtin.raw",
        "ansible.builtin.script",
        "ansible.legacy.command",
        "ansible.legacy.shell",
        "ansible.legacy.raw",
        "command",
        "shell",
        "raw",
        "script",
    }
)

_KV_RE = re.compile(r"(\w+)=")


def _find_closing_quote(s: str, quote_char: str) -> int:
    """Return index of the unescaped closing quote in *s*, or -1.

    Args:
        s: String that includes the opening quote character as the first character.
        quote_char: The quote character to match.

    Returns:
        Index of the closing quote, or -1 if not found.
    """
    i = 1
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            i += 2
            continue
        if s[i] == quote_char:
            return i
        i += 1
    return -1


def _parse_kv_string(value: str) -> CommentedMap | None:
    """Parse ``key=value key2=value2`` into a CommentedMap.

    Args:
        value: String containing key=value pairs.

    Returns:
        CommentedMap with parsed pairs, or None if not parseable.
    """
    if "=" not in value:
        return None

    result = CommentedMap()
    remaining = value.strip()

    while remaining:
        m = _KV_RE.match(remaining)
        if m is None:
            return None

        key = m.group(1)
        remaining = remaining[m.end() :]

        if remaining.startswith(('"', "'")):
            quote_char = remaining[0]
            end = _find_closing_quote(remaining, quote_char)
            if end == -1:
                return None
            val = remaining[1:end]
            remaining = remaining[end + 1 :].lstrip()
        else:
            parts = remaining.split(None, 1)
            val = parts[0]
            remaining = parts[1] if len(parts) > 1 else ""

        result[key] = val

    return result if len(result) > 0 else None


def fix_free_form(task: CommentedMap, violation: ViolationDict) -> bool:
    """Convert free-form module args to dict form.

    For command-family modules: ``command: echo hi`` -> ``command: {cmd: echo hi}``
    For other modules: ``stat: path=/tmp`` -> ``stat: {path: /tmp}``

    Args:
        task: Task CommentedMap to modify in-place.
        violation: Violation dict with line.

    Returns:
        True if a change was applied.
    """
    module_key = get_module_key(task)
    if module_key is None:
        return False

    module_args = task.get(module_key)
    if not isinstance(module_args, str):
        return False

    if module_key in _COMMAND_MODULES:
        new_args = CommentedMap()
        cmd_val: str | DoubleQuotedScalarString = module_args
        if any(c in module_args for c in _YAML_UNSAFE_RE_CHARS):
            cmd_val = DoubleQuotedScalarString(module_args)
        new_args["cmd"] = cmd_val
        task[module_key] = new_args
        return True

    parsed = _parse_kv_string(module_args)
    if parsed is not None and len(parsed) > 0:
        task[module_key] = parsed
        return True

    return False
