"""GraphRule L034: variable override ineffective due to low precedence.

Graph-aware port of ``L034_unused_override.py``.  Uses
``VariableProvenanceResolver.resolve_all_definitions()`` to collect every
variable definition across all scopes, then compares the local
definition's precedence against all others to find ineffective overrides.
"""

from __future__ import annotations

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict, YAMLValue
from apme_engine.engine.variable_provenance import (
    ProvenanceSource,
    VariableProvenanceResolver,
)
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

_PRECEDENCE_ORDER: dict[ProvenanceSource, int] = {
    ProvenanceSource.ROLE_DEFAULT: 1,
    ProvenanceSource.INVENTORY_FILE: 2,
    ProvenanceSource.VARS_FILE: 3,
    ProvenanceSource.PLAYBOOK: 4,
    ProvenanceSource.PLAY: 5,
    ProvenanceSource.BLOCK: 6,
    ProvenanceSource.ROLE_VAR: 7,
    ProvenanceSource.LOCAL: 8,
    ProvenanceSource.RUNTIME: 9,
    ProvenanceSource.EXTERNAL: 0,
}

_SET_FACT_FQCNS = frozenset(
    {
        "set_fact",
        "ansible.builtin.set_fact",
        "ansible.legacy.set_fact",
    }
)


def _locally_defined_vars(graph: ContentGraph, node_id: str) -> list[tuple[str, ProvenanceSource]]:
    """Collect variable names this node defines, with their provenance source.

    ``register`` and ``set_fact`` produce ``RUNTIME`` bindings;
    ``node.variables`` (task-level ``vars:``) produce ``LOCAL`` bindings.

    Args:
        graph: ContentGraph to query.
        node_id: Node to inspect.

    Returns:
        List of ``(variable_name, source)`` pairs.
    """
    node = graph.get_node(node_id)
    if node is None:
        return []
    result: list[tuple[str, ProvenanceSource]] = []
    for name in node.variables:
        result.append((name, ProvenanceSource.LOCAL))
    if node.register:
        result.append((node.register, ProvenanceSource.RUNTIME))
    for k in node.set_facts:
        if k != "cacheable":
            result.append((k, ProvenanceSource.RUNTIME))
    return result


def _defines_variables(graph: ContentGraph, node_id: str) -> bool:
    """Return True if the node defines variables.

    Checks ``node.variables``, ``register``, and ``set_fact``.

    Args:
        graph: ContentGraph to query.
        node_id: Node to inspect.

    Returns:
        True if the node defines any variables.
    """
    node = graph.get_node(node_id)
    if node is None:
        return False
    if node.variables:
        return True
    if node.register:
        return True
    if node.module in _SET_FACT_FQCNS:
        return True
    return bool(node.set_facts)


@dataclass
class UnusedOverrideGraphRule(GraphRule):
    """Detect variable overrides that are ineffective due to precedence.

    Uses ``VariableProvenanceResolver.resolve_all_definitions()`` to get
    every definition of each variable, then compares the local definition's
    precedence against all others.  If any other definition has higher
    precedence, the local override is flagged as ineffective.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L034"
    description: str = "A variable is not successfully re-defined because of low precedence"
    enabled: bool = True
    name: str = "UnusedOverride"
    version: str = "v0.0.2"
    severity: str = Severity.VERY_LOW
    tags: tuple[str, ...] = (Tag.VARIABLE,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match tasks that define variables.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True if the node is a task/handler that defines variables.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        if node.node_type not in _TASK_TYPES:
            return False
        return _defines_variables(graph, node_id)

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Check for ineffective overrides via precedence comparison.

        Uses ``resolve_all_definitions()`` to collect every definition of
        each locally defined variable, then checks whether any definition
        from a *different* node has higher precedence than the local one.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with variables detail if overrides are ineffective.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        local_vars = _locally_defined_vars(graph, node_id)
        if not local_vars:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        resolver = VariableProvenanceResolver(graph)
        all_defs = resolver.resolve_all_definitions(node_id)

        ineffective: list[YAMLValue] = []

        for var_name, local_source in local_vars:
            local_prec = _PRECEDENCE_ORDER.get(local_source, 0)
            defs = all_defs.get(var_name, [])
            for prov in defs:
                if prov.defining_node_id == node_id:
                    continue
                existing_prec = _PRECEDENCE_ORDER.get(prov.source, 0)
                if existing_prec > local_prec:
                    entry: YAMLDict = {
                        "name": var_name,
                        "local_precedence": local_source.value,
                        "shadowed_by": prov.source.value,
                        "shadowed_by_node": prov.defining_node_id,
                    }
                    ineffective.append(entry)
                    break

        verdict = len(ineffective) > 0
        detail: YAMLDict = {"variables": ineffective}

        return GraphRuleResult(
            verdict=verdict,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
