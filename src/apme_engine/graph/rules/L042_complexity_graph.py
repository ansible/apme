"""GraphRule L042: play with high task count (complexity).

Graph-aware port of ``L042_complexity.py``.  Uses ``graph.descendants``
to count tasks within the play's subtree rather than relying on the
flattened ``ctx.sequence``.  This correctly counts tasks within nested
blocks and includes/imports while staying scoped to the play.

The rule matches **PLAY** nodes and reports a single violation per play
when the task count exceeds the threshold.
"""

from __future__ import annotations

from dataclasses import dataclass

from apme_engine.graph.content_graph import ContentGraph, NodeType
from apme_engine.graph.rule_base import GraphRule, GraphRuleResult
from apme_engine.graph.types import RuleScope, Severity, YAMLDict
from apme_engine.graph.types import RuleTag as Tag

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

DEFAULT_TASK_COUNT_THRESHOLD = 20


def _count_tasks_in_subtree(graph: ContentGraph, root_id: str) -> int:
    """Count task and handler nodes within a subtree.

    Args:
        graph: ContentGraph to query.
        root_id: Root of the subtree to count.

    Returns:
        Number of TASK and HANDLER descendants (excludes root).
    """
    count = 0
    for desc_id in graph.descendants(root_id):
        desc = graph.get_node(desc_id)
        if desc is not None and desc.node_type in _TASK_TYPES:
            count += 1
    return count


@dataclass
class ComplexityGraphRule(GraphRule):
    """Detect plays with high task count (complexity).

    Matches PLAY nodes and counts task/handler descendants in their
    subtree.  Reports a single violation per play when the count
    exceeds the threshold.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
        scope: Structural scope.
        task_count_threshold: Maximum allowed task count before flagging.
    """

    rule_id: str = "L042"
    description: str = "Play has high task count (complexity)"
    enabled: bool = True
    name: str = "Complexity"
    version: str = "v0.0.3"
    severity: Severity = Severity.INFO
    tags: tuple[str, ...] = (Tag.DEPENDENCY,)
    scope: str = RuleScope.PLAY
    task_count_threshold: int = DEFAULT_TASK_COUNT_THRESHOLD

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match play nodes only.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True if the node is a play.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        return node.node_type == NodeType.PLAY

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Count tasks in the play subtree.

        Counts all TASK/HANDLER descendants of this play node and
        returns a violation if the count exceeds the threshold.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the play node to evaluate.

        Returns:
            GraphRuleResult with task_count and threshold detail.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        task_count = _count_tasks_in_subtree(graph, node_id)
        verdict = task_count > self.task_count_threshold

        detail: YAMLDict = {}
        if verdict:
            detail["task_count"] = task_count
            detail["threshold"] = self.task_count_threshold
            detail["affected_children"] = task_count

        return GraphRuleResult(
            verdict=verdict,
            detail=detail if detail else None,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
