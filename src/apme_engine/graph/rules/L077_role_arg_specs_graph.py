"""GraphRule L077: Roles should declare argument_specs for fail-fast validation."""

from dataclasses import dataclass
from typing import cast

from apme_engine.graph.argument_specs import get_argument_specs_from_metadata, load_standalone_argument_specs
from apme_engine.graph.content_graph import ContentGraph, NodeType
from apme_engine.graph.rule_base import GraphRule, GraphRuleResult
from apme_engine.graph.types import RuleScope, Severity, YAMLDict
from apme_engine.graph.types import RuleTag as Tag


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
    severity: Severity = Severity.LOW
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

        Falls back to parsing standalone ``meta/argument_specs.yml`` (or
        ``.yaml``) when inline metadata is absent. Malformed standalone
        content does not satisfy the rule.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with guidance when ``argument_specs`` is absent or empty.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        specs = get_argument_specs_from_metadata(node.role_metadata)
        has_arg_specs = bool(specs)
        if not has_arg_specs and node.file_path:
            standalone_specs = load_standalone_argument_specs(node.file_path)
            has_arg_specs = bool(standalone_specs)
        verdict = not has_arg_specs
        if verdict:
            return GraphRuleResult(
                verdict=True,
                detail=cast(
                    YAMLDict,
                    {
                        "message": (
                            "role should have argument_specs in meta/main.yml or a standalone meta/argument_specs.yml"
                        ),
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
