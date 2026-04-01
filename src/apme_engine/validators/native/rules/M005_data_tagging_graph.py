"""GraphRule M005: registered var used in Jinja template (2.19+ trust model).

Graph-aware port of ``M005_data_tagging.py``.  Uses ``DATA_FLOW`` edges
to find registered variables flowing into this task instead of manually
iterating ``previous_tasks``.  Then checks string values in
``node.options`` and ``node.module_options`` for Jinja references to
those registered vars.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, EdgeType, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict, YAMLValue
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

_JINJA_VAR_REF = re.compile(r"\{\{\s*(\w+)")


def _registered_vars_via_data_flow(graph: ContentGraph, node_id: str) -> set[str]:
    """Collect registered variable names from DATA_FLOW predecessors.

    Args:
        graph: ContentGraph to query.
        node_id: Consumer task node id.

    Returns:
        Set of registered variable names flowing into this node.
    """
    registered: set[str] = set()
    for source_id, _attrs in graph.edges_to(node_id, EdgeType.DATA_FLOW):
        source = graph.get_node(source_id)
        if source is not None and source.register:
            registered.add(source.register)
    return registered


def _registered_vars_via_siblings(graph: ContentGraph, node_id: str) -> set[str]:
    """Collect registered var names from preceding sibling tasks.

    Falls back to sibling-based lookup when no DATA_FLOW edges exist.
    Finds all tasks in the same parent that appear before this node
    (by line number) and collects their ``register`` values.

    Args:
        graph: ContentGraph to query.
        node_id: Consumer task node id.

    Returns:
        Set of registered variable names from preceding siblings.
    """
    node = graph.get_node(node_id)
    if node is None:
        return set()

    ancestors = graph.ancestors(node_id)
    if not ancestors:
        return set()
    parent = ancestors[0]

    registered: set[str] = set()
    for child in graph.children(parent.node_id):
        if child.node_id == node_id:
            break
        if child.node_type in _TASK_TYPES and child.register:
            registered.add(child.register)
    return registered


def _string_values(mapping: object) -> list[str]:
    """Extract string values from a dict.

    Args:
        mapping: Candidate dict to extract strings from.

    Returns:
        List of string values, empty if mapping is not a dict.
    """
    if not isinstance(mapping, dict):
        return []
    return [v for v in mapping.values() if isinstance(v, str)]


@dataclass
class DataTaggingGraphRule(GraphRule):
    """Detect registered vars used in Jinja templates (2.19+ trust model).

    Uses ``DATA_FLOW`` edges (with sibling fallback) to find registered
    variables, then scans task options/module_options for ``{{ var }}``
    references to those registered names.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "M005"
    description: str = "Registered variable used in Jinja template may be untrusted in 2.19+"
    enabled: bool = True
    name: str = "DataTagging"
    version: str = "v0.0.2"
    severity: Severity = Severity.HIGH
    tags: tuple[str, ...] = (Tag.CODING,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match tasks and handlers.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True if the node is a task or handler.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        return node.node_type in _TASK_TYPES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Check for registered vars referenced in Jinja expressions.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with registered_vars detail if found.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        registered = _registered_vars_via_data_flow(graph, node_id)
        if not registered:
            registered = _registered_vars_via_siblings(graph, node_id)
        if not registered:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        all_strings = _string_values(node.options) + _string_values(node.module_options)
        flagged: set[str] = set()
        for val in all_strings:
            for m in _JINJA_VAR_REF.finditer(val):
                if m.group(1) in registered:
                    flagged.add(m.group(1))

        verdict = len(flagged) > 0
        detail: YAMLDict = {}
        if flagged:
            sorted_names = sorted(flagged)
            detail["message"] = (
                f"Registered variable(s) {', '.join(sorted_names)} used in Jinja template; may be untrusted in 2.19+"
            )
            flagged_list: list[YAMLValue] = list(sorted_names)
            detail["registered_vars"] = flagged_list

        return GraphRuleResult(
            verdict=verdict,
            detail=detail if detail else None,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
