"""GraphRule R117: detect external role usage.

Graph-aware port of ``R117_external_role.py``.  Matches ``ROLE`` nodes
and checks for ``galaxy_info`` in ``node.role_metadata``.
The old ``ctx.is_begin(role)`` guard is replaced by checking whether the
role node has an incoming ``DEPENDENCY`` edge from a play — a root-level
role being scanned directly (rather than referenced from a playbook)
is not flagged.
"""

from __future__ import annotations

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, EdgeType, NodeType
from apme_engine.engine.models import RuleScope, Severity, YAMLDict
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult


def _has_galaxy_info(graph: ContentGraph, node_id: str) -> bool:
    """Check whether a role node carries ``galaxy_info`` metadata.

    Galaxy-style roles have a ``meta/main.yml`` with ``galaxy_info``.
    The graph builder stores this in ``node.role_metadata``.

    Args:
        graph: ContentGraph to query.
        node_id: Role node to inspect.

    Returns:
        True if ``galaxy_info`` is present in role_metadata.
    """
    node = graph.get_node(node_id)
    if node is None:
        return False
    return bool(node.role_metadata.get("galaxy_info"))


def _has_play_dependency(graph: ContentGraph, node_id: str) -> bool:
    """Return True if a play references this role via a DEPENDENCY edge.

    Play→role edges use ``EdgeType.DEPENDENCY`` (not CONTAINS), so
    ``graph.ancestors()`` won't find the play.  Instead we check
    incoming DEPENDENCY edges for a source node of type PLAY.

    Args:
        graph: ContentGraph to query.
        node_id: Role node to inspect.

    Returns:
        True if any incoming DEPENDENCY edge originates from a PLAY node.
    """
    for source_id, _attrs in graph.edges_to(node_id, EdgeType.DEPENDENCY):
        source = graph.get_node(source_id)
        if source is not None and source.node_type == NodeType.PLAY:
            return True
    return False


@dataclass
class ExternalRoleGraphRule(GraphRule):
    """Detect external role usage via graph metadata.

    Matches ``ROLE`` nodes with ``galaxy_info`` in ``role_metadata``.
    Replaces the ``ctx.is_begin`` + ``RoleCall`` + ``spec.metadata``
    pattern with a graph-native check using DEPENDENCY edges.

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

    rule_id: str = "R117"
    description: str = "An external role is used"
    enabled: bool = True
    name: str = "ExternalRole"
    version: str = "v0.0.2"
    severity: Severity = Severity.INFO
    tags: tuple[str, ...] = (Tag.DEPENDENCY,)
    scope: str = RuleScope.ROLE

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match role nodes with galaxy_info that have a play dependency.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True if the node is a role with galaxy metadata referenced by a play.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        if node.node_type != NodeType.ROLE:
            return False
        if not _has_play_dependency(graph, node_id):
            return False
        return _has_galaxy_info(graph, node_id)

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report external role usage.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult indicating external role found.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        detail: YAMLDict = {}
        if node.role_fqcn:
            detail["role_fqcn"] = node.role_fqcn
        if node.name:
            detail["role_name"] = node.name

        return GraphRuleResult(
            verdict=True,
            detail=detail if detail else None,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
