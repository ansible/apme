"""GraphRule L027: detect roles used without metadata."""

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult


@dataclass
class RoleWithoutMetadataGraphRule(GraphRule):
    """Flag ROLE nodes whose ``role_metadata`` is empty or missing.

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

    rule_id: str = "L027"
    description: str = "A role without metadata is used"
    enabled: bool = True
    name: str = "RoleWithoutMetadata"
    version: str = "v0.0.1"
    severity: Severity = Severity.LOW
    tags: tuple[str, ...] = (Tag.DEPENDENCY,)
    precedence: int = 10

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match ROLE nodes only.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True if the node is a ROLE.
        """
        node = graph.get_node(node_id)
        return node is not None and node.node_type == NodeType.ROLE

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report when ``role_metadata`` is empty.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with detail when metadata is absent or empty, else pass.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        verdict = not bool(node.role_metadata)
        if verdict:
            detail: YAMLDict = {
                "message": "A role without metadata is used",
            }
            return GraphRuleResult(
                verdict=True,
                detail=detail,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        return GraphRuleResult(
            verdict=False,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
