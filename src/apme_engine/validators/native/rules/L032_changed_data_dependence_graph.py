"""GraphRule L032: variable re-definition detection.

Graph-aware port of ``L032_changed_data_dependence.py``.  Uses
``VariableProvenanceResolver`` to detect variables defined in multiple
scopes, replacing the flat ``variable_set`` with provenance-aware
multi-definition detection.
"""

from __future__ import annotations

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict, YAMLValue
from apme_engine.engine.variable_provenance import VariableProvenanceResolver
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

_SET_FACT_FQCNS = frozenset(
    {
        "set_fact",
        "ansible.builtin.set_fact",
        "ansible.legacy.set_fact",
    }
)


def _defines_variables(graph: ContentGraph, node_id: str) -> bool:
    """Return True if the node defines variables via set_fact or register.

    Args:
        graph: ContentGraph to query.
        node_id: Node to inspect.

    Returns:
        True if the node registers output or uses set_fact.
    """
    node = graph.get_node(node_id)
    if node is None:
        return False
    if node.register:
        return True
    if node.module in _SET_FACT_FQCNS:
        return True
    return bool(node.set_facts)


def _locally_defined_var_names(graph: ContentGraph, node_id: str) -> list[str]:
    """Collect variable names this node defines.

    Args:
        graph: ContentGraph to query.
        node_id: Node to inspect.

    Returns:
        List of variable names defined by this node.
    """
    node = graph.get_node(node_id)
    if node is None:
        return []
    names: list[str] = []
    if node.register:
        names.append(node.register)
    names.extend(k for k in node.set_facts if k != "cacheable")
    return names


@dataclass
class ChangedDataDependenceGraphRule(GraphRule):
    """Detect variable re-definitions across scopes.

    Uses ``VariableProvenanceResolver`` to check whether variables this
    task defines already exist at a different scope (play vars, role
    defaults, etc.).  This replaces the old ``variable_set`` multi-def
    approach with provenance-aware detection.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L032"
    description: str = "A variable is re-defined"
    enabled: bool = True
    name: str = "ChangedDataDependence"
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
        """Check for re-defined variables via provenance.

        Resolves all variables in scope and checks whether any locally
        defined names also appear from a different scope.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with variables list if re-definitions found.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        local_names = _locally_defined_var_names(graph, node_id)
        if not local_names:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        resolver = VariableProvenanceResolver(graph)
        all_vars = resolver.resolve_variables(node_id)

        redefined: list[YAMLValue] = []
        for var_name in local_names:
            prov = all_vars.get(var_name)
            if prov is not None and prov.defining_node_id != node_id:
                entry: YAMLDict = {
                    "name": var_name,
                    "also_defined_at": prov.defining_node_id,
                    "source": prov.source.value,
                }
                redefined.append(entry)

        verdict = len(redefined) > 0
        detail: YAMLDict = {"variables": redefined}

        return GraphRuleResult(
            verdict=verdict,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
