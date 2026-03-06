"""L020: Convert numeric file mode to quoted string with leading zero.

Uses line-level string matching rather than AST to correctly handle YAML 1.1
octal ambiguity (``0644`` → int 420 vs ``644`` → int 644).
"""

from __future__ import annotations

import re

from apme_engine.remediation.registry import TransformResult

_MODE_NUMERIC = re.compile(
    r"^(\s+mode:\s+)"
    r"(0[0-7]{3,4}|[0-7]{3,4})"
    r"(\s*(?:#.*)?)$",
)

_MODE_QUOTED_NO_ZERO = re.compile(
    r'^(\s+mode:\s+)(["\'])([0-7]{3,4})\2(\s*(?:#.*)?)$',
)


def fix_octal_mode(content: str, violation: dict) -> TransformResult:
    """Convert ``mode: 644`` or ``mode: 0644`` to ``mode: "0644"``."""
    target_line = violation.get("line", 0)
    if isinstance(target_line, (list, tuple)):
        target_line = target_line[0] if target_line else 0

    lines = content.splitlines(keepends=True)
    applied = False

    for i, line in enumerate(lines):
        if i + 1 < target_line - 5 or i + 1 > target_line + 20:
            continue
        stripped = line.rstrip("\n")
        nl = "\n" if line.endswith("\n") else ""

        m = _MODE_NUMERIC.match(stripped)
        if m:
            prefix, digits, suffix = m.group(1), m.group(2), m.group(3) or ""
            if digits.startswith("0") and len(digits) >= 4:
                quoted = f'"{digits}"'
            else:
                quoted = f'"0{digits}"'
            lines[i] = prefix + quoted + suffix + nl
            applied = True
            break

        m2 = _MODE_QUOTED_NO_ZERO.match(stripped)
        if m2:
            prefix, quote, digits, suffix = m2.group(1), m2.group(2), m2.group(3), m2.group(4) or ""
            if not digits.startswith("0"):
                lines[i] = f'{prefix}{quote}0{digits}{quote}{suffix}{nl}'
                applied = True
                break

    if not applied:
        return TransformResult(content=content, applied=False)

    return TransformResult(content="".join(lines), applied=True)
