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
#: Upper bound on the serialized diagnostic output M040 retains for one upload.
_DIAGNOSTIC_BUDGET_BYTES = 256 * 1024
#: Serialized bytes charged per finding beyond its excerpt text.
_FINDING_OVERHEAD_BYTES = 512
#: Bytes reserved so the terminal incomplete-assessment notice always fits.
_NOTICE_RESERVE_BYTES = 1024
#: Marker appended when the notice's upload path is truncated.
_PATH_TRUNCATION_MARKER = "<truncated>"
#: Longest upload path in bytes the suppression notice echoes.
_MAX_NOTICE_PATH_BYTES = 512
_EXCERPT_OMITTED = "<source line omitted: M040 diagnostic byte budget exceeded>"


def _notice_file(path: str) -> str:
    """Bound the upload path echoed by the suppression notice.

    Args:
        path: Upload path of the first suppressed finding.

    Returns:
        The full path when it fits the notice budget, else a head slice
        with an explicit truncation marker of bounded byte length.
    """
    encoded = path.encode("utf-8", errors="replace")
    if len(encoded) <= _MAX_NOTICE_PATH_BYTES:
        return path
    keep = _MAX_NOTICE_PATH_BYTES - len(_PATH_TRUNCATION_MARKER)
    return encoded[:keep].decode("utf-8", errors="ignore") + _PATH_TRUNCATION_MARKER


def validate_inventory_plugins(files: Mapping[str, bytes]) -> list[ViolationDict]:
    """Assess uploaded Python inventory plugins without executing their code.

    Args:
        files: Source files keyed by their relative upload paths.

    Returns:
        Manual findings at the removed keyword's source line. Serialized
        diagnostic output — excerpts, upload paths, and finding metadata —
        is bounded by a per-upload byte budget; once the budget is
        exhausted, the remaining positions are summarized by one
        incomplete-assessment notice instead of further findings.
    """
    findings: list[ViolationDict] = []
    remaining = _DIAGNOSTIC_BUDGET_BYTES - _NOTICE_RESERVE_BYTES
    first_suppressed: tuple[str, int, int] | None = None
    suppressed_count = 0
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
        path_bytes = len(path.encode("utf-8", errors="replace"))
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
                if len(raw_line) + path_bytes + _FINDING_OVERHEAD_BYTES > remaining:
                    suppressed_count += 1
                    if first_suppressed is None:
                        first_suppressed = (path, line, keyword.col_offset)
                    continue
                remaining -= len(raw_line) + path_bytes + _FINDING_OVERHEAD_BYTES
                if line not in excerpts:
                    excerpts[line] = raw_line.decode("utf-8", errors="replace")
                findings.append(
                    {
                        "rule_id": RULE_ID,
                        "severity": severity_to_label(get_severity(RULE_ID)),
                        "message": _MESSAGE,
                        "file": path,
                        "line": line,
                        "path": f"$[{line}][{keyword.col_offset}]",
                        "original_yaml": excerpts[line],
                        "scope": RuleScope.INVENTORY.value,
                        "remediation_class": "manual-review",
                        "source": "native",
                    }
                )
    if first_suppressed is not None:
        path, line, col_offset = first_suppressed
        findings.append(
            {
                "rule_id": RULE_ID,
                "severity": severity_to_label(get_severity(RULE_ID)),
                "message": (
                    "M040 diagnostic output exceeded its byte budget for this upload;"
                    f" the assessment is incomplete with {suppressed_count} further findings suppressed."
                ),
                "file": _notice_file(path),
                "line": line,
                "path": f"$[{line}][{col_offset}]",
                "original_yaml": _EXCERPT_OMITTED,
                "scope": RuleScope.INVENTORY.value,
                "remediation_class": "manual-review",
                "source": "native",
            }
        )
    return findings
