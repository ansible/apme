"""GraphRule R108: detect privilege escalation via ContentGraph.

Graph-aware port of ``R108_privilege_escalation.py``.  Uses
``VariableProvenanceResolver.resolve_property_origins`` to attribute
``become`` to its defining scope (play or role) instead of flagging
every inheriting task.
"""

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleScope, Severity, YAMLDict
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.variable_provenance import VariableProvenanceResolver
from apme_engine.validators.native.rules.graph_rule_base import (
    GraphRule,
    GraphRuleResult,
)

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})


@dataclass
class PrivilegeEscalationGraphRule(GraphRule):
    """Detect privilege escalation (become) with scope attribution.

    In the old pipeline R108 fired on every task that inherited
    ``become: true`` from a play, producing redundant violations.
    This version uses ``PropertyOrigin`` to attribute ``become`` to its
    defining scope, whether the task declares it explicitly or inherits
    it from a play/role.

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
    version: str = "v0.0.2"
    severity: str = Severity.HIGH
    tags: tuple[str, ...] = (Tag.SYSTEM,)
    scope: str = RuleScope.PLAY

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match tasks/handlers with ``become`` set (explicit or inherited).

        Checks the node's own ``become`` first, then falls back to
        ``PropertyOrigin`` to detect inherited become from an ancestor.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True if the node is a task/handler with effective become.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        if node.node_type not in _TASK_TYPES:
            return False
        if node.become is not None and bool(node.become.get("become")):
            return True
        resolver = VariableProvenanceResolver(graph)
        origins = resolver.resolve_property_origins(node_id)
        become_origin = origins.get("become")
        if become_origin is None:
            return False
        val = become_origin.value
        if isinstance(val, dict):
            return bool(val.get("become"))
        return bool(val)

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Check for privilege escalation and attribute to defining scope.

        Uses ``VariableProvenanceResolver`` to find which scope actually
        sets ``become``.  If the defining scope is an ancestor (not the
        task itself), the violation detail indicates inheritance.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with become detail and attribution.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        resolver = VariableProvenanceResolver(graph)
        origins = resolver.resolve_property_origins(node_id)
        become_origin = origins.get("become")

        detail: YAMLDict = {}
        if node.become:
            detail.update(node.become)
        elif become_origin is not None:
            if isinstance(become_origin.value, dict):
                detail.update(become_origin.value)
            else:
                detail["become"] = become_origin.value

        if become_origin is not None and become_origin.defining_node_id != node_id:
            detail["inherited_from"] = become_origin.defining_node_id
            detail["defined_in_file"] = become_origin.file_path

        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
