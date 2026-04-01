"""GraphRule L090: plugin entry files should be small."""

from __future__ import annotations

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

MAX_PLUGIN_LINES = 500


@dataclass
class PluginFileSizeGraphRule(GraphRule):
    """Flag plugin entry files that exceed the recommended line count.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L090"
    description: str = "Plugin entry files should be small; move helpers to module_utils"
    enabled: bool = True
    name: str = "PluginFileSize"
    version: str = "v0.0.1"
    severity: Severity = Severity.LOW
    tags: tuple[str, ...] = (Tag.QUALITY,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match MODULE nodes with a known line count.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a MODULE with ``module_line_count > 0``.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type != NodeType.MODULE:
            return False
        return node.module_line_count > 0

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report a violation when the plugin file exceeds the line threshold.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            ``GraphRuleResult`` with ``verdict`` True when the file exceeds
            the threshold, ``verdict`` False when within limits, or None if
            the node is not applicable.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type != NodeType.MODULE:
            return None
        lines = node.module_line_count
        if lines <= 0:
            return None
        if lines <= MAX_PLUGIN_LINES:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        detail: YAMLDict = {
            "message": f"plugin file has {lines} lines (max {MAX_PLUGIN_LINES})",
            "line_count": lines,
            "max_lines": MAX_PLUGIN_LINES,
        }
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
