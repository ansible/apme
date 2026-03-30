"""Tests for GraphRule base class (ADR-044 Phase 1)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from apme_engine.engine.content_graph import (
    ContentGraph,
    ContentNode,
    NodeIdentity,
    NodeType,
)
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult


@dataclass
class SampleGraphRule(GraphRule):
    """Trivial rule: tasks must have a name.

    Attributes:
        rule_id: Rule identifier string.
        description: Human-readable rule description.
        enabled: Whether the rule is active.
    """

    rule_id: str = "TEST001"
    description: str = "Tasks must have a name"
    enabled: bool = True

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Return True when the node exists and is a task.

        Args:
            graph: Content graph under scan.
            node_id: Node identifier to inspect.

        Returns:
            True if the node is a task, else False.
        """
        node = graph.get_node(node_id)
        return node is not None and node.node_type == NodeType.TASK

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Evaluate whether the task has a name.

        Args:
            graph: Content graph under scan.
            node_id: Task node identifier.

        Returns:
            Rule result with pass/fail, or None if the node is missing.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        has_name = bool(node.name)
        return GraphRuleResult(
            rule=self,
            verdict=has_name,
            node_id=node_id,
            detail={"name": node.name} if not has_name else None,
            file=(node.file_path, node.line_start),
        )


def _make_test_graph() -> ContentGraph:
    """Build a graph with named/unnamed tasks and a play for rule tests.

    Returns:
        Content graph suitable for ``SampleGraphRule`` tests.
    """
    g = ContentGraph()

    named_task = ContentNode(
        identity=NodeIdentity(path="site.yml/tasks[0]", node_type=NodeType.TASK),
        file_path="site.yml",
        line_start=5,
        name="Install package",
    )
    g.add_node(named_task)

    unnamed_task = ContentNode(
        identity=NodeIdentity(path="site.yml/tasks[1]", node_type=NodeType.TASK),
        file_path="site.yml",
        line_start=10,
        name=None,
    )
    g.add_node(unnamed_task)

    play = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
        file_path="site.yml",
    )
    g.add_node(play)

    return g


class TestGraphRule:
    """Tests for ``GraphRule`` construction and ``SampleGraphRule`` behavior."""

    def test_base_class_raises(self) -> None:
        """Verify base ``GraphRule`` rejects empty rule_id."""
        with pytest.raises(ValueError):
            GraphRule(rule_id="", description="test")

    def test_match_filters_by_type(self) -> None:
        """Verify match is true for tasks and false for plays."""
        rule = SampleGraphRule()
        g = _make_test_graph()

        assert rule.match(g, "site.yml/tasks[0]") is True
        assert rule.match(g, "site.yml/plays[0]") is False

    def test_process_pass(self) -> None:
        """Verify process passes for a named task."""
        rule = SampleGraphRule()
        g = _make_test_graph()

        result = rule.process(g, "site.yml/tasks[0]")
        assert result is not None
        assert result.passed is True
        assert result.failed is False

    def test_process_fail(self) -> None:
        """Verify process fails for an unnamed task with expected metadata."""
        rule = SampleGraphRule()
        g = _make_test_graph()

        result = rule.process(g, "site.yml/tasks[1]")
        assert result is not None
        assert result.passed is False
        assert result.failed is True
        assert result.node_id == "site.yml/tasks[1]"

    def test_not_implemented_on_base(self) -> None:
        """Verify abstract base raises NotImplementedError for match and process."""
        rule = GraphRule.__new__(GraphRule)
        rule.rule_id = "X"
        rule.description = "X"
        g = ContentGraph()
        with pytest.raises(NotImplementedError):
            rule.match(g, "x")
        with pytest.raises(NotImplementedError):
            rule.process(g, "x")
