"""GraphRule L060: line too long."""

from dataclasses import dataclass
from typing import cast

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

DEFAULT_MAX_LINE_LENGTH = 160


@dataclass
class LineLengthGraphRule(GraphRule):
    """Detect YAML lines longer than the configured maximum.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L060"
    description: str = "Line too long"
    enabled: bool = True
    name: str = "LineLength"
    version: str = "v0.0.1"
    severity: str = Severity.VERY_LOW
    tags: tuple[str, ...] = (Tag.QUALITY,)

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
        """Flag lines that exceed ``DEFAULT_MAX_LINE_LENGTH`` characters.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result with ``long_lines`` when violated, else pass.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        yaml_lines = node.yaml_lines or ""
        long_lines: list[dict[str, int]] = []
        for i, line in enumerate(yaml_lines.splitlines(), start=1):
            if len(line) > DEFAULT_MAX_LINE_LENGTH:
                long_lines.append({"line": i, "length": len(line)})
        if not long_lines:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        detail = cast(
            YAMLDict,
            {
                "long_lines": long_lines,
                "max_length": DEFAULT_MAX_LINE_LENGTH,
                "message": f"line too long (>{DEFAULT_MAX_LINE_LENGTH} characters)",
            },
        )
        return GraphRuleResult(
            verdict=True,
            node_id=node_id,
            file=(node.file_path, node.line_start),
            detail=detail,
        )
