"""GraphRule L052: galaxy version in meta should follow semantic version format."""

import re
from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

GALAXY_VERSION_PATTERN = re.compile(r"^\d+\.\d+(\.\d+)?$")


@dataclass
class GalaxyVersionIncorrectGraphRule(GraphRule):
    """Flag ``galaxy_info.version`` values that are not ``x.y`` or ``x.y.z``.

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

    rule_id: str = "L052"
    description: str = "Galaxy version in meta should follow semantic version format (x.y.z)"
    enabled: bool = True
    name: str = "GalaxyVersionIncorrect"
    version: str = "v0.0.1"
    severity: str = Severity.LOW
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
        """Validate ``galaxy_info.version`` against the semantic version pattern.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with ``version`` detail when the value is invalid, else pass.
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
        version = galaxy_info.get("version")
        if version is None:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        vs = str(version).strip()
        if not GALAXY_VERSION_PATTERN.match(vs):
            detail: YAMLDict = {
                "version": vs,
                "message": "Galaxy version in meta should follow semantic version format (x.y.z)",
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
