"""GraphRule L074: role names should not contain dashes.

Graph-aware port of ``L074_no_dashes_in_role_name.py``. Evaluates ROLE
nodes in the ContentGraph for collection compatibility.
"""

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleScope, Severity, YAMLDict
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult


@dataclass
class NoDashesInRoleNameGraphRule(GraphRule):
    """Detect dashes in role names (incompatible with collections).

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

    rule_id: str = "L074"
    description: str = "Role names should not contain dashes"
    enabled: bool = True
    name: str = "NoDashesInRoleName"
    version: str = "v0.0.1"
    severity: Severity = Severity.LOW
    tags: tuple[str, ...] = (Tag.DEPENDENCY,)
    scope: str = RuleScope.ROLE
    precedence: int = 10

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match ROLE nodes only (not TASK).

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True if the node is a ROLE.
        """
        node = graph.get_node(node_id)
        return node is not None and node.node_type == NodeType.ROLE

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Flag role names that contain a hyphen.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with ``role_name`` detail when violated, else pass.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        role_name = (node.role_fqcn or "").strip() or (node.name or "").strip()
        verdict = "-" in role_name
        detail: YAMLDict = {}
        if verdict:
            detail["role_name"] = role_name
            detail["message"] = "role names with dashes cause collection compatibility issues"
        return GraphRuleResult(
            verdict=verdict,
            detail=detail if verdict else None,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
