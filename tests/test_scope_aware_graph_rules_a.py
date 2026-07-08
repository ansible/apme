"""Unit tests for graph-native rules L042, L097, L086, and R117 (scope-aware).

These rules use ``ContentGraph`` subtrees, sibling queries, play-level
vars checks, and role metadata to improve accuracy over the flat pipeline.
"""

from __future__ import annotations

import pytest

from apme_engine.engine.models import YAMLDict
from apme_engine.graph.content_graph import (
    ContentGraph,
    ContentNode,
    EdgeType,
    NodeIdentity,
    NodeScope,
    NodeType,
)
from apme_engine.graph.rules.L042_complexity_graph import ComplexityGraphRule
from apme_engine.graph.rules.L086_play_vars_usage_graph import PlayVarsUsageGraphRule
from apme_engine.graph.rules.L097_name_unique_graph import NameUniqueGraphRule
from apme_engine.graph.rules.R117_external_role_graph import ExternalRoleGraphRule
from apme_engine.graph.scanner import scan


def _build_play_with_tasks(
    task_count: int,
    *,
    task_names: list[str] | None = None,
) -> tuple[ContentGraph, str, str]:
    """Build a graph: playbook -> play -> N tasks.

    Args:
        task_count: Number of task nodes to create.
        task_names: Optional names for tasks (cycled if shorter).

    Returns:
        Tuple of (graph, play_node_id, first_task_node_id).
    """
    g = ContentGraph()
    pb = ContentNode(
        identity=NodeIdentity(path="site.yml", node_type=NodeType.PLAYBOOK),
        file_path="site.yml",
        scope=NodeScope.OWNED,
    )
    play = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
        file_path="site.yml",
        line_start=1,
        scope=NodeScope.OWNED,
    )
    g.add_node(pb)
    g.add_node(play)
    g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)

    first_task_id = ""
    for i in range(task_count):
        name = None
        if task_names:
            name = task_names[i % len(task_names)]
        task = ContentNode(
            identity=NodeIdentity(path=f"site.yml/plays[0]/tasks[{i}]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=10 + i,
            name=name,
            module="debug",
            scope=NodeScope.OWNED,
        )
        g.add_node(task)
        g.add_edge(play.node_id, task.node_id, EdgeType.CONTAINS)
        if i == 0:
            first_task_id = task.node_id

    return g, play.node_id, first_task_id


# ---------------------------------------------------------------------------
# L042 — Complexity
# ---------------------------------------------------------------------------


class TestL042GraphRule:
    """Tests for ``ComplexityGraphRule`` (L042)."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> ComplexityGraphRule:
        """Provide a fresh L042 rule instance.

        Returns:
            A new ``ComplexityGraphRule``.
        """
        return ComplexityGraphRule()

    def test_match_play(self, rule: ComplexityGraphRule) -> None:
        """Play nodes match.

        Args:
            rule: Rule instance under test.
        """
        g, play_id, _ = _build_play_with_tasks(1)
        assert rule.match(g, play_id)

    def test_no_match_task(self, rule: ComplexityGraphRule) -> None:
        """Task nodes do not match.

        Args:
            rule: Rule instance under test.
        """
        g, _, task_id = _build_play_with_tasks(1)
        assert not rule.match(g, task_id)

    def test_below_threshold_no_violation(self, rule: ComplexityGraphRule) -> None:
        """Play with 5 tasks is under the default threshold.

        Args:
            rule: Rule instance under test.
        """
        g, play_id, _ = _build_play_with_tasks(5)
        result = rule.process(g, play_id)
        assert result is not None
        assert result.verdict is False

    def test_above_threshold_violation(self, rule: ComplexityGraphRule) -> None:
        """Play with 25 tasks triggers a single violation on the play node.

        Args:
            rule: Rule instance under test.
        """
        g, play_id, _ = _build_play_with_tasks(25)
        result = rule.process(g, play_id)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["task_count"] == 25
        assert result.detail["threshold"] == 20
        assert result.detail["affected_children"] == 25
        assert result.node_id == play_id

    def test_custom_threshold(self) -> None:
        """Custom threshold is respected."""
        rule = ComplexityGraphRule(task_count_threshold=3)
        g, play_id, _ = _build_play_with_tasks(5)
        result = rule.process(g, play_id)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["task_count"] == 5


# ---------------------------------------------------------------------------
# L097 — Name Unique
# ---------------------------------------------------------------------------


class TestL097GraphRule:
    """Tests for ``NameUniqueGraphRule`` (L097)."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> NameUniqueGraphRule:
        """Provide a fresh L097 rule instance.

        Returns:
            A new ``NameUniqueGraphRule``.
        """
        return NameUniqueGraphRule()

    def test_match_named_task(self, rule: NameUniqueGraphRule) -> None:
        """Named tasks match.

        Args:
            rule: Rule instance under test.
        """
        g, _, task_id = _build_play_with_tasks(1, task_names=["Install package"])
        assert rule.match(g, task_id)

    def test_no_match_unnamed_task(self, rule: NameUniqueGraphRule) -> None:
        """Unnamed tasks do not match.

        Args:
            rule: Rule instance under test.
        """
        g, _, task_id = _build_play_with_tasks(1)
        assert not rule.match(g, task_id)

    def test_unique_names_no_violation(self, rule: NameUniqueGraphRule) -> None:
        """Unique task names produce no violation.

        Args:
            rule: Rule instance under test.
        """
        g, _, task_id = _build_play_with_tasks(3, task_names=["A", "B", "C"])
        result = rule.process(g, task_id)
        assert result is not None
        assert result.verdict is False

    def test_duplicate_names_violation(self, rule: NameUniqueGraphRule) -> None:
        """Duplicate task names trigger a violation.

        Args:
            rule: Rule instance under test.
        """
        g, _, task_id = _build_play_with_tasks(3, task_names=["Deploy app", "Deploy app", "Other"])
        result = rule.process(g, task_id)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["duplicate_name"] == "Deploy app"
        assert result.detail["count"] == 2

    def test_no_play_ancestor_no_violation(self, rule: NameUniqueGraphRule) -> None:
        """Named task without play ancestor does not violate.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        task = ContentNode(
            identity=NodeIdentity(path="orphan/tasks[0]", node_type=NodeType.TASK),
            file_path="orphan.yml",
            name="My task",
            scope=NodeScope.OWNED,
        )
        g.add_node(task)
        result = rule.process(g, task.node_id)
        assert result is not None
        assert result.verdict is False


# ---------------------------------------------------------------------------
# L086 — Play Vars Usage
# ---------------------------------------------------------------------------


class TestL086GraphRule:
    """Tests for ``PlayVarsUsageGraphRule`` (L086)."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> PlayVarsUsageGraphRule:
        """Provide a fresh L086 rule instance.

        Returns:
            A new ``PlayVarsUsageGraphRule``.
        """
        return PlayVarsUsageGraphRule()

    def test_match_play(self, rule: PlayVarsUsageGraphRule) -> None:
        """Play nodes match.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        play = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
            file_path="site.yml",
            scope=NodeScope.OWNED,
        )
        g.add_node(play)
        assert rule.match(g, play.node_id)

    def test_no_match_task(self, rule: PlayVarsUsageGraphRule) -> None:
        """Task nodes do not match.

        Args:
            rule: Rule instance under test.
        """
        g, _, task_id = _build_play_with_tasks(1)
        assert not rule.match(g, task_id)

    def test_few_vars_no_violation(self, rule: PlayVarsUsageGraphRule) -> None:
        """Play with 3 vars is under threshold.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        play_vars: YAMLDict = {"a": 1, "b": 2, "c": 3}
        play = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
            file_path="site.yml",
            variables=play_vars,
            scope=NodeScope.OWNED,
        )
        g.add_node(play)
        result = rule.process(g, play.node_id)
        assert result is not None
        assert result.verdict is False

    def test_many_vars_violation(self, rule: PlayVarsUsageGraphRule) -> None:
        """Play with 8 vars triggers a violation.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        many_vars: YAMLDict = {f"var_{i}": i for i in range(8)}
        play = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
            file_path="site.yml",
            variables=many_vars,
            scope=NodeScope.OWNED,
        )
        g.add_node(play)
        result = rule.process(g, play.node_id)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["var_count"] == 8


# ---------------------------------------------------------------------------
# R117 — External Role
# ---------------------------------------------------------------------------


class TestR117GraphRule:
    """Tests for ``ExternalRoleGraphRule`` (R117)."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> ExternalRoleGraphRule:
        """Provide a fresh R117 rule instance.

        Returns:
            A new ``ExternalRoleGraphRule``.
        """
        return ExternalRoleGraphRule()

    def _build_role_in_play(
        self,
        *,
        galaxy_info: bool = False,
        role_fqcn: str = "",
    ) -> tuple[ContentGraph, str]:
        """Build playbook -> play --(DEPENDENCY)--> role graph.

        Uses ``role_metadata`` and ``EdgeType.DEPENDENCY`` to match
        how ``GraphBuilder._build_role()`` constructs play→role edges.

        Args:
            galaxy_info: Whether to add galaxy_info to role_metadata.
            role_fqcn: FQCN string for the role node.

        Returns:
            Tuple of (graph, role_node_id).
        """
        g = ContentGraph()
        pb = ContentNode(
            identity=NodeIdentity(path="site.yml", node_type=NodeType.PLAYBOOK),
            file_path="site.yml",
            scope=NodeScope.OWNED,
        )
        play = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
            file_path="site.yml",
            scope=NodeScope.OWNED,
        )
        metadata: YAMLDict = {}
        if galaxy_info:
            metadata["galaxy_info"] = {"author": "upstream", "role_name": "nginx"}
        role = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/roles[0]", node_type=NodeType.ROLE),
            file_path="roles/nginx/meta/main.yml",
            name="nginx",
            role_fqcn=role_fqcn,
            role_metadata=metadata,
            scope=NodeScope.OWNED,
        )
        g.add_node(pb)
        g.add_node(play)
        g.add_node(role)
        g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)
        g.add_edge(play.node_id, role.node_id, EdgeType.DEPENDENCY)
        return g, role.node_id

    def test_match_external_role(self, rule: ExternalRoleGraphRule) -> None:
        """Role with galaxy_info under a play matches.

        Args:
            rule: Rule instance under test.
        """
        g, role_id = self._build_role_in_play(galaxy_info=True)
        assert rule.match(g, role_id)

    def test_no_match_local_role(self, rule: ExternalRoleGraphRule) -> None:
        """Role without galaxy_info does not match.

        Args:
            rule: Rule instance under test.
        """
        g, role_id = self._build_role_in_play(galaxy_info=False)
        assert not rule.match(g, role_id)

    def test_no_match_standalone_role(self, rule: ExternalRoleGraphRule) -> None:
        """Role without play dependency does not match (standalone scan).

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        role = ContentNode(
            identity=NodeIdentity(path="roles/nginx", node_type=NodeType.ROLE),
            file_path="roles/nginx/meta/main.yml",
            role_metadata={"galaxy_info": {"author": "someone"}},
            scope=NodeScope.OWNED,
        )
        g.add_node(role)
        assert not rule.match(g, role.node_id)

    def test_process_external_role(self, rule: ExternalRoleGraphRule) -> None:
        """Process reports external role with FQCN detail.

        Args:
            rule: Rule instance under test.
        """
        g, role_id = self._build_role_in_play(galaxy_info=True, role_fqcn="community.nginx")
        result = rule.process(g, role_id)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["role_fqcn"] == "community.nginx"


# ---------------------------------------------------------------------------
# Scanner integration
# ---------------------------------------------------------------------------


class TestScopeAwareGraphScanIntegrationA:
    """Integration tests running multiple scope-aware rules through the scanner."""

    def test_scan_complexity_violation(self) -> None:
        """Scanner picks up L042 violation for a complex play."""
        rule = ComplexityGraphRule(task_count_threshold=3)
        g, _, _ = _build_play_with_tasks(5, task_names=["t1", "t2", "t3", "t4", "t5"])
        report = scan(g, [rule])
        all_results = [rr for nr in report.node_results for rr in nr.rule_results]
        assert any(rr.verdict is True for rr in all_results)

    def test_scan_duplicate_names(self) -> None:
        """Scanner picks up L097 violation for duplicate task names."""
        rule = NameUniqueGraphRule()
        g, _, _ = _build_play_with_tasks(3, task_names=["dup", "dup", "other"])
        report = scan(g, [rule])
        all_results = [rr for nr in report.node_results for rr in nr.rule_results]
        assert any(rr.verdict is True for rr in all_results)
