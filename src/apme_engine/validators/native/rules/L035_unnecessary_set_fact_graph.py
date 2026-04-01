"""GraphRule L035: set_fact with random filter makes task non-idempotent."""

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict, YAMLValue
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

_SET_FACT_MODULES = frozenset(
    {
        "ansible.builtin.set_fact",
        "ansible.legacy.set_fact",
        "set_fact",
    }
)


@dataclass
class UnnecessarySetFactGraphRule(GraphRule):
    """Detect set_fact tasks whose values use the ``random`` filter.

    The ``random`` filter produces non-deterministic output, making the
    task non-idempotent.  Consider using ``ansible.builtin.password``
    with a seed file or pre-generating the value.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L035"
    description: str = "set_fact uses the random filter, making the task non-idempotent"
    enabled: bool = True
    name: str = "UnnecessarySetFact"
    version: str = "v0.0.1"
    severity: str = Severity.VERY_LOW
    tags: tuple[str, ...] = (Tag.VARIABLE,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task/handler nodes that invoke set_fact.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a set_fact task or handler.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return False
        mod = node.module
        return mod in _SET_FACT_MODULES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report when set_fact arguments contain the ``random`` filter.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result with impure_args detail, or None if node missing.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        mo = node.module_options
        if not isinstance(mo, dict):
            mo = {}

        impure_args: list[YAMLValue] = []
        for val in mo.values():
            if isinstance(val, str) and "random" in val:
                impure_args.append(val)

        if not impure_args:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        detail: YAMLDict = {"impure_args": impure_args}
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
