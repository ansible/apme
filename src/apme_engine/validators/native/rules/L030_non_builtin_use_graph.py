"""GraphRule L030: tasks using non-builtin modules.

Graph-aware port of ``L030_non_builtin_use.py``.
"""

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})


@dataclass
class NonBuiltinUseGraphRule(GraphRule):
    """Flag tasks whose resolved module is outside ``ansible.builtin``.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L030"
    description: str = "Non-builtin module is used"
    enabled: bool = True
    name: str = "NonBuiltinUse"
    version: str = "v0.0.2"
    severity: str = Severity.VERY_LOW
    tags: tuple[str, ...] = (Tag.DEPENDENCY,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task/handler nodes with a resolved non-builtin module FQCN.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task or handler and ``resolved_module_name``
            is set and does not start with ``ansible.builtin.``.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        if node.node_type not in _TASK_TYPES:
            return False
        resolved = node.resolved_module_name
        return bool(resolved and not resolved.startswith("ansible.builtin."))

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report the resolved FQCN for non-builtin module usage.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with ``verdict`` True when the violation applies.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        resolved = node.resolved_module_name
        verdict = bool(resolved and not resolved.startswith("ansible.builtin."))
        detail: YAMLDict = {"fqcn": resolved}
        return GraphRuleResult(
            verdict=verdict,
            detail=detail if verdict else None,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
