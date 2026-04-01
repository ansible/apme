"""GraphRule L040: YAML should not contain tabs; use spaces."""

from dataclasses import dataclass
from typing import cast

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})


@dataclass
class NoTabsGraphRule(GraphRule):
    """Detect tab characters in raw task YAML (prefer spaces).

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L040"
    description: str = "YAML should not contain tabs; use spaces"
    enabled: bool = True
    name: str = "NoTabs"
    version: str = "v0.0.1"
    severity: Severity = Severity.INFO
    tags: tuple[str, ...] = (Tag.DEPENDENCY,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task or handler nodes that have raw YAML text.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task or handler with non-empty ``yaml_lines``.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return False
        return bool(node.yaml_lines)

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Flag lines that contain tab characters.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result with ``lines_with_tabs`` when violated, else pass.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        yaml_lines = node.yaml_lines or ""
        lines_with_tabs: list[int] = []
        for i, line in enumerate(yaml_lines.splitlines(), start=1):
            if "\t" in line:
                lines_with_tabs.append(i)
        if not lines_with_tabs:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        detail = cast(YAMLDict, {"lines_with_tabs": lines_with_tabs})
        return GraphRuleResult(
            verdict=True,
            node_id=node_id,
            file=(node.file_path, node.line_start),
            detail=detail,
        )
