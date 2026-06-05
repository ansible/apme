#!/usr/bin/env python3
"""Validate SKILL.md frontmatter fields against spec + extended allowlist.

This hook is a **superset of skillmark E030**. Skillmark validates strictly
against the agentskills.io specification, which does not yet include fields
that major agent clients (Claude Code, VS Code Copilot) already support.

We disable E030 in ``.skillmark.toml`` and use this hook instead, which
allows both the spec fields and the emerging standard fields.

Spec fields (agentskills.io specification):
    https://github.com/agentskills/agentskills/blob/main/docs/specification.mdx

Extended fields (supported by Claude Code and VS Code, proposed for spec):
    - ``user-invocable``: Controls slash-command menu visibility (default true)
    - ``argument-hint``: Autocomplete hint for skill arguments
    - ``disable-model-invocation``: Prevents agent auto-triggering
    - ``triggers``: Invocation phrases for skill discovery

References:
    - Claude Code: https://code.claude.com/docs/en/skills.md
    - VS Code:     https://code.visualstudio.com/docs/copilot/customization/agent-skills
    - Spec issue:  https://github.com/agentskills/agentskills/issues/105

Sunset plan:
    This script exists because the agentskills.io spec has not yet adopted
    the extended fields listed above.  Once the spec merges the proposal in
    issue #105 and skillmark's E030 recognizes the new fields, this script
    can be removed and E030 re-enabled in ``.skillmark.toml``.

    Use the ``/check-skill-spec`` skill (see ``.agents/skills/check-skill-spec/``)
    to periodically compare the upstream spec against this allowlist and
    determine whether this script is still needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

_FRONTMATTER_PARTS = 3

# agentskills.io specification fields
_SPEC_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "license",
        "compatibility",
        "metadata",
        "allowed-tools",
    }
)

# Fields supported by Claude Code / VS Code but not yet in the spec
_EXTENDED_FIELDS: frozenset[str] = frozenset(
    {
        "user-invocable",
        "argument-hint",
        "disable-model-invocation",
        "triggers",
    }
)

_ALLOWED_FIELDS: frozenset[str] = _SPEC_FIELDS | _EXTENDED_FIELDS

_EXCLUDED_COMPONENTS: frozenset[str] = frozenset({".tox", ".venv", "venv", "node_modules"})
_EXCLUDED_PREFIXES: tuple[str, ...] = ("tests/fixtures",)

# Fields required by this project (beyond the spec's own requirements)
_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "user-invocable",
    }
)


def extract_frontmatter(path: Path) -> dict[str, object] | None:
    """Return parsed frontmatter dict, or None if missing/invalid.

    Args:
        path: Path to a SKILL.md file.

    Returns:
        Parsed YAML frontmatter as a dict, or None if absent/unparseable.
    """
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return None

    parts = content.split("---", 2)
    if len(parts) < _FRONTMATTER_PARTS:
        return None

    try:
        result: object = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None
    if not isinstance(result, dict) or not all(isinstance(k, str) for k in result):
        return None
    return result


def check_skill(path: Path) -> list[str]:
    """Validate frontmatter fields for a single SKILL.md.

    Args:
        path: Path to a SKILL.md file.

    Returns:
        List of error messages (empty if valid).
    """
    fm = extract_frontmatter(path)
    if fm is None:
        return [f"{path}: missing or invalid frontmatter"]

    errors = [f"{path}: unknown field '{field}'" for field in sorted(fm) if field not in _ALLOWED_FIELDS]
    errors.extend(f"{path}: missing required field '{field}'" for field in sorted(_REQUIRED_FIELDS) if field not in fm)
    return errors


def _is_excluded(path: Path) -> bool:
    """Return True if path is under an excluded directory.

    Args:
        path: Path to check against exclusion list.

    Returns:
        Whether the path should be skipped.
    """
    if _EXCLUDED_COMPONENTS & set(path.parts):
        return True
    return any(path.is_relative_to(prefix) for prefix in _EXCLUDED_PREFIXES)


def main() -> int:
    """Check all SKILL.md files for valid frontmatter fields.

    Returns:
        Exit code: 0 if all skills pass, 1 if errors found.
    """
    root = Path()
    skill_files = sorted(p for p in root.glob("**/SKILL.md") if not _is_excluded(p))

    if not skill_files:
        print("No SKILL.md files found")
        return 0

    all_errors: list[str] = []
    for skill_file in skill_files:
        all_errors.extend(check_skill(skill_file))

    if all_errors:
        print("Skill frontmatter errors:")
        for error in all_errors:
            print(f"  - {error}")
        return 1

    print(f"All {len(skill_files)} skills have valid frontmatter fields")
    return 0


if __name__ == "__main__":
    sys.exit(main())
