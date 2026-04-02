"""GraphRule L043: detect deprecated bare variables (prefer explicit form).

Graph-aware port of ``L043_deprecated_bare_vars.py``.
"""

import re
from dataclasses import dataclass
from typing import cast

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

BARE_VAR_PATTERN = re.compile(r"\{\{\s*[\w.]+\s*\}\}")


def _find_bare_vars(text: str | None) -> list[str]:
    """Find bare variable patterns (e.g. {{ var }}) in text.

    Args:
        text: Text to search for bare variables.

    Returns:
        List of matched bare variable strings.
    """
    if not text or not isinstance(text, str):
        return []
    return BARE_VAR_PATTERN.findall(text)


def _collect_strings_from_dict(d: object, out: list[str]) -> None:
    """Recursively collect string values from dict into out list.

    Args:
        d: Dict or value to traverse.
        out: List to append string values to.
    """
    if not isinstance(d, dict):
        if isinstance(d, str):
            out.append(d)
        return
    for v in d.values():
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            _collect_strings_from_dict(v, out)
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, str):
                    out.append(item)
                elif isinstance(item, dict):
                    _collect_strings_from_dict(item, out)


def _sources_for_node(node: object) -> list[str]:
    """Gather text sources from a task/handler node for bare-var scanning.

    Args:
        node: Content graph node (task or handler).

    Returns:
        List of string fragments to scan.
    """
    sources: list[str] = []
    yaml_lines = getattr(node, "yaml_lines", "") or ""
    if yaml_lines:
        sources.append(yaml_lines)
    options = getattr(node, "options", None) or {}
    module_options = getattr(node, "module_options", None) or {}
    _collect_strings_from_dict(options, sources)
    _collect_strings_from_dict(module_options, sources)
    return sources


@dataclass
class DeprecatedBareVarsGraphRule(GraphRule):
    """Rule for deprecated bare variables (e.g. {{ foo }}); prefer explicit form.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L043"
    description: str = "Deprecated bare variables (e.g. {{ foo }}); prefer explicit form"
    enabled: bool = True
    name: str = "DeprecatedBareVars"
    version: str = "v0.0.1"
    severity: Severity = Severity.LOW
    tags: tuple[str, ...] = (Tag.VARIABLE,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task or handler nodes.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task or handler.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        return node.node_type in _TASK_TYPES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Check for deprecated bare variables and return result.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with ``bare_vars`` detail when violated, else pass.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        bare_vars: list[str] = []
        for s in _sources_for_node(node):
            bare_vars.extend(_find_bare_vars(s))
        bare_vars = list(dict.fromkeys(bare_vars))
        verdict = len(bare_vars) > 0
        detail: YAMLDict | None = None
        if bare_vars:
            detail = cast(YAMLDict, {"bare_vars": bare_vars})
        return GraphRuleResult(
            verdict=verdict,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
