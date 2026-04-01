"""GraphRule R108: detect privilege escalation via ContentGraph.

Fires **once** on the scope that defines ``become: true`` (play, block,
or task) instead of on every inheriting child.  When the defining scope
is a play or block, ``affected_children`` reports how many descendants
inherit the privilege escalation.
"""

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleScope, Severity, YAMLDict
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.validators.native.rules.graph_rule_base import (
    GraphRule,
    GraphRuleResult,
)

_BECOME_SCOPES = frozenset({NodeType.PLAY, NodeType.BLOCK, NodeType.TASK, NodeType.HANDLER})


def _become_enabled(become: YAMLDict | None) -> bool:
    """Return True when *become* dict indicates privilege escalation.

    Args:
        become: Become mapping from a ContentNode (may be None).

    Returns:
        True if become is explicitly enabled.
    """
    if become is None:
        return False
    return bool(become.get("enabled", become.get("become")))


@dataclass
class PrivilegeEscalationGraphRule(GraphRule):
    """Detect privilege escalation (become) at its defining scope.

    Fires once on the play, block, or task that declares ``become: true``.
    Tasks that only inherit become from an ancestor are skipped — the
    violation is reported at the defining scope with an
    ``affected_children`` count.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
        scope: Structural scope.
    """

    rule_id: str = "R108"
    description: str = "Privilege escalation is found"
    enabled: bool = True
    name: str = "PrivilegeEscalation"
    version: str = "v0.0.3"
    severity: str = Severity.HIGH
    tags: tuple[str, ...] = (Tag.SYSTEM,)
    scope: str = RuleScope.TASK

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match nodes that **locally define** ``become: true``.

        Nodes that only inherit become from an ancestor are not matched;
        the ancestor will be matched instead.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True if become is defined on this node (not inherited).
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        if node.node_type not in _BECOME_SCOPES:
            return False
        return _become_enabled(node.become)

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report privilege escalation at the defining scope.

        For plays and blocks, counts descendant tasks/handlers that
        inherit the privilege escalation and includes the count as
        ``affected_children`` in the violation detail.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with become detail and affected count.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        detail: YAMLDict = {}
        if node.become:
            detail.update(node.become)

        if node.node_type in (NodeType.PLAY, NodeType.BLOCK):
            child_count = 0
            for desc_id in graph.structural_descendants(node_id):
                desc = graph.get_node(desc_id)
                if desc is None:
                    continue
                if desc.node_type not in (NodeType.TASK, NodeType.HANDLER):
                    continue
                if desc.become is not None:
                    continue
                child_count += 1
            if child_count > 0:
                detail["affected_children"] = child_count

        scope_label = {
            NodeType.PLAY: "play",
            NodeType.BLOCK: "block",
            NodeType.HANDLER: "handler",
        }.get(node.node_type, "task")
        detail["scope"] = scope_label

        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
