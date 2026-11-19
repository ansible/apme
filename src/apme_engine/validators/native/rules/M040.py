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


def validate_inventory_plugins(files: Mapping[str, bytes]) -> list[ViolationDict]:
    """Assess uploaded Python inventory plugins without executing their code.

    Args:
        files: Source files keyed by their relative upload paths.

    Returns:
        Manual findings at the removed keyword's source line.
    """
    findings: list[ViolationDict] = []
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
                findings.append(
                    {
                        "rule_id": RULE_ID,
                        "severity": severity_to_label(get_severity(RULE_ID)),
                        "message": "Remove the disable_lookups keyword from _compose(); it has no effect.",
                        "file": path,
                        "line": line,
                        "path": f"$[{line}][{keyword.col_offset}]",
                        "original_yaml": lines[line - 1].decode("utf-8", errors="replace"),
                        "scope": RuleScope.INVENTORY.value,
                        "remediation_class": "manual-review",
                        "source": "native",
                    }
                )
    return findings
