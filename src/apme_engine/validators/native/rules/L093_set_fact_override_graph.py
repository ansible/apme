"""GraphRule L093: set_fact overriding role defaults/vars names.

Graph-aware port of ``L093_set_fact_override.py``.  Walks graph ancestry
to find the enclosing role node and compare ``set_fact`` keys against
``role.default_variables`` and ``role.role_variables``, replacing the
``hasattr(ctx, "role_defaults")`` pattern with structural graph queries.
"""

from __future__ import annotations

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict, YAMLValue
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

_SET_FACT_FQCNS = frozenset(
    {
        "set_fact",
        "ansible.builtin.set_fact",
        "ansible.legacy.set_fact",
    }
)


def _is_set_fact(graph: ContentGraph, node_id: str) -> bool:
    """Return True if the node is a set_fact task.

    Args:
        graph: ContentGraph to query.
        node_id: Node to inspect.

    Returns:
        True if the module is set_fact (any FQCN form).
    """
    node = graph.get_node(node_id)
    if node is None:
        return False
    return node.module in _SET_FACT_FQCNS


def _find_enclosing_role(graph: ContentGraph, node_id: str) -> str | None:
    """Walk ancestors to find the enclosing role node.

    Args:
        graph: ContentGraph to query.
        node_id: Starting node whose ancestry is walked.

    Returns:
        Node ID of the nearest ROLE ancestor, or None.
    """
    for anc in graph.ancestors(node_id):
        if anc.node_type == NodeType.ROLE:
            return anc.node_id
    return None


@dataclass
class SetFactOverrideGraphRule(GraphRule):
    """Detect set_fact that overrides role default/var names.

    Walks the graph ancestry to find the enclosing role and compares
    ``set_fact`` keys against ``role.default_variables`` and
    ``role.role_variables``.  This replaces the ad-hoc ``hasattr(ctx, ...)``
    pattern from the old pipeline.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L093"
    description: str = "Do not override role defaults/vars with set_fact; use a different name"
    enabled: bool = True
    name: str = "SetFactOverride"
    version: str = "v0.0.2"
    severity: str = Severity.LOW
    tags: tuple[str, ...] = (Tag.VARIABLE,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match set_fact tasks within a role.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True if the node is a set_fact task inside a role.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        if node.node_type not in _TASK_TYPES:
            return False
        if not _is_set_fact(graph, node_id):
            return False
        return _find_enclosing_role(graph, node_id) is not None

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Check for set_fact keys that shadow role defaults/vars.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with overridden_vars detail if found.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        role_id = _find_enclosing_role(graph, node_id)
        if role_id is None:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        role_node = graph.get_node(role_id)
        if role_node is None:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        known_vars = set(role_node.default_variables) | set(role_node.role_variables)
        if not known_vars:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        fact_keys = (set(node.set_facts) | set(node.module_options)) - {"cacheable"}
        overridden: list[YAMLValue] = list(sorted(fact_keys & known_vars))
        verdict = len(overridden) > 0

        detail: YAMLDict = {}
        if overridden:
            detail["overridden_vars"] = overridden
            detail["role"] = role_id
            detail["message"] = "do not override role defaults/vars with set_fact; use a different name"

        return GraphRuleResult(
            verdict=verdict,
            detail=detail if detail else None,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
