"""GraphRule L038: Detect unresolved role references for include/import role tasks."""

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, EdgeType, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

ROLE_MODULES = frozenset(
    {
        "include_role",
        "import_role",
        "ansible.builtin.include_role",
        "ansible.builtin.import_role",
        "ansible.legacy.include_role",
        "ansible.legacy.import_role",
    }
)


@dataclass
class UnresolvedRoleGraphRule(GraphRule):
    """Flag include_role or import_role tasks with no resolved role edge.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L038"
    description: str = "Unresolved role is found"
    enabled: bool = True
    name: str = "UnresolvedRole"
    version: str = "v0.0.1"
    severity: str = Severity.LOW
    tags: tuple[str, ...] = (Tag.DEPENDENCY,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task or handler nodes that invoke include_role or import_role.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task or handler whose resolved or raw module
            name is a known include or import role action.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return False
        resolved = node.module
        return resolved in ROLE_MODULES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report a violation when no include or import edge targets a role.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result with ``verdict`` True when no outgoing include or
            import edge exists, ``verdict`` False when an edge exists, or None if
            the node is missing.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        mo = node.module_options
        if not isinstance(mo, dict):
            mo = {}
        role_name = mo.get("name", "")
        role_str = role_name if isinstance(role_name, str) else str(role_name)
        has_edge = bool(graph.edges_from(node_id, EdgeType.INCLUDE)) or bool(graph.edges_from(node_id, EdgeType.IMPORT))
        if has_edge:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        detail: YAMLDict = {"role": role_str}
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
