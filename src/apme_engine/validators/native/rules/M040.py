"""Detect the removed disable_lookups argument in Python inventory plugins."""

from __future__ import annotations

import ast
import json
from collections.abc import Mapping
from itertools import pairwise
from pathlib import PurePosixPath

from apme_engine.engine.models import ViolationDict
from apme_engine.graph.severity import get_severity, severity_to_label
from apme_engine.graph.types import RuleScope

RULE_ID = "M040"

_MESSAGE = "Remove the disable_lookups keyword from _compose(); it has no effect."
#: Upper bound on the serialized M040 diagnostic output for one upload. Charges
#: use JSON-escaped sizes, which upper-bound every transport form.
_DIAGNOSTIC_BUDGET_BYTES = 256 * 1024
#: Serialized bytes charged per finding beyond its excerpt and upload path:
#: message, position, metadata, and JSON structure.
_FINDING_OVERHEAD_BYTES = 512
#: Bytes reserved so the terminal incomplete-assessment notice always fits,
#: including its truncated upload path in escaped form.
_NOTICE_RESERVE_BYTES = 2048
#: Marker appended when the notice's upload path is truncated.
_PATH_TRUNCATION_MARKER = "<truncated>"
#: Longest upload path in JSON-escaped bytes the suppression notice echoes.
_MAX_NOTICE_PATH_BYTES = 512
_EXCERPT_OMITTED = "<source line omitted: M040 diagnostic byte budget exceeded>"


def _json_size(value: str) -> int:
    """Measure a string in its most expanded serialized form.

    Args:
        value: Excerpt or upload path text.

    Returns:
        JSON-escaped length in bytes, which upper-bounds the UTF-8 protobuf
        and non-escaping JSON transports.
    """
    return len(json.dumps(value, ensure_ascii=True))


def _notice_file(path: str) -> str:
    """Bound the upload path echoed by the suppression notice.

    Args:
        path: Upload path of the first suppressed finding.

    Returns:
        The full path when its escaped form fits the notice budget, else a
        head slice with an explicit truncation marker whose escaped length
        stays within the budget.
    """
    if _json_size(path) <= _MAX_NOTICE_PATH_BYTES:
        return path
    budget = _MAX_NOTICE_PATH_BYTES - _json_size(_PATH_TRUNCATION_MARKER)
    encoded = path.encode("utf-8", errors="replace")
    low, high = 0, len(encoded)
    while low < high:
        mid = (low + high + 1) // 2
        if _json_size(encoded[:mid].decode("utf-8", errors="ignore")) <= budget:
            low = mid
        else:
            high = mid - 1
    return encoded[:low].decode("utf-8", errors="ignore") + _PATH_TRUNCATION_MARKER


def validate_inventory_plugins(files: Mapping[str, bytes]) -> list[ViolationDict]:
    """Assess uploaded Python inventory plugins without executing their code.

    Args:
        files: Source files keyed by their relative upload paths.

    Returns:
        Manual findings at the removed keyword's source line. Serialized
        diagnostic output — excerpts, upload paths, and finding metadata,
        measured in JSON-escaped form — is bounded by a per-upload byte
        budget; once the budget is exhausted, the remaining positions are
        summarized by one incomplete-assessment notice instead of further
        findings.
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
        path_charge = _json_size(path)
        excerpts: dict[int, tuple[str | None, int]] = {}
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
                if line in excerpts:
                    excerpt, excerpt_charge = excerpts[line]
                else:
                    excerpt = lines[line - 1].decode("utf-8", errors="replace")
                    excerpt_charge = _json_size(excerpt)
                if excerpt_charge + path_charge + _FINDING_OVERHEAD_BYTES > remaining:
                    suppressed_count += 1
                    if first_suppressed is None:
                        first_suppressed = (path, line, keyword.col_offset)
                    # Cache the charge, not the decoded text, so repeated
                    # calls on an over-budget line skip re-decoding while
                    # memory stays bounded by the admitted output.
                    if excerpt is not None:
                        excerpts[line] = (None, excerpt_charge)
                    continue
                remaining -= excerpt_charge + path_charge + _FINDING_OVERHEAD_BYTES
                excerpts[line] = (excerpt, excerpt_charge)
                findings.append(
                    {
                        "rule_id": RULE_ID,
                        "severity": severity_to_label(get_severity(RULE_ID)),
                        "message": _MESSAGE,
                        "file": path,
                        "line": line,
                        "path": f"$[{line}][{keyword.col_offset}]",
                        "original_yaml": excerpt,
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
