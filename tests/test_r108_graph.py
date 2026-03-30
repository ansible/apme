"""Tests for R108 privilege escalation GraphRule (ADR-044 Phase 2A)."""

from __future__ import annotations

from apme_engine.engine.content_graph import (
    ContentGraph,
    ContentNode,
    EdgeType,
    NodeIdentity,
    NodeScope,
    NodeType,
)
from apme_engine.engine.graph_scanner import scan
from apme_engine.validators.native.rules.graph_rule_base import GraphRule
from apme_engine.validators.native.rules.R108_privilege_escalation_graph import (
    PrivilegeEscalationGraphRule,
)


def _make_become_graph() -> ContentGraph:
    """Build a graph with two plays: one with become and one without.

    Returns:
        A ``ContentGraph`` suitable for R108 become / inheritance tests.
    """
    g = ContentGraph()
    pb = ContentNode(
        identity=NodeIdentity("site.yml", NodeType.PLAYBOOK),
        file_path="site.yml",
        scope=NodeScope.OWNED,
    )
    play_become = ContentNode(
        identity=NodeIdentity("site.yml::play[0]", NodeType.PLAY),
        file_path="site.yml",
        line_start=1,
        become={"become": True, "become_user": "root"},
        scope=NodeScope.OWNED,
    )
    play_normal = ContentNode(
        identity=NodeIdentity("site.yml::play[1]", NodeType.PLAY),
        file_path="site.yml",
        line_start=20,
        scope=NodeScope.OWNED,
    )
    t_inherited = ContentNode(
        identity=NodeIdentity("site.yml::play[0]/tasks[0]", NodeType.TASK),
        file_path="site.yml",
        line_start=5,
        name="Escalated task (inherited)",
        module="ansible.builtin.yum",
        scope=NodeScope.OWNED,
    )
    t_normal = ContentNode(
        identity=NodeIdentity("site.yml::play[1]/tasks[0]", NodeType.TASK),
        file_path="site.yml",
        line_start=25,
        name="Normal task (no become)",
        module="ansible.builtin.debug",
        scope=NodeScope.OWNED,
    )
    t_explicit = ContentNode(
        identity=NodeIdentity("site.yml::play[1]/tasks[1]", NodeType.TASK),
        file_path="site.yml",
        line_start=30,
        name="Self-become task",
        module="ansible.builtin.shell",
        become={"become": True, "become_user": "admin"},
        scope=NodeScope.OWNED,
    )
    g.add_node(pb)
    g.add_node(play_become)
    g.add_node(play_normal)
    g.add_node(t_inherited)
    g.add_node(t_normal)
    g.add_node(t_explicit)
    g.add_edge(pb.node_id, play_become.node_id, EdgeType.CONTAINS)
    g.add_edge(pb.node_id, play_normal.node_id, EdgeType.CONTAINS)
    g.add_edge(play_become.node_id, t_inherited.node_id, EdgeType.CONTAINS)
    g.add_edge(play_normal.node_id, t_normal.node_id, EdgeType.CONTAINS)
    g.add_edge(play_normal.node_id, t_explicit.node_id, EdgeType.CONTAINS)
    return g


class TestR108GraphRule:
    """Tests for the graph-based R108 privilege escalation rule."""

    def test_match_task_with_become(self) -> None:
        """Verify match returns True for tasks with become set."""
        graph = _make_become_graph()
        rule = PrivilegeEscalationGraphRule()
        assert rule.match(graph, "site.yml::play[0]/tasks[0]")

    def test_no_match_task_without_become(self) -> None:
        """Verify match returns False for tasks in a play without become."""
        graph = _make_become_graph()
        rule = PrivilegeEscalationGraphRule()
        assert not rule.match(graph, "site.yml::play[1]/tasks[0]")

    def test_no_match_play_node(self) -> None:
        """Verify match returns False for non-task nodes."""
        graph = _make_become_graph()
        rule = PrivilegeEscalationGraphRule()
        assert not rule.match(graph, "site.yml::play[0]")

    def test_process_returns_become_detail(self) -> None:
        """Verify process includes become info and inheritance in detail."""
        graph = _make_become_graph()
        rule = PrivilegeEscalationGraphRule()
        result = rule.process(graph, "site.yml::play[0]/tasks[0]")

        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert "become" in result.detail
        assert result.detail.get("inherited_from") == "site.yml::play[0]"

    def test_process_attributes_inherited_become(self) -> None:
        """Verify inherited_from is set when become comes from play."""
        graph = _make_become_graph()
        rule = PrivilegeEscalationGraphRule()
        result = rule.process(graph, "site.yml::play[0]/tasks[0]")

        assert result is not None
        assert result.detail is not None
        assert result.detail.get("inherited_from") == "site.yml::play[0]"

    def test_process_explicit_become_on_task(self) -> None:
        """Verify task with its own become shows no inherited_from.

        play[1]/tasks[1] declares its own ``become``.  PropertyOrigin
        finds the task itself as the defining scope, so
        ``inherited_from`` is not set.
        """
        graph = _make_become_graph()
        rule = PrivilegeEscalationGraphRule()
        result = rule.process(graph, "site.yml::play[1]/tasks[1]")

        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail.get("become") is True
        assert result.detail.get("inherited_from") is None

    def test_scan_integration(self) -> None:
        """Verify R108 integrates with the graph scanner."""
        graph = _make_become_graph()
        rules: list[GraphRule] = [PrivilegeEscalationGraphRule()]
        report = scan(graph, rules)

        flagged_ids = {r.node_id for nr in report.node_results for r in nr.rule_results}
        assert "site.yml::play[0]/tasks[0]" in flagged_ids
        assert "site.yml::play[1]/tasks[1]" in flagged_ids
        assert "site.yml::play[1]/tasks[0]" not in flagged_ids

    def test_match_nonexistent_node(self) -> None:
        """Verify match returns False for unknown node IDs."""
        graph = _make_become_graph()
        rule = PrivilegeEscalationGraphRule()
        assert not rule.match(graph, "nonexistent::node")

    def test_process_nonexistent_node(self) -> None:
        """Verify process returns None for unknown node IDs."""
        graph = _make_become_graph()
        rule = PrivilegeEscalationGraphRule()
        result = rule.process(graph, "nonexistent::node")
        assert result is None

    def test_handler_with_become(self) -> None:
        """Verify R108 matches handlers with become."""
        g = ContentGraph()
        handler = ContentNode(
            identity=NodeIdentity("handlers/main.yml::handler[0]", NodeType.HANDLER),
            file_path="handlers/main.yml",
            line_start=1,
            name="restart service",
            become={"become": True, "become_user": "root"},
            scope=NodeScope.OWNED,
        )
        g.add_node(handler)
        rule = PrivilegeEscalationGraphRule()
        assert rule.match(g, handler.node_id)
