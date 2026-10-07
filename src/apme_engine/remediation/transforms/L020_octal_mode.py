"""L020: Convert numeric file mode to quoted string with leading zero.

Handles three forms that the YAML parser produces:

- ``mode: 0644`` → YAML 1.1 octal → OctalIntYAML11(420) → ``"0644"``
- ``mode: 644``  → int(644), all digits are valid octal → ``"0644"``
- ``mode: "644"`` → str without leading zero → ``"0644"``
"""

from __future__ import annotations

import re

from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarstring import SingleQuotedScalarString

from apme_engine.engine.models import ViolationDict
from apme_engine.engine.yaml_utils import OctalIntYAML11
from apme_engine.remediation.transforms._helpers import get_module_key

_OCTAL_DIGITS = re.compile(r"^[0-7]{3,4}$")


def fix_octal_mode(task: CommentedMap, violation: ViolationDict) -> bool:
    """Ensure ``mode`` is a quoted octal string with a leading zero.

    Args:
        task: Task CommentedMap to modify in-place.
        violation: Violation dict for context.

    Returns:
        True if a change was applied.
    """
    module_key = get_module_key(task)
    if module_key is None:
        return False

    module_args = task.get(module_key)
    if isinstance(module_args, dict) and "mode" in module_args:
        container = module_args
    elif "mode" in task:
        container = task
    else:
        return False

    mode_val = container["mode"]

    if isinstance(mode_val, str):
        stripped = mode_val.strip()
        if _OCTAL_DIGITS.match(stripped) and not stripped.startswith("0"):
            container["mode"] = SingleQuotedScalarString(f"0{stripped}")
            return True
        return False

    if isinstance(mode_val, int):
        if isinstance(mode_val, OctalIntYAML11):
            octal_digits = format(int(mode_val), "o")
            normalized = octal_digits if octal_digits.startswith("0") else f"0{octal_digits}"
        else:
            decimal_digits = str(mode_val)
            if not _OCTAL_DIGITS.match(decimal_digits):
                return False
            normalized = f"0{decimal_digits}" if not decimal_digits.startswith("0") else decimal_digits
        container["mode"] = SingleQuotedScalarString(normalized)
        return True

    return False
