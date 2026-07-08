"""Unit tests for GraphRule L050: variable names lowercase + underscores."""

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
from apme_engine.graph.rules.L050_var_naming_graph import (
    VarNamingGraphRule,
)
from apme_engine.graph.scanner import scan


def _make_task(
    *,
    module: str = "ansible.builtin.debug",
    module_options: YAMLDict | None = None,
    variables: YAMLDict | None = None,
    register: str | None = None,
    path: str = "site.yml/plays[0]/tasks[0]",
) -> tuple[ContentGraph, str]:
    """Build a minimal playbook > play > task graph.

    Args:
        module: Module name.
        module_options: Module arguments.
        variables: Task-level vars.
        register: Register variable name.
        path: YAML path identity for the task node.

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
    task = ContentNode(
        identity=NodeIdentity(path=path, node_type=NodeType.TASK),
        file_path="site.yml",
        line_start=10,
        module=module,
        module_options=module_options or {},
        variables=variables or {},
        register=register,
        scope=NodeScope.OWNED,
    )
    g.add_node(pb)
    g.add_node(play)
    g.add_node(task)
    g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)
    g.add_edge(play.node_id, task.node_id, EdgeType.CONTAINS)
    return g, task.node_id


def _make_play(
    *,
    variables: YAMLDict | None = None,
) -> tuple[ContentGraph, str]:
    """Build a minimal playbook > play graph.

    Args:
        variables: Play-level vars.

    Returns:
        Tuple of (graph, play_node_id).
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
        variables=variables or {},
        scope=NodeScope.OWNED,
    )
    g.add_node(pb)
    g.add_node(play)
    g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)
    return g, play.node_id


def _make_role(
    *,
    default_variables: YAMLDict | None = None,
    role_variables: YAMLDict | None = None,
) -> tuple[ContentGraph, str]:
    """Build a minimal graph with a role node.

    Args:
        default_variables: Role defaults/main vars.
        role_variables: Role vars/main vars.

    Returns:
        Tuple of (graph, role_node_id).
    """
    g = ContentGraph()
    pb = ContentNode(
        identity=NodeIdentity(path="site.yml", node_type=NodeType.PLAYBOOK),
        file_path="site.yml",
        scope=NodeScope.OWNED,
    )
    role = ContentNode(
        identity=NodeIdentity(path="roles/myrole", node_type=NodeType.ROLE),
        file_path="roles/myrole/tasks/main.yml",
        line_start=1,
        default_variables=default_variables or {},
        role_variables=role_variables or {},
        scope=NodeScope.OWNED,
    )
    g.add_node(pb)
    g.add_node(role)
    g.add_edge(pb.node_id, role.node_id, EdgeType.DEPENDENCY)
    return g, role.node_id


def _run(graph: ContentGraph) -> list[dict[str, object]]:
    """Run L050 against a graph and return violation details.

    Args:
        graph: ContentGraph to scan.

    Returns:
        List of result detail dicts for L050 violations.
    """
    rule = VarNamingGraphRule()
    report = scan(graph, [rule], owned_only=False)
    results: list[dict[str, object]] = []
    for nr in report.node_results:
        for rr in nr.rule_results:
            if rr.verdict and rr.detail:
                results.append(dict(rr.detail))
    return results


def _bad_names(results: list[dict[str, object]], idx: int = 0) -> list[str]:
    """Extract the bad_names list from the result at the given index.

    Args:
        results: Violation result list.
        idx: Index into the list.

    Returns:
        List of bad variable names.
    """
    return cast(list[str], results[idx]["bad_names"])


# ---- Play-level vars ----


class TestPlayVars:
    """L050 on play-level variable definitions."""

    def test_play_vars_bad_names(self) -> None:
        """Play vars with non-lowercase names trigger L050.

        Tests:
            CamelCase and Mixed_Case names produce violations.
        """
        g, _ = _make_play(variables={"MyAppVersion": "2.1", "Server_Port": 8080})
        results = _run(g)
        assert len(results) == 1
        bad = _bad_names(results)
        assert "MyAppVersion" in bad
        assert "Server_Port" in bad

    def test_play_vars_good_names(self) -> None:
        """Play vars with valid names produce no violations.

        Tests:
            lowercase and underscore names pass.
        """
        g, _ = _make_play(variables={"my_app_version": "2.1", "server_port": 8080})
        results = _run(g)
        assert results == []


# ---- Task-level: set_fact ----


class TestSetFact:
    """L050 on set_fact module arguments."""

    def test_set_fact_bad_name(self) -> None:
        """set_fact with CamelCase key triggers L050.

        Tests:
            A single bad key is reported.
        """
        g, _ = _make_task(
            module="ansible.builtin.set_fact",
            module_options={"MyFact": "value"},
        )
        results = _run(g)
        assert len(results) == 1
        assert "MyFact" in _bad_names(results)

    def test_set_fact_good_name(self) -> None:
        """set_fact with valid key produces no violation.

        Tests:
            lowercase key passes.
        """
        g, _ = _make_task(
            module="ansible.builtin.set_fact",
            module_options={"my_fact": "value"},
        )
        results = _run(g)
        assert results == []

    def test_set_fact_cacheable_ignored(self) -> None:
        """The ``cacheable`` meta-key is not checked.

        Tests:
            cacheable is skipped even though it would pass anyway.
        """
        g, _ = _make_task(
            module="set_fact",
            module_options={"cacheable": True, "GoodFact": "x"},
        )
        results = _run(g)
        assert len(results) == 1
        bad = _bad_names(results)
        assert "cacheable" not in bad
        assert "GoodFact" in bad


# ---- Task-level: include_vars ----


class TestIncludeVars:
    """L050 on include_vars name parameter."""

    def test_include_vars_bad_name(self) -> None:
        """include_vars with CamelCase name parameter triggers L050.

        Tests:
            The ``name`` argument value is checked.
        """
        g, _ = _make_task(
            module="ansible.builtin.include_vars",
            module_options={"file": "vars.yml", "name": "MyVars"},
        )
        results = _run(g)
        assert len(results) == 1
        assert "MyVars" in _bad_names(results)


# ---- Task-level: register ----


class TestRegister:
    """L050 on register variable names."""

    def test_register_bad_name(self) -> None:
        """Register with CamelCase name triggers L050.

        Tests:
            Register names are checked.
        """
        g, _ = _make_task(register="MyResult")
        results = _run(g)
        assert len(results) == 1
        assert "MyResult" in _bad_names(results)

    def test_register_good_name(self) -> None:
        """Register with valid name produces no violation.

        Tests:
            lowercase register passes.
        """
        g, _ = _make_task(register="my_result")
        results = _run(g)
        assert results == []


# ---- Role-level ----


class TestRoleVars:
    """L050 on role default and role variables."""

    def test_role_defaults_bad_name(self) -> None:
        """Role defaults with CamelCase key triggers L050.

        Tests:
            default_variables keys are checked.
        """
        g, _ = _make_role(default_variables={"DefaultPort": 80})
        results = _run(g)
        assert len(results) == 1
        assert "DefaultPort" in _bad_names(results)

    def test_role_vars_bad_name(self) -> None:
        """Role vars with CamelCase key triggers L050.

        Tests:
            role_variables keys are checked.
        """
        g, _ = _make_role(role_variables={"AppConfig": "/etc/app"})
        results = _run(g)
        assert len(results) == 1
        assert "AppConfig" in _bad_names(results)

    def test_role_all_good(self) -> None:
        """Role with valid variable names produces no violation.

        Tests:
            Both defaults and vars pass.
        """
        g, _ = _make_role(
            default_variables={"default_port": 80},
            role_variables={"app_config": "/etc/app"},
        )
        results = _run(g)
        assert results == []


# ---- Mixed ----


class TestMixed:
    """L050 with multiple violation sources on a single node."""

    def test_task_vars_and_register(self) -> None:
        """Task with bad vars and bad register produces one violation with both names.

        Tests:
            Multiple sources are merged into one result.
        """
        g, _ = _make_task(
            variables={"BadVar": "x"},
            register="BadResult",
        )
        results = _run(g)
        assert len(results) == 1
        bad = _bad_names(results)
        assert "BadResult" in bad
        assert "BadVar" in bad
