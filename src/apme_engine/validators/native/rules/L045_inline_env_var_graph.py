"""GraphRule L045: inline task environment variables.

Fires **once** on the scope that defines ``environment:`` (play, block,
or task) instead of on every inheriting child.  When the defining scope
is a play or block, ``affected_children`` reports how many descendants
inherit the inline environment.
"""

from dataclasses import dataclass
from typing import TypeGuard

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_ENV_SCOPES = frozenset({NodeType.PLAY, NodeType.BLOCK, NodeType.TASK, NodeType.HANDLER})


def _environment_truthy(value: object) -> TypeGuard[YAMLDict]:
    """Return True when *value* is a non-empty environment mapping.

    Args:
        value: Candidate environment value from a node or property origin.

    Returns:
        True if ``value`` is a dict with at least one entry.
    """
    return isinstance(value, dict) and bool(value)


@dataclass
class InlineEnvVarGraphRule(GraphRule):
    """Discourage inline ``environment`` at its defining scope.

    Fires once on the play, block, or task that declares ``environment:``
    instead of on every child that inherits it.  When the defining scope
    is a play or block, ``affected_children`` counts descendants.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L045"
    description: str = "Avoid inline environment variables in tasks; use env file or role vars"
    enabled: bool = True
    name: str = "InlineEnvVar"
    version: str = "v0.0.3"
    severity: str = Severity.VERY_LOW
    tags: tuple[str, ...] = (Tag.CODING,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match nodes that **locally define** a non-empty ``environment``.

        Nodes that only inherit environment from an ancestor are not
        matched; the ancestor will be matched instead.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when ``environment`` is set on this node (not inherited).
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        if node.node_type not in _ENV_SCOPES:
            return False
        return _environment_truthy(node.environment)

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report inline environment at the defining scope.

        For plays and blocks, counts descendant tasks/handlers that
        inherit the environment and includes the count as
        ``affected_children`` in the violation detail.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with environment detail and affected count.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        scope_label = {
            NodeType.PLAY: "play",
            NodeType.BLOCK: "block",
            NodeType.HANDLER: "handler",
        }.get(node.node_type, "task")

        detail: YAMLDict = {
            "message": f"{scope_label} defines inline environment; consider env file or variables",
            "environment": dict(node.environment) if node.environment else {},
            "scope": scope_label,
        }

        if node.node_type in (NodeType.PLAY, NodeType.BLOCK):
            child_count = 0
            for desc_id in graph.structural_descendants(node_id):
                desc = graph.get_node(desc_id)
                if desc is None:
                    continue
                if desc.node_type not in (NodeType.TASK, NodeType.HANDLER):
                    continue
                if _environment_truthy(desc.environment):
                    continue
                child_count += 1
            if child_count > 0:
                detail["affected_children"] = child_count

        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
