"""GraphRule L097: task names should be unique within a play.

Graph-aware port of ``L097_name_unique.py``.  Uses the graph's parent
relationship to find all sibling tasks under the same play, giving a
structurally correct scope for uniqueness rather than the flattened
``ctx.sequence``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleScope, Severity, YAMLDict
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})


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


def _collect_task_names_in_play(graph: ContentGraph, play_id: str) -> Counter[str]:
    """Collect all task names within a play's subtree.

    Args:
        graph: ContentGraph to query.
        play_id: Play node whose descendants are enumerated.

    Returns:
        Counter mapping non-empty task names to occurrence counts.
    """
    names: Counter[str] = Counter()
    for desc_id in graph.descendants(play_id):
        desc = graph.get_node(desc_id)
        if desc is not None and desc.node_type in _TASK_TYPES and desc.name:
            names[desc.name] += 1
    return names


@dataclass
class NameUniqueGraphRule(GraphRule):
    """Detect duplicate task names within a play via graph structure.

    Uses ``graph.descendants`` from the containing play to collect all
    task names, giving structurally-correct scope boundaries instead of
    relying on a flattened sequence.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
        scope: Structural scope.
    """

    rule_id: str = "L097"
    description: str = "Task names should be unique within a play"
    enabled: bool = True
    name: str = "NameUnique"
    version: str = "v0.0.2"
    severity: Severity = Severity.LOW
    tags: tuple[str, ...] = (Tag.QUALITY,)
    scope: str = RuleScope.PLAYBOOK

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match named tasks and handlers.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True if the node is a task/handler with a non-empty name.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        return node.node_type in _TASK_TYPES and bool(node.name)

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Check whether this task's name is duplicated within its play.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with duplicate_name detail if duplicated.
        """
        node = graph.get_node(node_id)
        if node is None or not node.name:
            return None

        play_id = _find_containing_play(graph, node_id)
        if play_id is None:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        counts = _collect_task_names_in_play(graph, play_id)
        dup_count = counts.get(node.name, 0)
        verdict = dup_count > 1

        detail: YAMLDict = {}
        if verdict:
            detail["duplicate_name"] = node.name
            detail["count"] = dup_count
            detail["message"] = f"task name '{node.name}' is not unique (appears {dup_count} times)"

        return GraphRuleResult(
            verdict=verdict,
            detail=detail if detail else None,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
