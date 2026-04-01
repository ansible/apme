"""GraphRule L089: plugin Python files should include return type hints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult


@dataclass
class PluginTypeHintsGraphRule(GraphRule):
    """Flag plugin Python files with functions missing return type hints.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L089"
    description: str = "Plugin Python files should include type hints"
    enabled: bool = True
    name: str = "PluginTypeHints"
    version: str = "v0.0.1"
    severity: Severity = Severity.LOW
    tags: tuple[str, ...] = (Tag.QUALITY,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match MODULE nodes that have functions without return type annotations.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a MODULE with at least one function
            missing a return type annotation.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type != NodeType.MODULE:
            return False
        return bool(node.module_functions_without_return_type)

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report functions missing return type hints.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            ``GraphRuleResult`` with ``verdict`` True when functions lack
            return type annotations, ``verdict`` False when all have them,
            or None if the node is not applicable.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type != NodeType.MODULE:
            return None
        missing = node.module_functions_without_return_type
        if not missing:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        detail = cast(
            YAMLDict,
            {
                "message": f"{len(missing)} function(s) missing return type hints",
                "functions": missing,
            },
        )
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
