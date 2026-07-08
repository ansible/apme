"""Unit tests for GraphRule L039: undefined variable references."""

from __future__ import annotations

from typing import cast

from apme_engine.engine.models import YAMLDict
from apme_engine.graph.content_graph import (
    ContentGraph,
    ContentNode,
    EdgeType,
    NodeIdentity,
    NodeScope,
    NodeType,
)
from apme_engine.graph.rules.L039_undefined_variable_graph import (
    UndefinedVariableGraphRule,
)
from apme_engine.graph.scanner import scan


def _make_graph(
    *,
    play_vars: YAMLDict | None = None,
    task_module: str = "ansible.builtin.debug",
    task_module_options: YAMLDict | None = None,
    task_when: str | list[str] | None = None,
    task_name: str | None = None,
    task_register: str | None = None,
    task_changed_when: str | None = None,
    task_environment: YAMLDict | None = None,
    task_variables: YAMLDict | None = None,
) -> tuple[ContentGraph, str]:
    """Build a playbook > play > task graph for L039 testing.

    Args:
        play_vars: Play-level variables.
        task_module: Module name on the task.
        task_module_options: Module arguments.
        task_when: When expression(s).
        task_name: Task name.
        task_register: Register name.
        task_changed_when: changed_when expression.
        task_environment: Environment dict.
        task_variables: Task-level vars.

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
        line_start=1,
        variables=play_vars or {},
        scope=NodeScope.OWNED,
    )
    task = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
        file_path="site.yml",
        line_start=10,
        module=task_module,
        module_options=task_module_options or {},
        when_expr=task_when,
        name=task_name,
        register=task_register,
        changed_when=task_changed_when,
        environment=task_environment,
        variables=task_variables or {},
        scope=NodeScope.OWNED,
    )
    g.add_node(pb)
    g.add_node(play)
    g.add_node(task)
    g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)
    g.add_edge(play.node_id, task.node_id, EdgeType.CONTAINS)
    return g, task.node_id


def _make_two_tasks(
    *,
    play_vars: YAMLDict | None = None,
    t1_module: str = "ansible.builtin.command",
    t1_register: str | None = None,
    t1_set_facts: YAMLDict | None = None,
    t2_module_options: YAMLDict | None = None,
    t2_when: str | None = None,
) -> tuple[ContentGraph, str]:
    """Build a graph with two sequential tasks for data-flow testing.

    Args:
        play_vars: Play-level variables.
        t1_module: Module for task 1.
        t1_register: Register name on task 1.
        t1_set_facts: set_facts on task 1.
        t2_module_options: Module options on task 2.
        t2_when: When expression on task 2.

    Returns:
        Tuple of (graph, task2_node_id).
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
        variables=play_vars or {},
        scope=NodeScope.OWNED,
    )
    t1 = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
        file_path="site.yml",
        line_start=5,
        module=t1_module,
        register=t1_register,
        set_facts=t1_set_facts or {},
        scope=NodeScope.OWNED,
    )
    t2 = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[1]", node_type=NodeType.TASK),
        file_path="site.yml",
        line_start=10,
        module="ansible.builtin.debug",
        module_options=t2_module_options or {},
        when_expr=t2_when,
        scope=NodeScope.OWNED,
    )
    g.add_node(pb)
    g.add_node(play)
    g.add_node(t1)
    g.add_node(t2)
    g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)
    g.add_edge(play.node_id, t1.node_id, EdgeType.CONTAINS, position=0)
    g.add_edge(play.node_id, t2.node_id, EdgeType.CONTAINS, position=1)
    g.add_edge(t1.node_id, t2.node_id, EdgeType.DATA_FLOW)
    return g, t2.node_id


def _run(graph: ContentGraph) -> list[dict[str, object]]:
    """Run L039 against a graph and return violation details.

    Args:
        graph: ContentGraph to scan.

    Returns:
        List of result detail dicts for L039 violations.
    """
    rule = UndefinedVariableGraphRule()
    report = scan(graph, [rule], owned_only=False)
    results: list[dict[str, object]] = []
    for nr in report.node_results:
        for rr in nr.rule_results:
            if rr.verdict and rr.detail:
                results.append(dict(rr.detail))
    return results


def _undef_vars(results: list[dict[str, object]], idx: int = 0) -> list[str]:
    """Extract the undefined_vars list from the result at the given index.

    Args:
        results: Violation result list.
        idx: Index into the list.

    Returns:
        List of undefined variable names.
    """
    return cast(list[str], results[idx]["undefined_vars"])


# ---- Undefined variable detection ----


class TestUndefined:
    """L039 fires when a Jinja reference has no visible definition."""

    def test_undefined_in_module_options(self) -> None:
        """Task referencing an undefined variable in module_options triggers L039.

        Tests:
            ``never_defined_var_xyz`` has no definition in scope.
        """
        g, _ = _make_graph(
            task_module_options={"msg": "{{ never_defined_var_xyz }}"},
        )
        results = _run(g)
        assert len(results) == 1
        assert "never_defined_var_xyz" in _undef_vars(results)

    def test_undefined_in_when(self) -> None:
        """Task referencing an undefined variable in when triggers L039.

        Tests:
            ``some_flag`` has no definition in scope.
        """
        g, _ = _make_graph(task_when="some_flag | bool")
        results = _run(g)
        assert len(results) == 1
        assert "some_flag" in _undef_vars(results)

    def test_undefined_in_name(self) -> None:
        """Task name containing an undefined Jinja reference triggers L039.

        Tests:
            ``target_host`` has no definition in scope.
        """
        g, _ = _make_graph(task_name="Deploy to {{ target_host }}")
        results = _run(g)
        assert len(results) == 1
        assert "target_host" in _undef_vars(results)


# ---- Defined variable (should NOT fire) ----


class TestDefined:
    """L039 should not fire when variables are resolvable in scope."""

    def test_play_var_defined(self) -> None:
        """Task referencing a play-level variable does not trigger L039.

        Tests:
            ``my_var`` is defined in play vars.
        """
        g, _ = _make_graph(
            play_vars={"my_var": "value"},
            task_module_options={"msg": "{{ my_var }}"},
        )
        results = _run(g)
        assert results == []

    def test_task_var_defined(self) -> None:
        """Task referencing its own task-level variable does not trigger L039.

        Tests:
            ``local_var`` is in the task's ``variables``.
        """
        g, _ = _make_graph(
            task_variables={"local_var": "x"},
            task_module_options={"msg": "{{ local_var }}"},
        )
        results = _run(g)
        assert results == []

    def test_registered_var(self) -> None:
        """Task referencing a registered variable from a prior task passes.

        Tests:
            ``cmd_result`` is registered by task 1, referenced in task 2's when.
        """
        g, _ = _make_two_tasks(
            t1_register="cmd_result",
            t2_when="cmd_result.rc == 0",
        )
        results = _run(g)
        assert results == []

    def test_set_fact_var(self) -> None:
        """Task referencing a set_fact from a prior task passes.

        Tests:
            ``computed_value`` is set by task 1, referenced in task 2's msg.
        """
        g, _ = _make_two_tasks(
            t1_set_facts={"computed_value": "42"},
            t2_module_options={"msg": "{{ computed_value }}"},
        )
        results = _run(g)
        assert results == []


# ---- Magic / special variables ----


class TestMagicVars:
    """L039 should not fire on Ansible magic/special variables."""

    def test_inventory_hostname(self) -> None:
        """Magic variable ``inventory_hostname`` is never flagged.

        Tests:
            ``inventory_hostname`` is in the built-in allowlist.
        """
        g, _ = _make_graph(
            task_module_options={"msg": "{{ inventory_hostname }}"},
        )
        results = _run(g)
        assert results == []

    def test_ansible_prefixed_fact(self) -> None:
        """Any ``ansible_*`` variable is treated as a potential fact.

        Tests:
            ``ansible_os_family`` and other ``ansible_*`` are allowlisted.
        """
        g, _ = _make_graph(
            task_when="ansible_os_family == 'RedHat'",
        )
        results = _run(g)
        assert results == []

    def test_item_in_loop_context(self) -> None:
        """Loop variable ``item`` is a magic variable.

        Tests:
            ``item`` is allowlisted regardless of loop presence.
        """
        g, _ = _make_graph(
            task_module_options={"msg": "{{ item }}"},
        )
        results = _run(g)
        assert results == []


# ---- changed_when / environment ----


class TestExtraFields:
    """L039 scans changed_when and environment fields."""

    def test_undefined_in_changed_when(self) -> None:
        """Undefined variable in changed_when triggers L039.

        Tests:
            ``deploy_status`` is not defined anywhere.
        """
        g, _ = _make_graph(task_changed_when="{{ deploy_status }} == 0")
        results = _run(g)
        assert len(results) == 1
        assert "deploy_status" in _undef_vars(results)

    def test_undefined_in_environment(self) -> None:
        """Undefined variable in environment triggers L039.

        Tests:
            ``api_key`` is not defined anywhere.
        """
        g, _ = _make_graph(
            task_environment={"API_KEY": "{{ api_key }}"},
        )
        results = _run(g)
        assert len(results) == 1
        assert "api_key" in _undef_vars(results)


# ---- No Jinja ----


class TestNoJinja:
    """Tasks with no Jinja references should not trigger L039."""

    def test_plain_task(self) -> None:
        """Task with no Jinja expressions produces no violation.

        Tests:
            Static strings have no references to check.
        """
        g, _ = _make_graph(
            task_module_options={"msg": "hello world"},
        )
        results = _run(g)
        assert results == []
