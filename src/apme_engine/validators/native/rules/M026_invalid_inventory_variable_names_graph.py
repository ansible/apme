"""GraphRule M026: invalid inventory variable names (2.23).

Graph-aware port of ``M026_invalid_inventory_variable_names.py``.  Validates
Python identifier rules for module keys, inline vars, and all names resolved
in scope via ``VariableProvenanceResolver``.
"""

from dataclasses import dataclass
from typing import cast

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict, YAMLValue
from apme_engine.engine.variable_provenance import VariableProvenance, VariableProvenanceResolver
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})


@dataclass
class InvalidInventoryVariableNamesGraphRule(GraphRule):
    """Flag variable names that are not valid Python identifiers.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "M026"
    description: str = "Inventory variable names must be valid Python identifiers (enforced in 2.23)"
    enabled: bool = True
    name: str = "InvalidInventoryVariableNames"
    version: str = "v0.0.2"
    severity: str = Severity.MEDIUM
    tags: tuple[str, ...] = (Tag.VARIABLE,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task and handler nodes for identifier checks.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task or handler.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        return node.node_type in _TASK_TYPES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Collect invalid names from module options, inline vars, and resolved scope.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result; ``verdict`` True when any invalid name is found.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        resolver = VariableProvenanceResolver(graph)
        resolved = resolver.resolve_variables(node_id)

        by_name: dict[str, VariableProvenance | None] = {}

        def record(name: object, prov: VariableProvenance | None) -> None:
            if not isinstance(name, str) or name.isidentifier():
                return
            if name not in by_name or by_name[name] is None and prov is not None:
                by_name[name] = prov

        for vname, vprov in resolved.items():
            record(vname, vprov)
        for key in node.module_options:
            record(key, None)
        for key in node.variables:
            record(key, None)
        vars_option = node.options.get("vars")
        if isinstance(vars_option, dict):
            for key in vars_option:
                record(key, None)

        if not by_name:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        invalid_names = sorted(by_name)
        entries: list[YAMLDict] = []
        for name in invalid_names:
            prov = by_name[name]
            entry: YAMLDict = {"name": name}
            if prov is not None:
                entry["defining_node_id"] = prov.defining_node_id
                entry["defined_in_file"] = prov.file_path
                entry["line"] = prov.line
                if prov.defining_node_id != node_id:
                    entry["inherited_from"] = prov.defining_node_id
            else:
                entry["defining_node_id"] = node.node_id
                entry["defined_in_file"] = node.file_path
                entry["line"] = node.line_start
            entries.append(entry)

        detail: YAMLDict = {
            "message": f"Invalid variable name(s): {', '.join(invalid_names)}",
            "invalid_names": cast("YAMLValue", invalid_names),
            "invalid_variable_details": cast("YAMLValue", entries),
        }
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
