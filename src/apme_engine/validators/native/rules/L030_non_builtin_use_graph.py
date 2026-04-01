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
    """Flag tasks whose declared module FQCN is outside ``ansible.builtin``.

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
        """Match task/handler nodes with a non-builtin collection FQCN on ``module``.

        Short module names (no dot) do not match; only explicit FQCNs from
        outside ``ansible.builtin`` are flagged.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task or handler, ``module`` is non-empty,
            contains a dot, and does not start with ``ansible.builtin.``.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        if node.node_type not in _TASK_TYPES:
            return False
        mod = node.module or ""
        return bool(mod and "." in mod and not mod.startswith("ansible.builtin."))

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report the declared FQCN for non-builtin module usage.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with ``verdict`` True when the violation applies.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        mod = node.module or ""
        verdict = bool(mod and "." in mod and not mod.startswith("ansible.builtin."))
        detail: YAMLDict = {"fqcn": mod}
        return GraphRuleResult(
            verdict=verdict,
            detail=detail if verdict else None,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
