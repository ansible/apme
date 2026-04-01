"""Unit tests for graph-native rules L032, L034, L093, and M005 (scope-aware).

These rules use ``VariableProvenanceResolver``, ``DATA_FLOW`` edges,
role ancestry, and scope-aware variable redefinition detection.
"""

from __future__ import annotations

from typing import cast

import pytest

from apme_engine.engine.content_graph import (
    ContentGraph,
    ContentNode,
    EdgeType,
    NodeIdentity,
    NodeScope,
    NodeType,
)
from apme_engine.engine.graph_scanner import scan
from apme_engine.engine.models import YAMLDict
from apme_engine.validators.native.rules.L032_changed_data_dependence_graph import (
    ChangedDataDependenceGraphRule,
)
from apme_engine.validators.native.rules.L034_unused_override_graph import UnusedOverrideGraphRule
from apme_engine.validators.native.rules.L093_set_fact_override_graph import SetFactOverrideGraphRule
from apme_engine.validators.native.rules.M005_data_tagging_graph import DataTaggingGraphRule


def _build_playbook_play_task(
    *,
    play_vars: YAMLDict | None = None,
    task_module: str = "debug",
    task_module_options: YAMLDict | None = None,
    task_options: YAMLDict | None = None,
    task_register: str | None = None,
    task_set_facts: YAMLDict | None = None,
    task_name: str | None = None,
) -> tuple[ContentGraph, str, str]:
    """Build a graph: playbook -> play -> task.

    Args:
        play_vars: Variables on the play node.
        task_module: Module name as authored in YAML (short or FQCN).
        task_module_options: Module options dict.
        task_options: Task options dict.
        task_register: Register variable name.
        task_set_facts: Facts set by the task.
        task_name: Optional task name.

    Returns:
        Tuple of (graph, play_node_id, task_node_id).
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
        variables=play_vars or {},
        scope=NodeScope.OWNED,
    )
    effective_set_facts = task_set_facts or {}
    effective_module_opts = task_module_options or {}
    if effective_set_facts and not effective_module_opts:
        effective_module_opts = effective_set_facts
    task = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
        file_path="site.yml",
        line_start=10,
        name=task_name,
        module=task_module,
        module_options=effective_module_opts,
        options=task_options or {},
        register=task_register,
        set_facts=effective_set_facts,
        scope=NodeScope.OWNED,
    )
    g.add_node(pb)
    g.add_node(play)
    g.add_node(task)
    g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)
    g.add_edge(play.node_id, task.node_id, EdgeType.CONTAINS)
    return g, play.node_id, task.node_id


# ---------------------------------------------------------------------------
# L032 — Changed Data Dependence
# ---------------------------------------------------------------------------


class TestL032GraphRule:
    """Tests for ``ChangedDataDependenceGraphRule`` (L032)."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> ChangedDataDependenceGraphRule:
        """Provide a fresh L032 rule instance.

        Returns:
            A new ``ChangedDataDependenceGraphRule``.
        """
        return ChangedDataDependenceGraphRule()

    def test_match_task_with_register(self, rule: ChangedDataDependenceGraphRule) -> None:
        """Task with register matches.

        Args:
            rule: Rule instance under test.
        """
        g, _, task_id = _build_playbook_play_task(task_register="result")
        assert rule.match(g, task_id)

    def test_match_set_fact_task(self, rule: ChangedDataDependenceGraphRule) -> None:
        """set_fact task matches.

        Args:
            rule: Rule instance under test.
        """
        g, _, task_id = _build_playbook_play_task(
            task_module="ansible.builtin.set_fact",
        )
        assert rule.match(g, task_id)

    def test_no_match_plain_task(self, rule: ChangedDataDependenceGraphRule) -> None:
        """Task without register or set_fact does not match.

        Args:
            rule: Rule instance under test.
        """
        g, _, task_id = _build_playbook_play_task()
        assert not rule.match(g, task_id)

    def test_no_redef_no_violation(self, rule: ChangedDataDependenceGraphRule) -> None:
        """Register that doesn't collide with play vars is clean.

        Args:
            rule: Rule instance under test.
        """
        g, _, task_id = _build_playbook_play_task(
            play_vars={"other_var": "value"},
            task_register="result",
        )
        result = rule.process(g, task_id)
        assert result is not None
        assert result.verdict is False

    def test_redef_violation(self, rule: ChangedDataDependenceGraphRule) -> None:
        """Register that shadows a play var triggers a violation.

        Args:
            rule: Rule instance under test.
        """
        g, _, task_id = _build_playbook_play_task(
            play_vars={"result": "old_value"},
            task_register="result",
        )
        result = rule.process(g, task_id)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        variables = cast(list[YAMLDict], result.detail["variables"])
        assert any(v["name"] == "result" for v in variables)

    def test_set_fact_redef(self, rule: ChangedDataDependenceGraphRule) -> None:
        """set_fact that redefines a play var triggers a violation.

        Args:
            rule: Rule instance under test.
        """
        g, _, task_id = _build_playbook_play_task(
            play_vars={"my_var": "original"},
            task_module="ansible.builtin.set_fact",
            task_set_facts={"my_var": "new_value"},
        )
        result = rule.process(g, task_id)
        assert result is not None
        assert result.verdict is True


# ---------------------------------------------------------------------------
# L034 — Unused Override
# ---------------------------------------------------------------------------


class TestL034GraphRule:
    """Tests for ``UnusedOverrideGraphRule`` (L034)."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> UnusedOverrideGraphRule:
        """Provide a fresh L034 rule instance.

        Returns:
            A new ``UnusedOverrideGraphRule``.
        """
        return UnusedOverrideGraphRule()

    def test_match_task_with_register(self, rule: UnusedOverrideGraphRule) -> None:
        """Task with register matches.

        Args:
            rule: Rule instance under test.
        """
        g, _, task_id = _build_playbook_play_task(task_register="result")
        assert rule.match(g, task_id)

    def test_no_match_plain_task(self, rule: UnusedOverrideGraphRule) -> None:
        """Task without variable definition does not match.

        Args:
            rule: Rule instance under test.
        """
        g, _, task_id = _build_playbook_play_task()
        assert not rule.match(g, task_id)

    def test_no_existing_def_no_violation(self, rule: UnusedOverrideGraphRule) -> None:
        """Register with no existing definition is clean.

        Args:
            rule: Rule instance under test.
        """
        g, _, task_id = _build_playbook_play_task(task_register="brand_new")
        result = rule.process(g, task_id)
        assert result is not None
        assert result.verdict is False

    def test_override_same_scope_no_violation(self, rule: UnusedOverrideGraphRule) -> None:
        """Two set_facts in the same task scope don't trigger.

        Args:
            rule: Rule instance under test.
        """
        g, _, task_id = _build_playbook_play_task(
            task_module="ansible.builtin.set_fact",
            task_set_facts={"my_var": "new"},
        )
        result = rule.process(g, task_id)
        assert result is not None
        assert result.verdict is False

    def test_match_task_with_vars(self, rule: UnusedOverrideGraphRule) -> None:
        """Task with ``vars:`` section also matches.

        Args:
            rule: Rule instance under test.
        """
        g, _, task_id = _build_playbook_play_task(
            task_options={"vars": {"foo": "bar"}},
        )
        task = g.get_node(task_id)
        assert task is not None
        task.variables = {"foo": "bar"}
        assert rule.match(g, task_id)

    def test_local_vars_shadowed_by_runtime_violation(self, rule: UnusedOverrideGraphRule) -> None:
        """Task-level ``vars:`` shadowed by upstream ``register`` triggers violation.

        An upstream task registers ``result``, which flows to this task
        via ``DATA_FLOW``.  The consumer task also defines ``result``
        in its ``vars:`` (LOCAL, precedence 8).  Since RUNTIME (9) >
        LOCAL (8), the task's definition is ineffective.

        Args:
            rule: Rule instance under test.
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
        producer = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=5,
            module="command",
            register="result",
            scope=NodeScope.OWNED,
        )
        consumer = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[1]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=10,
            module="debug",
            variables={"result": "fallback"},
            scope=NodeScope.OWNED,
        )
        g.add_node(pb)
        g.add_node(play)
        g.add_node(producer)
        g.add_node(consumer)
        g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)
        g.add_edge(play.node_id, producer.node_id, EdgeType.CONTAINS)
        g.add_edge(play.node_id, consumer.node_id, EdgeType.CONTAINS)
        g.add_edge(producer.node_id, consumer.node_id, EdgeType.DATA_FLOW)

        result = rule.process(g, consumer.node_id)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        variables = cast(list[YAMLDict], result.detail["variables"])
        assert any(v["name"] == "result" for v in variables)
        shadowed = next(v for v in variables if v["name"] == "result")
        assert shadowed["local_precedence"] == "local"
        assert shadowed["shadowed_by"] == "runtime"


# ---------------------------------------------------------------------------
# L093 — Set Fact Override
# ---------------------------------------------------------------------------


class TestL093GraphRule:
    """Tests for ``SetFactOverrideGraphRule`` (L093)."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> SetFactOverrideGraphRule:
        """Provide a fresh L093 rule instance.

        Returns:
            A new ``SetFactOverrideGraphRule``.
        """
        return SetFactOverrideGraphRule()

    def _build_role_with_set_fact(
        self,
        *,
        role_defaults: YAMLDict | None = None,
        role_vars: YAMLDict | None = None,
        fact_keys: YAMLDict | None = None,
    ) -> tuple[ContentGraph, str]:
        """Build playbook -> play -> role -> taskfile -> set_fact task.

        ``GraphBuilder`` populates ``ContentNode.set_facts`` (not just
        ``module_options``) for ``set_fact`` tasks, so we set both.

        Args:
            role_defaults: Role default_variables.
            role_vars: Role role_variables.
            fact_keys: Facts set by the set_fact task.

        Returns:
            Tuple of (graph, task_node_id).
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
        role = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/roles[0]", node_type=NodeType.ROLE),
            file_path="roles/myrole/meta/main.yml",
            default_variables=role_defaults or {},
            role_variables=role_vars or {},
            scope=NodeScope.OWNED,
        )
        tf = ContentNode(
            identity=NodeIdentity(
                path="site.yml/plays[0]/roles[0]/tasks/main.yml",
                node_type=NodeType.TASKFILE,
            ),
            file_path="roles/myrole/tasks/main.yml",
            scope=NodeScope.OWNED,
        )
        facts = fact_keys or {}
        task = ContentNode(
            identity=NodeIdentity(
                path="site.yml/plays[0]/roles[0]/tasks/main.yml/tasks[0]",
                node_type=NodeType.TASK,
            ),
            file_path="roles/myrole/tasks/main.yml",
            line_start=5,
            module="set_fact",
            module_options=facts,
            set_facts=facts,
            scope=NodeScope.OWNED,
        )
        g.add_node(pb)
        g.add_node(play)
        g.add_node(role)
        g.add_node(tf)
        g.add_node(task)
        g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)
        g.add_edge(play.node_id, role.node_id, EdgeType.CONTAINS)
        g.add_edge(role.node_id, tf.node_id, EdgeType.CONTAINS)
        g.add_edge(tf.node_id, task.node_id, EdgeType.CONTAINS)
        return g, task.node_id

    def test_match_set_fact_in_role(self, rule: SetFactOverrideGraphRule) -> None:
        """set_fact task inside a role matches.

        Args:
            rule: Rule instance under test.
        """
        g, task_id = self._build_role_with_set_fact(
            role_defaults={"port": 80},
            fact_keys={"port": 8080},
        )
        assert rule.match(g, task_id)

    def test_no_match_set_fact_outside_role(self, rule: SetFactOverrideGraphRule) -> None:
        """set_fact task outside a role does not match.

        Args:
            rule: Rule instance under test.
        """
        g, _, task_id = _build_playbook_play_task(
            task_module="ansible.builtin.set_fact",
            task_module_options={"port": 8080},
        )
        assert not rule.match(g, task_id)

    def test_no_match_non_set_fact(self, rule: SetFactOverrideGraphRule) -> None:
        """Non-set_fact task does not match.

        Args:
            rule: Rule instance under test.
        """
        g, _, task_id = _build_playbook_play_task(task_module="debug")
        assert not rule.match(g, task_id)

    def test_override_role_default_violation(self, rule: SetFactOverrideGraphRule) -> None:
        """set_fact overriding a role default triggers a violation.

        Args:
            rule: Rule instance under test.
        """
        g, task_id = self._build_role_with_set_fact(
            role_defaults={"http_port": 80, "ssl_enabled": False},
            fact_keys={"http_port": 8080},
        )
        result = rule.process(g, task_id)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        overridden = cast(list[str], result.detail["overridden_vars"])
        assert "http_port" in overridden

    def test_override_role_var_violation(self, rule: SetFactOverrideGraphRule) -> None:
        """set_fact overriding a role var triggers a violation.

        Args:
            rule: Rule instance under test.
        """
        g, task_id = self._build_role_with_set_fact(
            role_vars={"config_path": "/etc/app"},
            fact_keys={"config_path": "/opt/app"},
        )
        result = rule.process(g, task_id)
        assert result is not None
        assert result.verdict is True

    def test_no_override_clean(self, rule: SetFactOverrideGraphRule) -> None:
        """set_fact with no overlapping keys is clean.

        Args:
            rule: Rule instance under test.
        """
        g, task_id = self._build_role_with_set_fact(
            role_defaults={"http_port": 80},
            fact_keys={"totally_new": "value"},
        )
        result = rule.process(g, task_id)
        assert result is not None
        assert result.verdict is False

    def test_cacheable_excluded(self, rule: SetFactOverrideGraphRule) -> None:
        """The ``cacheable`` key is excluded from override checks.

        Args:
            rule: Rule instance under test.
        """
        g, task_id = self._build_role_with_set_fact(
            role_defaults={"cacheable": True},
            fact_keys={"cacheable": True},
        )
        result = rule.process(g, task_id)
        assert result is not None
        assert result.verdict is False


# ---------------------------------------------------------------------------
# M005 — Data Tagging
# ---------------------------------------------------------------------------


class TestM005GraphRule:
    """Tests for ``DataTaggingGraphRule`` (M005)."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> DataTaggingGraphRule:
        """Provide a fresh M005 rule instance.

        Returns:
            A new ``DataTaggingGraphRule``.
        """
        return DataTaggingGraphRule()

    def _build_register_then_use(
        self,
        *,
        register_name: str = "cmd_output",
        consumer_options: YAMLDict | None = None,
        consumer_module_options: YAMLDict | None = None,
        use_data_flow_edge: bool = True,
    ) -> tuple[ContentGraph, str]:
        """Build playbook -> play -> [register_task, consumer_task].

        Args:
            register_name: Name of the registered variable.
            consumer_options: Options on the consumer task.
            consumer_module_options: Module options on the consumer task.
            use_data_flow_edge: Whether to add a DATA_FLOW edge.

        Returns:
            Tuple of (graph, consumer_task_node_id).
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
        producer = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=5,
            module="command",
            register=register_name,
            scope=NodeScope.OWNED,
        )
        consumer = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[1]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=10,
            module="debug",
            options=consumer_options or {},
            module_options=consumer_module_options or {},
            scope=NodeScope.OWNED,
        )
        g.add_node(pb)
        g.add_node(play)
        g.add_node(producer)
        g.add_node(consumer)
        g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)
        g.add_edge(play.node_id, producer.node_id, EdgeType.CONTAINS)
        g.add_edge(play.node_id, consumer.node_id, EdgeType.CONTAINS)
        if use_data_flow_edge:
            g.add_edge(producer.node_id, consumer.node_id, EdgeType.DATA_FLOW)
        return g, consumer.node_id

    def test_match_task(self, rule: DataTaggingGraphRule) -> None:
        """Tasks match.

        Args:
            rule: Rule instance under test.
        """
        g, task_id = self._build_register_then_use()
        assert rule.match(g, task_id)

    def test_jinja_ref_to_registered_var_violation(self, rule: DataTaggingGraphRule) -> None:
        """Jinja reference to a registered var triggers a violation.

        Args:
            rule: Rule instance under test.
        """
        g, task_id = self._build_register_then_use(
            register_name="cmd_result",
            consumer_module_options={"msg": "{{ cmd_result.stdout }}"},
        )
        result = rule.process(g, task_id)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        registered = cast(list[str], result.detail["registered_vars"])
        assert "cmd_result" in registered

    def test_no_jinja_ref_no_violation(self, rule: DataTaggingGraphRule) -> None:
        """Task not referencing registered var is clean.

        Args:
            rule: Rule instance under test.
        """
        g, task_id = self._build_register_then_use(
            consumer_module_options={"msg": "hello world"},
        )
        result = rule.process(g, task_id)
        assert result is not None
        assert result.verdict is False

    def test_no_registered_vars_no_violation(self, rule: DataTaggingGraphRule) -> None:
        """Task with no registered vars in scope is clean.

        Args:
            rule: Rule instance under test.
        """
        g, _, task_id = _build_playbook_play_task(
            task_module_options={"msg": "{{ some_var }}"},
        )
        result = rule.process(g, task_id)
        assert result is not None
        assert result.verdict is False

    def test_sibling_fallback(self, rule: DataTaggingGraphRule) -> None:
        """Falls back to sibling scan when no DATA_FLOW edges exist.

        Args:
            rule: Rule instance under test.
        """
        g, task_id = self._build_register_then_use(
            register_name="sib_result",
            consumer_module_options={"msg": "{{ sib_result }}"},
            use_data_flow_edge=False,
        )
        result = rule.process(g, task_id)
        assert result is not None
        assert result.verdict is True

    def test_jinja_ref_in_options(self, rule: DataTaggingGraphRule) -> None:
        """Jinja reference in task options (not module_options) is detected.

        Args:
            rule: Rule instance under test.
        """
        g, task_id = self._build_register_then_use(
            register_name="out",
            consumer_options={"when": "{{ out.rc }} == 0"},
        )
        result = rule.process(g, task_id)
        assert result is not None
        assert result.verdict is True


# ---------------------------------------------------------------------------
# Scanner integration
# ---------------------------------------------------------------------------


class TestScopeAwareGraphScanIntegrationB:
    """Integration tests running scope-aware rules through the scanner."""

    def test_scan_set_fact_override(self) -> None:
        """Scanner picks up L093 violation for set_fact overriding role defaults."""
        rule = SetFactOverrideGraphRule()

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
        role = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/roles[0]", node_type=NodeType.ROLE),
            file_path="roles/myrole/meta/main.yml",
            default_variables={"port": 80},
            scope=NodeScope.OWNED,
        )
        tf = ContentNode(
            identity=NodeIdentity(
                path="site.yml/plays[0]/roles[0]/tasks/main.yml",
                node_type=NodeType.TASKFILE,
            ),
            file_path="roles/myrole/tasks/main.yml",
            scope=NodeScope.OWNED,
        )
        task = ContentNode(
            identity=NodeIdentity(
                path="site.yml/plays[0]/roles[0]/tasks/main.yml/tasks[0]",
                node_type=NodeType.TASK,
            ),
            file_path="roles/myrole/tasks/main.yml",
            line_start=5,
            module="set_fact",
            module_options={"port": 8080},
            set_facts={"port": 8080},
            scope=NodeScope.OWNED,
        )
        g.add_node(pb)
        g.add_node(play)
        g.add_node(role)
        g.add_node(tf)
        g.add_node(task)
        g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)
        g.add_edge(play.node_id, role.node_id, EdgeType.CONTAINS)
        g.add_edge(role.node_id, tf.node_id, EdgeType.CONTAINS)
        g.add_edge(tf.node_id, task.node_id, EdgeType.CONTAINS)

        report = scan(g, [rule])
        all_results = [rr for nr in report.node_results for rr in nr.rule_results]
        assert any(rr.verdict is True for rr in all_results)

    def test_scan_data_tagging(self) -> None:
        """Scanner picks up M005 violation for registered var in Jinja."""
        rule = DataTaggingGraphRule()

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
        producer = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=5,
            module="command",
            register="cmd_out",
            scope=NodeScope.OWNED,
        )
        consumer = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[1]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=10,
            module="debug",
            module_options={"msg": "{{ cmd_out.stdout }}"},
            scope=NodeScope.OWNED,
        )
        g.add_node(pb)
        g.add_node(play)
        g.add_node(producer)
        g.add_node(consumer)
        g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)
        g.add_edge(play.node_id, producer.node_id, EdgeType.CONTAINS)
        g.add_edge(play.node_id, consumer.node_id, EdgeType.CONTAINS)
        g.add_edge(producer.node_id, consumer.node_id, EdgeType.DATA_FLOW)

        report = scan(g, [rule])
        all_results = [rr for nr in report.node_results for rr in nr.rule_results]
        assert any(rr.verdict is True for rr in all_results)
