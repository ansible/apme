"""GraphRule L073: YAML should use 2-space indentation."""

from dataclasses import dataclass
from typing import cast

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

EXPECTED_INDENT = 2


@dataclass
class IndentationGraphRule(GraphRule):
    """Detect leading space indentation that is not a multiple of two.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L073"
    description: str = "YAML should use 2-space indentation"
    enabled: bool = True
    name: str = "Indentation"
    version: str = "v0.0.1"
    severity: Severity = Severity.LOW
    tags: tuple[str, ...] = (Tag.CODING,)

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
        """Flag non-empty, non-comment lines whose indent is not a multiple of 2.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result with ``bad_indent_lines`` when violated, else pass.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        yaml_lines = node.yaml_lines or ""
        bad_lines: list[int] = []
        for i, line in enumerate(yaml_lines.splitlines(), start=1):
            stripped = line.lstrip(" ")
            if stripped == "" or stripped.startswith("#"):
                continue
            indent = len(line) - len(stripped)
            if indent > 0 and indent % EXPECTED_INDENT != 0:
                bad_lines.append(i)
        if not bad_lines:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        detail = cast(
            YAMLDict,
            {
                "bad_indent_lines": bad_lines,
                "expected_indent": EXPECTED_INDENT,
                "message": "indentation should be a multiple of 2 spaces",
            },
        )
        return GraphRuleResult(
            verdict=True,
            node_id=node_id,
            file=(node.file_path, node.line_start),
            detail=detail,
        )
