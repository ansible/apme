"""GraphRule L026: tasks using short module names instead of FQCN.

Graph-aware port of ``L026_non_fqcn_use.py``.
"""

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})


@dataclass
class NonFQCNUseGraphRule(GraphRule):
    """Flag tasks whose declared module is a short name (no dot), not an FQCN.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L026"
    description: str = "A task with a short module name is found"
    enabled: bool = True
    name: str = "NonFQCNUse"
    version: str = "v0.0.2"
    severity: str = Severity.VERY_LOW
    tags: tuple[str, ...] = (Tag.DEPENDENCY,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task/handler nodes whose module is a non-empty short name (not FQCN).

        A name containing ``.`` is treated as already FQCN-qualified.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task or handler, ``module`` is non-empty, and
            it contains no dot (short form such as ``copy`` rather than
            ``ansible.builtin.copy``).
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        if node.node_type not in _TASK_TYPES:
            return False
        mod = node.module or ""
        return bool(mod and "." not in mod)

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report the short module name when the rule fires.

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
        verdict = bool(mod and "." not in mod)
        detail: YAMLDict = {"module": mod}
        return GraphRuleResult(
            verdict=verdict,
            detail=detail if verdict else None,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
