"""GraphRule L036: ``include_vars`` without tags or ``when``.

Graph-aware port of ``L036_unnecessary_include_vars.py``.
"""

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})
_INCLUDE_VARS_NAMES = frozenset({"include_vars", "ansible.builtin.include_vars", "ansible.legacy.include_vars"})


@dataclass
class UnnecessaryIncludeVarsGraphRule(GraphRule):
    """Flag ``include_vars`` tasks that have neither tags nor a ``when`` condition.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L036"
    description: str = "include_vars is used without any condition"
    enabled: bool = True
    name: str = "UnnecessaryIncludeVars"
    version: str = "v0.0.2"
    severity: str = Severity.VERY_LOW
    tags: tuple[str, ...] = (Tag.VARIABLE,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task/handler nodes that invoke ``include_vars``.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task or handler whose authored module
            name is an ``include_vars`` variant.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        if node.node_type not in _TASK_TYPES:
            return False
        return node.module in _INCLUDE_VARS_NAMES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Violate when ``include_vars`` has no tags and no ``when`` expression.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with ``verdict`` True when there is no condition.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        verdict = not node.tags and not node.when_expr
        return GraphRuleResult(
            verdict=verdict,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
