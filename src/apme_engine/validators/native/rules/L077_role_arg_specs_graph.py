"""GraphRule L077: Roles should declare argument_specs for fail-fast validation."""

from dataclasses import dataclass
from typing import cast

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleScope, Severity, YAMLDict
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult


@dataclass
class RoleArgSpecsGraphRule(GraphRule):
    """Require ``argument_specs`` in role metadata for parameter validation.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
        scope: Structural scope.
        precedence: Evaluation order (lower = earlier).
    """

    rule_id: str = "L077"
    description: str = "Roles should have meta/argument_specs.yml for fail-fast parameter validation"
    enabled: bool = True
    name: str = "RoleArgSpecs"
    version: str = "v0.0.1"
    severity: str = Severity.LOW
    tags: tuple[str, ...] = (Tag.QUALITY,)
    scope: str = RuleScope.ROLE
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
        """Flag roles missing ``argument_specs`` in metadata.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with guidance when ``argument_specs`` is absent or empty.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        argument_specs = node.role_metadata.get("argument_specs")
        verdict = not bool(argument_specs)
        if verdict:
            return GraphRuleResult(
                verdict=True,
                detail=cast(
                    YAMLDict,
                    {
                        "message": "role should have meta/argument_specs.yml for fail-fast validation",
                    },
                ),
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        return GraphRuleResult(
            verdict=False,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
