"""GraphRule L092: avoid loop variable references in task names.

Graph-aware port of ``L092_loop_var_in_name.py``. Searches task and
handler ``name`` fields for ``{{ item``-style patterns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_LOOP_VAR_IN_NAME = re.compile(r"\{\{\s*item\b")
_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})


@dataclass
class LoopVarInNameGraphRule(GraphRule):
    """Detect ``{{ item }}``-style references in task names.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
        precedence: Evaluation order (lower = earlier).
    """

    rule_id: str = "L092"
    description: str = "Avoid loop variable references in task names"
    enabled: bool = True
    name: str = "LoopVarInName"
    version: str = "v0.0.1"
    severity: Severity = Severity.LOW
    tags: tuple[str, ...] = (Tag.CODING,)
    precedence: int = 10

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match TASK and HANDLER nodes that declare a name.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True if the node is a named task or handler.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return False
        return bool(node.name)

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Flag names that reference the default loop variable.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with ``task_name`` detail when violated, else pass.
        """
        node = graph.get_node(node_id)
        if node is None or not node.name:
            return None
        verdict = bool(_LOOP_VAR_IN_NAME.search(node.name))
        detail: YAMLDict = {}
        if verdict:
            detail["task_name"] = node.name
            detail["message"] = "avoid loop variable references ({{ item }}) in task names"
        return GraphRuleResult(
            verdict=verdict,
            detail=detail if verdict else None,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
