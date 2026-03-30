"""Minimal stdlib-only YAML subset parser for galaxy.yml and meta/main.yml.

Handles simple key:value pairs, one-level nested mappings, list items,
comments, and quoted strings. Returns empty dict on any parse failure.
This avoids pulling in PyYAML as a dependency per ADR-014.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _strip_quotes(value: str) -> str:
    """Strip matching single or double quotes from a string value.

    Args:
        value: The string to unquote.

    Returns:
        The unquoted string, or the original if not quoted.
    """
    if len(value) >= 2 and ((value[0] == "'" and value[-1] == "'") or (value[0] == '"' and value[-1] == '"')):
        return value[1:-1]
    return value


def parse_yaml_subset(text: str) -> dict[str, str | list[str] | dict[str, str]]:
    """Parse a minimal subset of YAML used in Ansible metadata files.

    Supports simple key:value pairs, one-level nested mappings, list items
    (``- item``), comments (``#``), document markers (``---``), and
    single/double quoted values. Never raises on malformed input.

    Args:
        text: Raw YAML text content to parse.

    Returns:
        Parsed dictionary. Values are strings, lists of strings, or
        dicts of string-to-string. Returns empty dict on any error.
    """
    try:
        result: dict[str, str | list[str] | dict[str, str]] = {}
        current_key: str | None = None

        for line in text.splitlines():
            stripped = line.strip()

            # Skip empty lines, comments, and document markers
            if not stripped or stripped.startswith("#") or stripped == "---":
                continue

            # Determine indent level
            indent = len(line) - len(line.lstrip())

            if indent == 0:
                # Top-level key:value
                if ":" not in stripped:
                    continue
                key, _, val = stripped.partition(":")
                key = key.strip()
                if not key:
                    continue
                val = val.strip()
                val = _strip_quotes(val)
                result[key] = val
                current_key = key if val == "" else None
            elif indent > 0 and current_key is not None:
                # Nested content under a top-level key with empty value
                if stripped.startswith("- "):
                    # List item
                    item = stripped[2:].strip()
                    item = _strip_quotes(item)
                    existing = result.get(current_key)
                    if isinstance(existing, list):
                        existing.append(item)
                    else:
                        result[current_key] = [item]
                elif ":" in stripped:
                    # Nested mapping entry
                    key, _, val = stripped.partition(":")
                    key = key.strip()
                    val = val.strip()
                    val = _strip_quotes(val)
                    existing = result.get(current_key)
                    if isinstance(existing, dict):
                        existing[key] = val
                    else:
                        result[current_key] = {key: val}

        return result
    except Exception:
        logger.warning("Failed to parse YAML subset, returning empty dict", exc_info=True)
        return {}
