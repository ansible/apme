"""Detect the removed disable_lookups argument in Python inventory plugins."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from itertools import pairwise
from pathlib import PurePosixPath

from apme_engine.engine.models import ViolationDict
from apme_engine.graph.severity import get_severity, severity_to_label
from apme_engine.graph.types import RuleScope

RULE_ID = "M040"

_MESSAGE = "Remove the disable_lookups keyword from _compose(); it has no effect."
#: Upper bound on excerpt bytes retained for one upload, including a per-finding
#: allowance for the rest of the serialized diagnostic (message, position, metadata).
_DIAGNOSTIC_BUDGET_BYTES = 256 * 1024
#: Serialized bytes charged per finding beyond its excerpt text.
_FINDING_OVERHEAD_BYTES = 512
_EXCERPT_OMITTED = "<source line omitted: M040 diagnostic byte budget exceeded>"
_INCOMPLETE_SUFFIX = (
    " Source excerpt omitted: M040 exceeded its diagnostic byte budget for this upload; the assessment is incomplete."
)


def validate_inventory_plugins(files: Mapping[str, bytes]) -> list[ViolationDict]:
    """Assess uploaded Python inventory plugins without executing their code.

    Args:
        files: Source files keyed by their relative upload paths.

    Returns:
        Manual findings at the removed keyword's source line. Excerpt text is
        bounded by a per-upload diagnostic byte budget; findings past the
        budget keep their source position but omit the excerpt and say so.
    """
    findings: list[ViolationDict] = []
    remaining = _DIAGNOSTIC_BUDGET_BYTES
    for path, content in files.items():
        source = PurePosixPath(path)
        parents = source.parts[:-1]
        collection_plugin = ("plugins", "inventory") in pairwise(parents)
        if source.suffix != ".py" or not (collection_plugin or "inventory_plugins" in parents):
            continue
        try:
            tree = ast.parse(content, filename=path)
        except (SyntaxError, ValueError, RecursionError):
            continue
        lines = content.splitlines()
        excerpts: dict[int, str] = {}
        for node in ast.walk(tree):
            if (
                not isinstance(node, ast.Call)
                or not isinstance(node.func, ast.Attribute)
                or node.func.attr != "_compose"
            ):
                continue
            for keyword in node.keywords:
                if keyword.arg != "disable_lookups":
                    continue
                line = keyword.lineno
                raw_line = lines[line - 1]
                message = _MESSAGE
                if len(raw_line) + _FINDING_OVERHEAD_BYTES <= remaining:
                    remaining -= len(raw_line) + _FINDING_OVERHEAD_BYTES
                    if line not in excerpts:
                        excerpts[line] = raw_line.decode("utf-8", errors="replace")
                    excerpt = excerpts[line]
                else:
                    message += _INCOMPLETE_SUFFIX
                    excerpt = _EXCERPT_OMITTED
                findings.append(
                    {
                        "rule_id": RULE_ID,
                        "severity": severity_to_label(get_severity(RULE_ID)),
                        "message": message,
                        "file": path,
                        "line": line,
                        "path": f"$[{line}][{keyword.col_offset}]",
                        "original_yaml": excerpt,
                        "scope": RuleScope.INVENTORY.value,
                        "remediation_class": "manual-review",
                        "source": "native",
                    }
                )
    return findings
