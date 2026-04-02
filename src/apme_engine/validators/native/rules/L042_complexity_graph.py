"""GraphRule L042: play or block with high task count (complexity).

Graph-aware port of ``L042_complexity.py``.  Uses ``graph.descendants``
to count tasks within the containing play's subtree rather than relying
on the flattened ``ctx.sequence``.  This correctly counts tasks within
nested blocks and includes/imports while staying scoped to the play.
"""

from __future__ import annotations

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleScope, Severity, YAMLDict
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

DEFAULT_TASK_COUNT_THRESHOLD = 20


def _find_containing_play(graph: ContentGraph, node_id: str) -> str | None:
    """Walk ancestors to find the enclosing play node.

    Args:
        graph: ContentGraph to query.
        node_id: Starting node whose ancestry is walked.

    Returns:
        Node ID of the nearest PLAY ancestor, or None.
    """
    for anc in graph.ancestors(node_id):
        if anc.node_type == NodeType.PLAY:
            return anc.node_id
    return None


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
    """Detect plays or blocks with high task count (complexity).

    Uses the graph subtree rooted at the enclosing play to count task
    nodes, giving an accurate measure regardless of block nesting depth.

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
    description: str = "Play or block has high task count (complexity)"
    enabled: bool = True
    name: str = "Complexity"
    version: str = "v0.0.2"
    severity: Severity = Severity.INFO
    tags: tuple[str, ...] = (Tag.DEPENDENCY,)
    scope: str = RuleScope.PLAY
    task_count_threshold: int = DEFAULT_TASK_COUNT_THRESHOLD

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match tasks and handlers.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True if the node is a task or handler.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        return node.node_type in _TASK_TYPES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Count tasks in the containing play subtree.

        Walks up to the nearest play ancestor and counts all TASK/HANDLER
        descendants.  Returns a violation if the count exceeds the threshold.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with task_count and threshold detail.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        play_id = _find_containing_play(graph, node_id)
        if play_id is None:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        task_count = _count_tasks_in_subtree(graph, play_id)
        verdict = task_count > self.task_count_threshold

        detail: YAMLDict = {}
        if verdict:
            detail["task_count"] = task_count
            detail["threshold"] = self.task_count_threshold
            detail["play"] = play_id

        return GraphRuleResult(
            verdict=verdict,
            detail=detail if detail else None,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
