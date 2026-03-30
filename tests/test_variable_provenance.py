"""Tests for VariableProvenanceResolver, PropertyOrigin, VariableProvenance (ADR-044)."""

from __future__ import annotations

from apme_engine.engine.content_graph import (
    ContentGraph,
    ContentNode,
    EdgeType,
    NodeIdentity,
    NodeType,
)
from apme_engine.engine.variable_provenance import (
    ProvenanceSource,
    VariableProvenanceResolver,
)


def _make_graph_with_vars() -> ContentGraph:
    """Graph with play -> task, play has become and vars, task has local vars.

    Returns:
        ContentGraph with nodes and edges.
    """
    g = ContentGraph()

    play = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
        file_path="site.yml",
        line_start=1,
        line_end=30,
        variables={"app_port": 8080, "app_name": "myapp"},
        become={"become": True, "become_user": "root"},
        environment={"PATH": "/usr/bin"},
    )
    g.add_node(play)

    task = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
        file_path="site.yml",
        line_start=10,
        line_end=15,
        variables={"local_var": "value", "app_port": 9090},
    )
    g.add_node(task)
    g.add_edge("site.yml/plays[0]", "site.yml/plays[0]/tasks[0]", EdgeType.CONTAINS)

    return g


def _make_graph_with_role() -> ContentGraph:
    """Graph with play -> role, role has defaults and vars.

    Returns:
        ContentGraph with nodes and edges.
    """
    g = ContentGraph()

    play = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
        file_path="site.yml",
        variables={"play_var": "pv"},
    )
    g.add_node(play)

    role = ContentNode(
        identity=NodeIdentity(path="roles/web", node_type=NodeType.ROLE),
        file_path="roles/web",
        default_variables={"nginx_port": 80},
        role_variables={"nginx_workers": 4},
    )
    g.add_node(role)
    g.add_edge("site.yml/plays[0]", "roles/web", EdgeType.DEPENDENCY)

    taskfile = ContentNode(
        identity=NodeIdentity(path="roles/web/tasks/main.yml", node_type=NodeType.TASKFILE),
        file_path="roles/web/tasks/main.yml",
    )
    g.add_node(taskfile)
    g.add_edge("roles/web", "roles/web/tasks/main.yml", EdgeType.CONTAINS)

    task = ContentNode(
        identity=NodeIdentity(path="roles/web/tasks/main.yml/tasks[0]", node_type=NodeType.TASK),
        file_path="roles/web/tasks/main.yml",
        line_start=1,
        variables={"task_var": "tv"},
    )
    g.add_node(task)
    g.add_edge("roles/web/tasks/main.yml", "roles/web/tasks/main.yml/tasks[0]", EdgeType.CONTAINS)

    return g


def _make_graph_with_data_flow() -> ContentGraph:
    """Graph with register + set_fact data flow.

    Returns:
        ContentGraph with nodes and edges.
    """
    g = ContentGraph()

    play = ContentNode(
        identity=NodeIdentity(path="play", node_type=NodeType.PLAY),
        file_path="site.yml",
    )
    g.add_node(play)

    producer = ContentNode(
        identity=NodeIdentity(path="play/tasks[0]", node_type=NodeType.TASK),
        file_path="site.yml",
        register="cmd_result",
        set_facts={"deploy_ts": "{{ ansible_date_time.iso8601 }}"},
    )
    g.add_node(producer)
    g.add_edge("play", "play/tasks[0]", EdgeType.CONTAINS)

    consumer = ContentNode(
        identity=NodeIdentity(path="play/tasks[1]", node_type=NodeType.TASK),
        file_path="site.yml",
    )
    g.add_node(consumer)
    g.add_edge("play", "play/tasks[1]", EdgeType.CONTAINS)
    g.add_edge("play/tasks[0]", "play/tasks[1]", EdgeType.DATA_FLOW)

    return g


def _make_graph_with_vars_file() -> ContentGraph:
    """Graph with play -> vars_file.

    Returns:
        ContentGraph with nodes and edges.
    """
    g = ContentGraph()

    play = ContentNode(
        identity=NodeIdentity(path="play", node_type=NodeType.PLAY),
        file_path="site.yml",
    )
    g.add_node(play)

    vf = ContentNode(
        identity=NodeIdentity(path="vars/secrets.yml", node_type=NodeType.VARS_FILE),
        file_path="vars/secrets.yml",
        variables={"db_password": "secret123"},
    )
    g.add_node(vf)
    g.add_edge("play", "vars/secrets.yml", EdgeType.VARS_INCLUDE)

    task = ContentNode(
        identity=NodeIdentity(path="play/tasks[0]", node_type=NodeType.TASK),
        file_path="site.yml",
    )
    g.add_node(task)
    g.add_edge("play", "play/tasks[0]", EdgeType.CONTAINS)

    return g


# ---------------------------------------------------------------------------
# VariableProvenance
# ---------------------------------------------------------------------------


class TestVariableProvenance:
    """Tests for ``VariableProvenanceResolver.resolve_variables``."""

    def test_local_shadows_play(self) -> None:
        """Verify task vars shadow play vars and locals are tagged correctly."""
        g = _make_graph_with_vars()
        resolver = VariableProvenanceResolver(g)
        provs = resolver.resolve_variables("site.yml/plays[0]/tasks[0]")

        assert "local_var" in provs
        assert provs["local_var"].source == ProvenanceSource.LOCAL

        assert "app_port" in provs
        assert provs["app_port"].source == ProvenanceSource.LOCAL
        assert provs["app_port"].value == 9090

        assert "app_name" in provs
        assert provs["app_name"].source == ProvenanceSource.PLAY

    def test_role_vars_and_defaults(self) -> None:
        """Verify role-scoped task resolves task-local variables."""
        g = _make_graph_with_role()
        resolver = VariableProvenanceResolver(g)
        provs = resolver.resolve_variables("roles/web/tasks/main.yml/tasks[0]")

        assert "task_var" in provs
        assert provs["task_var"].source == ProvenanceSource.LOCAL

    def test_runtime_data_flow(self) -> None:
        """Verify register and set_fact flow appear as runtime provenance."""
        g = _make_graph_with_data_flow()
        resolver = VariableProvenanceResolver(g)
        provs = resolver.resolve_variables("play/tasks[1]")

        assert "cmd_result" in provs
        assert provs["cmd_result"].source == ProvenanceSource.RUNTIME
        assert provs["cmd_result"].defining_node_id == "play/tasks[0]"

        assert "deploy_ts" in provs
        assert provs["deploy_ts"].source == ProvenanceSource.RUNTIME

    def test_vars_file_variables(self) -> None:
        """Verify vars_file variables resolve with VARS_FILE source."""
        g = _make_graph_with_vars_file()
        resolver = VariableProvenanceResolver(g)
        provs = resolver.resolve_variables("play/tasks[0]")

        assert "db_password" in provs
        assert provs["db_password"].source == ProvenanceSource.VARS_FILE
        assert provs["db_password"].defining_node_id == "vars/secrets.yml"


# ---------------------------------------------------------------------------
# PropertyOrigin
# ---------------------------------------------------------------------------


class TestPropertyOrigin:
    """Tests for ``VariableProvenanceResolver.resolve_property_origins``."""

    def test_become_origin_from_play(self) -> None:
        """Verify become is attributed to the ancestor play."""
        g = _make_graph_with_vars()
        resolver = VariableProvenanceResolver(g)
        origins = resolver.resolve_property_origins("site.yml/plays[0]/tasks[0]")

        assert "become" in origins
        assert origins["become"].defining_node_id == "site.yml/plays[0]"
        assert origins["become"].value == {"become": True, "become_user": "root"}

    def test_environment_origin_from_play(self) -> None:
        """Verify environment is attributed to the ancestor play."""
        g = _make_graph_with_vars()
        resolver = VariableProvenanceResolver(g)
        origins = resolver.resolve_property_origins("site.yml/plays[0]/tasks[0]")

        assert "environment" in origins
        assert origins["environment"].defining_node_id == "site.yml/plays[0]"

    def test_no_origin_when_not_set(self) -> None:
        """Verify become is absent when no ancestor defines it."""
        g = _make_graph_with_role()
        resolver = VariableProvenanceResolver(g)
        origins = resolver.resolve_property_origins("roles/web/tasks/main.yml/tasks[0]")

        assert "become" not in origins
