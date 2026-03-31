"""GraphRule L054: role meta ``galaxy_info`` should include ``galaxy_tags``."""

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult


@dataclass
class MetaNoTagsGraphRule(GraphRule):
    """Flag ``galaxy_info`` that lacks non-empty ``galaxy_tags`` and ``categories``.

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

    rule_id: str = "L054"
    description: str = "Role meta galaxy_info should include galaxy_tags"
    enabled: bool = True
    name: str = "MetaNoTags"
    version: str = "v0.0.1"
    severity: str = Severity.VERY_LOW
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
        """Report when ``galaxy_tags`` and ``categories`` are both missing or empty.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with detail when tags are missing, else pass.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        rm = node.role_metadata
        if not isinstance(rm, dict):
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        galaxy_info = rm.get("galaxy_info")
        if not isinstance(galaxy_info, dict):
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        tags = galaxy_info.get("galaxy_tags")
        categories = galaxy_info.get("categories")
        has_tags = isinstance(tags, list) and len(tags) > 0
        has_categories = isinstance(categories, list) and len(categories) > 0
        if not (has_tags or has_categories):
            detail: YAMLDict = {
                "message": "Role meta galaxy_info should include galaxy_tags or categories",
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
