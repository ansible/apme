"""Tests for R108 privilege escalation GraphRule — scope-based deduplication."""

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
    """Build a graph: play with become (3 tasks), play without (1 normal + 1 explicit).

    Returns:
        A ``ContentGraph`` suitable for R108 scope-dedup tests.
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
    t0 = ContentNode(
        identity=NodeIdentity("site.yml::play[0]/tasks[0]", NodeType.TASK),
        file_path="site.yml",
        line_start=5,
        name="Task A (inherits become)",
        module="ansible.builtin.yum",
        scope=NodeScope.OWNED,
    )
    t1 = ContentNode(
        identity=NodeIdentity("site.yml::play[0]/tasks[1]", NodeType.TASK),
        file_path="site.yml",
        line_start=10,
        name="Task B (inherits become)",
        module="ansible.builtin.copy",
        scope=NodeScope.OWNED,
    )
    t2 = ContentNode(
        identity=NodeIdentity("site.yml::play[0]/tasks[2]", NodeType.TASK),
        file_path="site.yml",
        line_start=15,
        name="Task C (inherits become)",
        module="ansible.builtin.service",
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
    for n in (pb, play_become, play_normal, t0, t1, t2, t_normal, t_explicit):
        g.add_node(n)
    g.add_edge(pb.node_id, play_become.node_id, EdgeType.CONTAINS)
    g.add_edge(pb.node_id, play_normal.node_id, EdgeType.CONTAINS)
    g.add_edge(play_become.node_id, t0.node_id, EdgeType.CONTAINS)
    g.add_edge(play_become.node_id, t1.node_id, EdgeType.CONTAINS)
    g.add_edge(play_become.node_id, t2.node_id, EdgeType.CONTAINS)
    g.add_edge(play_normal.node_id, t_normal.node_id, EdgeType.CONTAINS)
    g.add_edge(play_normal.node_id, t_explicit.node_id, EdgeType.CONTAINS)
    return g


class TestR108ScopeDedup:
    """R108 fires once on the defining scope, not on every inheriting child."""

    def test_match_play_with_become(self) -> None:
        """Play that defines become matches."""
        graph = _make_become_graph()
        rule = PrivilegeEscalationGraphRule()
        assert rule.match(graph, "site.yml::play[0]")

    def test_no_match_inheriting_child(self) -> None:
        """Task that only inherits become does NOT match."""
        graph = _make_become_graph()
        rule = PrivilegeEscalationGraphRule()
        assert not rule.match(graph, "site.yml::play[0]/tasks[0]")

    def test_match_explicit_task_become(self) -> None:
        """Task with its own become still matches."""
        graph = _make_become_graph()
        rule = PrivilegeEscalationGraphRule()
        assert rule.match(graph, "site.yml::play[1]/tasks[1]")

    def test_no_match_play_without_become(self) -> None:
        """Play without become does not match."""
        graph = _make_become_graph()
        rule = PrivilegeEscalationGraphRule()
        assert not rule.match(graph, "site.yml::play[1]")

    def test_no_match_normal_task(self) -> None:
        """Task without become in a clean play does not match."""
        graph = _make_become_graph()
        rule = PrivilegeEscalationGraphRule()
        assert not rule.match(graph, "site.yml::play[1]/tasks[0]")

    def test_process_play_affected_children(self) -> None:
        """Play with become reports affected_children count."""
        graph = _make_become_graph()
        rule = PrivilegeEscalationGraphRule()
        result = rule.process(graph, "site.yml::play[0]")

        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["affected_children"] == 3

    def test_process_task_no_affected_children(self) -> None:
        """Task-level become has no affected_children."""
        graph = _make_become_graph()
        rule = PrivilegeEscalationGraphRule()
        result = rule.process(graph, "site.yml::play[1]/tasks[1]")

        assert result is not None
        assert result.detail is not None
        assert "affected_children" not in result.detail

    def test_scan_dedup(self) -> None:
        """Full scan: 2 violations (play + explicit task), not 4."""
        graph = _make_become_graph()
        rules: list[GraphRule] = [PrivilegeEscalationGraphRule()]
        report = scan(graph, rules)

        flagged = {r.node_id for nr in report.node_results for r in nr.rule_results if r.verdict}
        assert "site.yml::play[0]" in flagged
        assert "site.yml::play[1]/tasks[1]" in flagged
        assert len(flagged) == 2

    def test_block_with_become(self) -> None:
        """Block that defines become fires once with affected_children."""
        g = ContentGraph()
        play = ContentNode(
            identity=NodeIdentity("p.yml/plays[0]", NodeType.PLAY),
            file_path="p.yml",
            scope=NodeScope.OWNED,
        )
        block = ContentNode(
            identity=NodeIdentity("p.yml/plays[0]/tasks[0]", NodeType.BLOCK),
            file_path="p.yml",
            line_start=5,
            become={"become": True, "become_user": "root"},
            scope=NodeScope.OWNED,
        )
        child1 = ContentNode(
            identity=NodeIdentity("p.yml/plays[0]/tasks[0]/block[0]", NodeType.TASK),
            file_path="p.yml",
            line_start=7,
            module="ansible.builtin.debug",
            scope=NodeScope.OWNED,
        )
        child2 = ContentNode(
            identity=NodeIdentity("p.yml/plays[0]/tasks[0]/block[1]", NodeType.TASK),
            file_path="p.yml",
            line_start=10,
            module="ansible.builtin.copy",
            scope=NodeScope.OWNED,
        )
        for n in (play, block, child1, child2):
            g.add_node(n)
        g.add_edge(play.node_id, block.node_id, EdgeType.CONTAINS)
        g.add_edge(block.node_id, child1.node_id, EdgeType.CONTAINS)
        g.add_edge(block.node_id, child2.node_id, EdgeType.CONTAINS)

        rule = PrivilegeEscalationGraphRule()
        assert rule.match(g, block.node_id)
        assert not rule.match(g, child1.node_id)
        assert not rule.match(g, child2.node_id)

        result = rule.process(g, block.node_id)
        assert result is not None
        assert result.detail is not None
        assert result.detail["affected_children"] == 2

    def test_handler_with_become(self) -> None:
        """Handler with its own become still matches."""
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

    def test_match_nonexistent_node(self) -> None:
        """Match returns False for unknown node IDs."""
        graph = _make_become_graph()
        rule = PrivilegeEscalationGraphRule()
        assert not rule.match(graph, "nonexistent::node")

    def test_process_nonexistent_node(self) -> None:
        """Process returns None for unknown node IDs."""
        graph = _make_become_graph()
        rule = PrivilegeEscalationGraphRule()
        result = rule.process(graph, "nonexistent::node")
        assert result is None
