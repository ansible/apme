"""Tests for graph_opa_payload (ADR-044 Phase 1)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from apme_engine.engine.graph_opa_payload import (
    build_hierarchy_from_graph,
    content_node_to_opa_dict,
)
from apme_engine.graph.content_graph import (
    ContentGraph,
    ContentNode,
    EdgeType,
    NodeIdentity,
    NodeType,
)
from apme_engine.graph.types import YAMLDict, YAMLValue


def _make_minimal_graph() -> ContentGraph:
    """Build a small graph: playbook -> play -> 2 tasks.

    Returns:
        ContentGraph with nodes and edges.
    """
    g = ContentGraph()

    pb = ContentNode(
        identity=NodeIdentity(path="site.yml", node_type=NodeType.PLAYBOOK),
        file_path="site.yml",
        name="site",
    )
    g.add_node(pb)

    play = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
        file_path="site.yml",
        line_start=1,
        line_end=25,
        name="Install web",
        become={"become": True, "become_user": "root"},
    )
    g.add_node(play)
    g.add_edge("site.yml", "site.yml/plays[0]", EdgeType.CONTAINS)

    task = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
        file_path="site.yml",
        line_start=5,
        line_end=10,
        name="Install nginx",
        module="ansible.builtin.package",
        module_options={"name": "nginx", "state": "present"},
        options={"when": "ansible_os_family == 'Debian'"},
    )
    g.add_node(task)
    g.add_edge("site.yml/plays[0]", "site.yml/plays[0]/tasks[0]", EdgeType.CONTAINS)

    return g


class TestContentNodeToOpaDict:
    """Tests for ``content_node_to_opa_dict``."""

    def test_playcall_shape(self) -> None:
        """Verify playcall nodes map to the expected OPA dict shape."""
        g = _make_minimal_graph()
        play = g.get_node("site.yml/plays[0]")
        assert play is not None
        d = content_node_to_opa_dict(play)

        assert d["type"] == "playcall"
        assert d["name"] == "Install web"
        opts = d["options"]
        assert isinstance(opts, dict)
        assert opts["become"] is True
        line = d["line"]
        assert line == [1, 25]

    def test_taskcall_shape(self) -> None:
        """Verify taskcall nodes map to the expected OPA dict shape."""
        g = _make_minimal_graph()
        task = g.get_node("site.yml/plays[0]/tasks[0]")
        assert task is not None
        d = content_node_to_opa_dict(task)

        assert d["type"] == "taskcall"
        assert d["module"] == "ansible.builtin.package"
        assert d["original_module"] == "ansible.builtin.package"
        assert d["name"] == "Install nginx"
        mo = d["module_options"]
        assert isinstance(mo, dict)
        assert mo["name"] == "nginx"
        topts = d["options"]
        assert isinstance(topts, dict)
        assert topts["when"] == "ansible_os_family == 'Debian'"

    def test_loop_and_with_options_forwarded(self) -> None:
        """Verify loop and with_* options are forwarded to OPA dict."""
        task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=1,
            line_end=5,
            module="ansible.builtin.set_fact",
            module_options={"result": "{{ items }}"},
            options={
                "loop": "{{ all_services }}",
                "when": "item.state == 'running'",
                "with_custom_lookup": "{{ data }}",
                "internal_ansible_field": "should_be_excluded",
            },
        )
        d = content_node_to_opa_dict(task)
        opts = d["options"]
        assert isinstance(opts, dict)
        assert opts["loop"] == "{{ all_services }}"
        assert opts["when"] == "item.state == 'running'"
        assert opts["with_custom_lookup"] == "{{ data }}"
        assert "internal_ansible_field" not in opts

    def test_key_is_node_id(self) -> None:
        """Verify 'key' in OPA dict is always node_id (not ari_key)."""
        g = _make_minimal_graph()

        play = g.get_node("site.yml/plays[0]")
        assert play is not None
        d = content_node_to_opa_dict(play)
        assert d["key"] == "site.yml/plays[0]"

        task = g.get_node("site.yml/plays[0]/tasks[0]")
        assert task is not None
        d = content_node_to_opa_dict(task)
        assert d["key"] == "site.yml/plays[0]/tasks[0]"

    def test_hierarchy_root_key_is_node_id(self) -> None:
        """Verify hierarchy root_key uses node_id."""
        g = _make_minimal_graph()
        payload = build_hierarchy_from_graph(g, scan_type="playbook", scan_name="site")
        hierarchy = payload["hierarchy"]
        assert isinstance(hierarchy, list) and len(hierarchy) > 0
        tree = hierarchy[0]
        assert isinstance(tree, dict)
        assert tree["root_key"] == "site.yml"

    def test_playcall_connection_and_strategy(self) -> None:
        """Verify play-level connection and strategy pass through to OPA options (M018, M025)."""
        play = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
            file_path="site.yml",
            line_start=1,
            line_end=20,
            name="Web play",
            options={"connection": "paramiko_ssh", "strategy": "free"},
        )
        d = content_node_to_opa_dict(play)
        opts = d["options"]
        assert isinstance(opts, dict)
        assert opts["connection"] == "paramiko_ssh"
        assert opts["strategy"] == "free"

    def test_playcall_vars(self) -> None:
        """Verify play-level variables are serialized under options.vars (M018)."""
        play = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
            file_path="site.yml",
            line_start=1,
            line_end=20,
            name="Web play",
            variables={"ansible_connection": "paramiko_ssh", "http_port": 8080},
        )
        d = content_node_to_opa_dict(play)
        opts = d["options"]
        assert isinstance(opts, dict)
        play_vars = opts["vars"]
        assert isinstance(play_vars, dict)
        assert play_vars["ansible_connection"] == "paramiko_ssh"
        assert play_vars["http_port"] == 8080

    def test_taskcall_vars_option(self) -> None:
        """Verify task-level vars pass through to OPA options (M018)."""
        task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=5,
            line_end=10,
            module="ansible.builtin.debug",
            module_options={"msg": "hi"},
            options={"vars": {"ansible_connection": "paramiko_ssh"}},
        )
        d = content_node_to_opa_dict(task)
        opts = d["options"]
        assert isinstance(opts, dict)
        assert opts["vars"] == {"ansible_connection": "paramiko_ssh"}

    def test_playcall_has_roles_has_tasks(self) -> None:
        """Verify has_roles/has_tasks flags are injected for plays with children (L066)."""
        g = ContentGraph()
        pb = ContentNode(
            identity=NodeIdentity(path="site.yml", node_type=NodeType.PLAYBOOK),
            file_path="site.yml",
        )
        g.add_node(pb)

        play = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
            file_path="site.yml",
            line_start=1,
            line_end=30,
            name="Full play",
        )
        g.add_node(play)
        g.add_edge("site.yml", "site.yml/plays[0]", EdgeType.CONTAINS)

        task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=10,
            line_end=15,
            module="ansible.builtin.debug",
        )
        g.add_node(task)
        g.add_edge("site.yml/plays[0]", "site.yml/plays[0]/tasks[0]", EdgeType.CONTAINS)

        role = ContentNode(
            identity=NodeIdentity(path="roles/myrole", node_type=NodeType.ROLE),
            file_path="roles/myrole",
        )
        g.add_node(role)
        g.add_edge("site.yml/plays[0]", "roles/myrole", EdgeType.DEPENDENCY)

        d = content_node_to_opa_dict(play, graph=g)
        opts = d["options"]
        assert isinstance(opts, dict)
        assert opts["roles"] is True
        assert opts["tasks"] is True

    def test_playcall_handlers_do_not_set_tasks(self) -> None:
        """Verify HANDLER children do not set opts['tasks'] (L066 false-positive guard)."""
        g = ContentGraph()
        pb = ContentNode(
            identity=NodeIdentity(path="site.yml", node_type=NodeType.PLAYBOOK),
            file_path="site.yml",
        )
        g.add_node(pb)

        play = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
            file_path="site.yml",
            line_start=1,
            line_end=30,
            name="Roles-only play",
        )
        g.add_node(play)
        g.add_edge("site.yml", "site.yml/plays[0]", EdgeType.CONTAINS)

        role = ContentNode(
            identity=NodeIdentity(path="roles/myrole", node_type=NodeType.ROLE),
            file_path="roles/myrole",
        )
        g.add_node(role)
        g.add_edge("site.yml/plays[0]", "roles/myrole", EdgeType.DEPENDENCY)

        handler = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/handlers[0]", node_type=NodeType.HANDLER),
            file_path="site.yml",
            line_start=20,
            line_end=25,
            module="ansible.builtin.service",
        )
        g.add_node(handler)
        g.add_edge("site.yml/plays[0]", "site.yml/plays[0]/handlers[0]", EdgeType.CONTAINS)

        d = content_node_to_opa_dict(play, graph=g)
        opts = d["options"]
        assert isinstance(opts, dict)
        assert opts["roles"] is True
        assert "tasks" not in opts, "handlers must not set tasks flag"

    def test_playcall_no_roles_no_tasks_flags(self) -> None:
        """Verify roles/tasks absent when play has no children."""
        play = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
            file_path="site.yml",
            line_start=1,
            line_end=5,
            name="Empty play",
        )
        g = ContentGraph()
        g.add_node(play)
        d = content_node_to_opa_dict(play, graph=g)
        opts = d["options"]
        assert isinstance(opts, dict)
        assert "roles" not in opts
        assert "tasks" not in opts

    def test_blockcall_type(self) -> None:
        """Verify BLOCK nodes serialize as 'blockcall' type (L063)."""
        block = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/block[0]", node_type=NodeType.BLOCK),
            file_path="site.yml",
            line_start=5,
            line_end=15,
            name="Security block",
        )
        d = content_node_to_opa_dict(block)
        assert d["type"] == "blockcall"
        assert d["name"] == "Security block"

    def test_raw_params_alias(self) -> None:
        """Verify _raw is aliased to _raw_params for meta tasks (L064)."""
        task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=5,
            line_end=10,
            module="ansible.builtin.meta",
            module_options={"_raw": "flush_handlers"},
        )
        d = content_node_to_opa_dict(task)
        mo = d["module_options"]
        assert isinstance(mo, dict)
        assert mo["_raw"] == "flush_handlers"
        assert mo["_raw_params"] == "flush_handlers"
        assert mo["cmd"] == "flush_handlers"

    def test_raw_params_end_play(self) -> None:
        """Verify _raw_params alias works for end_play (L064 violation target)."""
        task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=5,
            line_end=10,
            module="ansible.builtin.meta",
            module_options={"_raw": "end_play"},
        )
        d = content_node_to_opa_dict(task)
        mo = d["module_options"]
        assert isinstance(mo, dict)
        assert mo["_raw_params"] == "end_play"
        assert mo["cmd"] == "end_play"

    def test_raw_params_preserves_existing(self) -> None:
        """Verify _raw_params is not overwritten when already present."""
        task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=5,
            line_end=10,
            module="ansible.builtin.meta",
            module_options={"_raw": "flush_handlers", "_raw_params": "existing_value"},
        )
        d = content_node_to_opa_dict(task)
        mo = d["module_options"]
        assert isinstance(mo, dict)
        assert mo["_raw_params"] == "existing_value"

    def test_vars_file_returns_empty(self) -> None:
        """Verify VARS_FILE nodes yield an empty dict."""
        node = ContentNode(
            identity=NodeIdentity(path="vars/main.yml", node_type=NodeType.VARS_FILE),
        )
        d = content_node_to_opa_dict(node)
        assert d == {}


class TestBuildHierarchyFromGraph:
    """Tests for ``build_hierarchy_from_graph``."""

    def test_basic_structure(self) -> None:
        """Verify scan payload includes hierarchy, collection_set, and metadata."""
        g = _make_minimal_graph()
        payload = build_hierarchy_from_graph(
            g,
            scan_type="playbook",
            scan_name="site.yml",
            scan_id="test-scan-001",
        )

        assert payload["scan_id"] == "test-scan-001"
        hierarchy = payload["hierarchy"]
        assert isinstance(hierarchy, list)
        assert len(hierarchy) >= 1
        collection_set = payload["collection_set"]
        assert isinstance(collection_set, list)
        meta = payload["metadata"]
        assert isinstance(meta, dict)
        assert meta["type"] == "playbook"

    def test_nodes_in_hierarchy(self) -> None:
        """Verify playbook, play, and task node types appear in the tree."""
        g = _make_minimal_graph()
        payload = build_hierarchy_from_graph(g, scan_type="playbook", scan_name="site")

        hierarchy = payload["hierarchy"]
        assert isinstance(hierarchy, list)
        tree = hierarchy[0]
        assert isinstance(tree, dict)
        assert tree["root_type"] == "playbook"
        nodes = tree["nodes"]
        assert isinstance(nodes, list)
        types: list[str] = []
        for n in nodes:
            assert isinstance(n, dict)
            t = n.get("type")
            assert isinstance(t, str)
            types.append(t)
        assert "playbookcall" in types
        assert "playcall" in types
        assert "taskcall" in types

    def test_empty_graph(self) -> None:
        """Verify an empty graph produces empty hierarchy and collection_set."""
        g = ContentGraph()
        payload = build_hierarchy_from_graph(g, scan_type="role", scan_name="test")
        assert payload["hierarchy"] == []
        assert payload["collection_set"] == []

    def test_collection_extraction(self) -> None:
        """Verify collection_set includes namespaces from task modules."""
        g = ContentGraph()
        pb = ContentNode(
            identity=NodeIdentity(path="site.yml", node_type=NodeType.PLAYBOOK),
            file_path="site.yml",
        )
        g.add_node(pb)
        task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            module="community.general.timezone",
        )
        g.add_node(task)
        g.add_edge("site.yml", "site.yml/plays[0]/tasks[0]", EdgeType.CONTAINS)

        payload = build_hierarchy_from_graph(g, scan_type="playbook", scan_name="site")
        cs = payload["collection_set"]
        assert isinstance(cs, list)
        assert "community.general" in cs


def _defaults_node(graph: ContentGraph, name: str, node_type: NodeType, defaults: YAMLValue = None) -> ContentNode:
    """Add a scope node for defaults transport tests.

    Args:
        graph: Graph to extend.
        name: Stable node ID.
        node_type: Scope or task type.
        defaults: Raw local defaults layer, if supplied.

    Returns:
        The added node.
    """
    node = ContentNode(
        identity=NodeIdentity(path=name, node_type=node_type),
        module="ansible.builtin.file" if node_type in {NodeType.TASK, NodeType.HANDLER} else "",
        options={"module_defaults": defaults} if defaults is not None else {},
    )
    graph.add_node(node)
    return node


def test_defaults_contexts_preserve_order_resets_and_authored_arguments() -> None:
    """Transport raw play, nested block, and task layers without merging args."""
    graph = ContentGraph()
    layers: list[YAMLValue] = [
        {"ansible.builtin.file": {"owner": "root", "mode": "0755"}},
        [{"group/example.testing.files": {"mode": "0644"}}, {"ansible.builtin.file": {"group": "{{ group }}"}}],
        {"ansible.builtin.file": {}},
        {},
    ]
    scopes = [
        _defaults_node(graph, "play", NodeType.PLAY, layers[0]),
        _defaults_node(graph, "outer", NodeType.BLOCK, layers[1]),
        _defaults_node(graph, "inner", NodeType.BLOCK, layers[2]),
        _defaults_node(graph, "task", NodeType.TASK, layers[3]),
    ]
    for parent, child in zip(scopes, scopes[1:], strict=False):
        graph.add_edge(parent.node_id, child.node_id, EdgeType.CONTAINS)
    task = scopes[-1]
    task.module_options = {"path": "/tmp/example"}
    before_options = dict(task.options)
    payload = content_node_to_opa_dict(task, graph)
    assert payload["module_defaults_contexts"] == [layers]
    assert "module_defaults_incomplete" not in payload
    assert payload["module_options"] == {"path": "/tmp/example"}
    assert task.module_options == {"path": "/tmp/example"}
    assert task.options == before_options
    restored = ContentGraph.from_dict(graph.to_dict(slim=True))
    restored_task = restored.get_node(task.node_id)
    assert restored_task is not None
    assert content_node_to_opa_dict(restored_task, restored) == payload


@pytest.mark.parametrize("edge_type", [EdgeType.INCLUDE, EdgeType.IMPORT])  # type: ignore[untyped-decorator]
def test_defaults_contexts_keep_shared_file_invocations_separate(edge_type: EdgeType) -> None:
    """Two calls in one play retain alternatives, including a no-defaults path.

    Args:
        edge_type: Static import or dynamic include relationship.
    """
    graph = ContentGraph()
    play = _defaults_node(graph, "play", NodeType.PLAY)
    shared = _defaults_node(graph, "shared.yml", NodeType.TASKFILE)
    task = _defaults_node(graph, "shared.yml/task", NodeType.TASK)
    first = _defaults_node(graph, "first", NodeType.TASK, {"ansible.builtin.file": {"owner": "first"}})
    second = _defaults_node(graph, "second", NodeType.TASK)
    for caller in (first, second):
        graph.add_edge(play.node_id, caller.node_id, EdgeType.CONTAINS)
        graph.add_edge(caller.node_id, shared.node_id, edge_type)
    graph.add_edge(shared.node_id, task.node_id, EdgeType.CONTAINS)
    assert content_node_to_opa_dict(task, graph)["module_defaults_contexts"] == [
        [{"ansible.builtin.file": {"owner": "first"}}],
        [],
    ]


@pytest.mark.parametrize("module", ["include_tasks", "ansible.builtin.include_role", "ansible.legacy.include_tasks"])  # type: ignore[untyped-decorator]
def test_include_apply_defaults_form_a_descendant_only_block_layer(module: str) -> None:
    """Applied defaults sit after the caller and before the included local scopes.

    Args:
        module: Include module spelling whose apply mapping is serialized.
    """
    graph = ContentGraph()
    play_defaults: YAMLDict = {"file": {"owner": "root"}}
    include_defaults: YAMLDict = {"file": {"group": "caller"}}
    applied: YAMLValue = [{"group/example.testing.files": {"mode": "0644"}}, {"file": {}}]
    task_defaults: YAMLDict = {"file": {"owner": "local"}}
    play = _defaults_node(graph, "play", NodeType.PLAY, play_defaults)
    caller = _defaults_node(graph, "caller", NodeType.TASK, include_defaults)
    caller.module = module
    caller.options["args"] = {"apply": {"module_defaults": applied}}
    target_type = NodeType.ROLE if module.endswith("include_role") else NodeType.TASKFILE
    target = _defaults_node(graph, "target", target_type)
    task = _defaults_node(graph, "task", NodeType.TASK, task_defaults)
    graph.add_edge(play.node_id, caller.node_id, EdgeType.CONTAINS)
    graph.add_edge(caller.node_id, target.node_id, EdgeType.INCLUDE)
    graph.add_edge(target.node_id, task.node_id, EdgeType.CONTAINS)
    assert content_node_to_opa_dict(caller, graph)["module_defaults_contexts"] == [[play_defaults, include_defaults]]
    payload = content_node_to_opa_dict(task, graph)
    assert payload["module_defaults_contexts"] == [[play_defaults, include_defaults, applied, task_defaults]]
    assert "module_defaults_incomplete" not in payload
    assert caller.options["args"] == {"apply": {"module_defaults": applied}}


def test_shared_include_apply_defaults_do_not_leak_between_invocations() -> None:
    """The same task file called twice keeps distinct applied block defaults."""
    graph = ContentGraph()
    target = _defaults_node(graph, "target", NodeType.TASKFILE)
    task = _defaults_node(graph, "task", NodeType.TASK)
    graph.add_edge(target.node_id, task.node_id, EdgeType.CONTAINS)
    for name in ("first", "second"):
        caller = _defaults_node(graph, name, NodeType.TASK)
        caller.module = "ansible.builtin.include_tasks"
        caller.module_options = {"file": "target", "apply": {"module_defaults": {"file": {"owner": name}}}}
        graph.add_edge(caller.node_id, target.node_id, EdgeType.INCLUDE)
    assert content_node_to_opa_dict(task, graph)["module_defaults_contexts"] == [
        [{"file": {"owner": "first"}}],
        [{"file": {"owner": "second"}}],
    ]


@pytest.mark.parametrize("apply", ["{{ applied_attributes }}", None])  # type: ignore[untyped-decorator]
def test_unavailable_apply_marks_descendants_incomplete(apply: YAMLValue) -> None:
    """Unknown apply data cannot silently produce false missing-argument findings.

    Args:
        apply: Apply mapping that cannot be inspected statically.
    """
    graph = ContentGraph()
    caller = _defaults_node(graph, "caller", NodeType.TASK)
    caller.module = "ansible.builtin.include_role"
    caller.module_options = {"apply": apply}
    role = _defaults_node(graph, "role", NodeType.ROLE)
    task = _defaults_node(graph, "task", NodeType.TASK)
    graph.add_edge(caller.node_id, role.node_id, EdgeType.INCLUDE)
    graph.add_edge(role.node_id, task.node_id, EdgeType.CONTAINS)
    assert content_node_to_opa_dict(task, graph)["module_defaults_incomplete"] is True
    assert "module_defaults_incomplete" not in content_node_to_opa_dict(caller, graph)


def test_include_templated_args_mark_unknown_applied_defaults_incomplete() -> None:
    """Templated include args could supply apply and require explicit coverage."""
    graph = ContentGraph()
    caller = _defaults_node(graph, "caller", NodeType.TASK)
    caller.module = "include_tasks"
    caller.options["args"] = "{{ include_arguments }}"
    task = _defaults_node(graph, "task", NodeType.TASK)
    graph.add_edge(caller.node_id, task.node_id, EdgeType.INCLUDE)
    assert content_node_to_opa_dict(task, graph)["module_defaults_incomplete"] is True


@pytest.mark.parametrize("edge_type", [EdgeType.DEPENDENCY, EdgeType.INCLUDE, EdgeType.IMPORT])  # type: ignore[untyped-decorator]
def test_defaults_contexts_follow_roles_across_plays(edge_type: EdgeType) -> None:
    """Role and dependent-role tasks keep each play's inherited defaults.

    Args:
        edge_type: Static role, included role, or imported role relationship.
    """
    graph = ContentGraph()
    role = _defaults_node(graph, "role", NodeType.ROLE)
    dependency = _defaults_node(graph, "dependency", NodeType.ROLE)
    handler = _defaults_node(graph, "handler", NodeType.HANDLER)
    graph.add_edge(role.node_id, dependency.node_id, EdgeType.DEPENDENCY)
    graph.add_edge(dependency.node_id, handler.node_id, EdgeType.CONTAINS)
    for name in ("first", "second"):
        play = _defaults_node(graph, name, NodeType.PLAY, {"ansible.builtin.file": {"owner": name}})
        if edge_type == EdgeType.DEPENDENCY:
            graph.add_edge(play.node_id, role.node_id, edge_type)
        else:
            caller = _defaults_node(graph, name + "/caller", NodeType.TASK)
            graph.add_edge(play.node_id, caller.node_id, EdgeType.CONTAINS)
            graph.add_edge(caller.node_id, role.node_id, edge_type)
    assert content_node_to_opa_dict(handler, graph)["module_defaults_contexts"] == [
        [{"ansible.builtin.file": {"owner": "first"}}],
        [{"ansible.builtin.file": {"owner": "second"}}],
    ]


def test_defaults_contexts_ignore_nonpositional_edges_and_duplicate_section_edges() -> None:
    """Notifications and data flow cannot inject layers or create false cycles."""
    graph = ContentGraph()
    block = _defaults_node(graph, "block", NodeType.BLOCK, {"file": {"owner": "block"}})
    task = _defaults_node(graph, "task", NodeType.TASK)
    unrelated = _defaults_node(graph, "unrelated", NodeType.TASK, {"file": {"owner": "unrelated"}})
    graph.add_edge(block.node_id, task.node_id, EdgeType.CONTAINS)
    graph.add_edge(block.node_id, task.node_id, EdgeType.RESCUE)
    graph.add_edge(block.node_id, task.node_id, EdgeType.ALWAYS)
    for edge_type in (EdgeType.NOTIFY, EdgeType.DATA_FLOW, EdgeType.INVOKES, EdgeType.DEPENDENCY):
        graph.add_edge(unrelated.node_id, task.node_id, edge_type)
        graph.add_edge(task.node_id, unrelated.node_id, edge_type)
    payload = content_node_to_opa_dict(task, graph)
    assert payload["module_defaults_contexts"] == [[{"file": {"owner": "block"}}]]
    assert "module_defaults_incomplete" not in payload


@pytest.mark.parametrize("count", [64, 65])  # type: ignore[untyped-decorator]
def test_defaults_context_cap_propagates_incompleteness(count: int) -> None:
    """Only distinct contexts consume the budget and truncation reaches children.

    Args:
        count: Number of distinct inherited defaults alternatives.
    """
    graph = ContentGraph()
    shared = _defaults_node(graph, "shared", NodeType.TASKFILE)
    task = _defaults_node(graph, "task", NodeType.TASK, {"file": {}})
    graph.add_edge(shared.node_id, task.node_id, EdgeType.CONTAINS)
    for index in range(count):
        parent = _defaults_node(graph, f"parent-{index:02}", NodeType.PLAY, {"file": {"owner": index}})
        graph.add_edge(parent.node_id, shared.node_id, EdgeType.INCLUDE)
    # Duplicate positional paths do not use additional context capacity.
    graph.add_edge("parent-00", shared.node_id, EdgeType.IMPORT)
    payload = content_node_to_opa_dict(task, graph)
    contexts = payload["module_defaults_contexts"]
    assert isinstance(contexts, list) and len(contexts) == 64
    assert payload.get("module_defaults_incomplete", False) is (count > 64)
    assert all(isinstance(context, list) and context[-1] == {"file": {}} for context in contexts)


def test_defaults_context_identity_retains_group_mapping_order() -> None:
    """Equal mappings with different group order remain distinct raw contexts."""
    graph = ContentGraph()
    task = _defaults_node(graph, "task", NodeType.TASK)
    groups: YAMLDict = {
        "group/example.testing.first": {"mode": "0644"},
        "group/example.testing.second": {"mode": "0755"},
    }
    for name, defaults in (("first", groups), ("second", dict(reversed(list(groups.items()))))):
        parent = _defaults_node(graph, name, NodeType.PLAY, defaults)
        graph.add_edge(parent.node_id, task.node_id, EdgeType.CONTAINS)
    contexts = content_node_to_opa_dict(task, graph)["module_defaults_contexts"]
    assert isinstance(contexts, list) and len(contexts) == 2
    for context, expected in zip(contexts, (list(groups), list(reversed(groups))), strict=True):
        assert isinstance(context, list) and isinstance(context[0], dict)
        assert list(context[0]) == expected


def test_defaults_contexts_deduplicate_equivalent_invocations_before_capping() -> None:
    """Many calls with identical raw layers stay complete and use one context."""
    graph = ContentGraph()
    task = _defaults_node(graph, "task", NodeType.TASK)
    for index in range(100):
        parent = _defaults_node(graph, f"parent-{index:03}", NodeType.PLAY, {"file": {"owner": "same"}})
        graph.add_edge(parent.node_id, task.node_id, EdgeType.CONTAINS)
    payload = content_node_to_opa_dict(task, graph)
    assert payload["module_defaults_contexts"] == [[{"file": {"owner": "same"}}]]
    assert "module_defaults_incomplete" not in payload


def test_defaults_context_cycles_are_incomplete_and_standalone_keeps_local_layer() -> None:
    """Cycles cannot fabricate complete ancestry; standalone tasks retain local data."""
    graph = ContentGraph()
    task = _defaults_node(graph, "task", NodeType.TASK, [{"file": {"mode": "0644"}}, {"file": {}}])
    parent = _defaults_node(graph, "parent", NodeType.BLOCK)
    graph.add_edge(parent.node_id, task.node_id, EdgeType.CONTAINS)
    graph.add_edge(task.node_id, parent.node_id, EdgeType.INCLUDE)
    payload = content_node_to_opa_dict(task, graph)
    assert payload["module_defaults_contexts"] == []
    assert payload["module_defaults_incomplete"] is True
    assert "module_defaults_inactive" not in payload
    assert content_node_to_opa_dict(task)["module_defaults_contexts"] == [[task.options["module_defaults"]]]


@pytest.mark.parametrize("depth", [256, 257])  # type: ignore[untyped-decorator]
def test_defaults_layer_depth_is_bounded_before_path_copying(depth: int) -> None:
    """Deep positional paths cannot accumulate unbounded layer tuples.

    Args:
        depth: Number of raw defaults layers along the positional path.
    """
    graph = ContentGraph()
    parent = None
    for index in range(depth):
        block = _defaults_node(graph, f"block-{index}", NodeType.BLOCK, {"file": {"owner": "root"}})
        if parent is not None:
            graph.add_edge(parent.node_id, block.node_id, EdgeType.CONTAINS)
        parent = block
    task = _defaults_node(graph, "task", NodeType.TASK)
    assert parent is not None
    graph.add_edge(parent.node_id, task.node_id, EdgeType.CONTAINS)
    payload = content_node_to_opa_dict(task, graph)
    if depth == 256:
        contexts = payload["module_defaults_contexts"]
        assert isinstance(contexts, list) and isinstance(contexts[0], list) and len(contexts[0]) == depth
        assert "module_defaults_incomplete" not in payload
    else:
        assert payload["module_defaults_incomplete"] is True
        assert payload["module_defaults_contexts"] == []
        assert "module_defaults_inactive" not in payload


@pytest.mark.parametrize("scope", ["play", "task", "apply"])  # type: ignore[untyped-decorator]
def test_deep_raw_defaults_become_incomplete_without_crashing(scope: str) -> None:
    """Recursive conversion failures cannot abort hierarchy argument context.

    Args:
        scope: Location containing an excessively deep raw defaults value.
    """
    graph = ContentGraph()
    raw: YAMLValue = {}
    for _index in range(2000):
        raw = {"nested": raw}
    task = _defaults_node(graph, "task", NodeType.TASK, raw if scope == "task" else None)
    if scope == "play":
        parent = _defaults_node(graph, "play", NodeType.PLAY, raw)
        graph.add_edge(parent.node_id, task.node_id, EdgeType.CONTAINS)
    elif scope == "apply":
        parent = _defaults_node(graph, "caller", NodeType.TASK)
        parent.module = "include_tasks"
        parent.module_options = {"apply": {"module_defaults": raw}}
        graph.add_edge(parent.node_id, task.node_id, EdgeType.INCLUDE)
    assert content_node_to_opa_dict(task, graph)["module_defaults_incomplete"] is True
    if scope == "task":
        assert content_node_to_opa_dict(task)["module_defaults_incomplete"] is True


def test_incomplete_context_takes_precedence_over_inactive_inventory() -> None:
    """Conflicting markers must report incomplete coverage instead of skipping it."""
    from pathlib import Path

    from apme_engine.validators.ansible.module_defaults import resolve_module_defaults

    task: dict[str, object] = {
        "module": "file",
        "module_options": {},
        "module_defaults_contexts": [],
        "module_defaults_incomplete": True,
        "module_defaults_inactive": True,
    }
    with patch("apme_engine.validators.ansible.module_defaults._resolve", side_effect=AssertionError):
        result = resolve_module_defaults([task], Path("/unused-runtime"))
    assert result.tasks == []
    assert len(result.violations) == 1
    assert "incomplete" in str(result.violations[0]["message"])


def test_full_hierarchy_reuses_one_defaults_context_cache() -> None:
    """All roots share one positional DP pass and agree with standalone graph calls."""
    from apme_engine.engine import graph_opa_payload

    graph = _make_minimal_graph()
    second_root = _defaults_node(graph, "other.yml", NodeType.PLAYBOOK)
    task = graph.get_node("site.yml/plays[0]/tasks[0]")
    assert task is not None
    graph.add_edge(second_root.node_id, task.node_id, EdgeType.IMPORT)
    expected = content_node_to_opa_dict(task, graph)
    with (
        patch.object(
            graph_opa_payload,
            "_build_module_defaults_contexts",
            wraps=graph_opa_payload._build_module_defaults_contexts,
        ) as build,
        patch.object(graph, "positional_ancestors", side_effect=AssertionError("No repeated ancestry walks")),
    ):
        payload = build_hierarchy_from_graph(graph, scan_type="project", scan_name="example")
    assert build.call_count == 1
    with patch.object(
        graph_opa_payload,
        "_build_module_defaults_contexts",
        wraps=graph_opa_payload._build_module_defaults_contexts,
    ) as batch_build:
        batch = graph_opa_payload.content_nodes_to_opa_dicts([task, second_root], graph)
    assert batch_build.call_count == 1
    assert batch[0] == expected
    hierarchy = payload["hierarchy"]
    assert isinstance(hierarchy, list) and len(hierarchy) == 2
    for tree in hierarchy:
        assert isinstance(tree, dict) and isinstance(tree["nodes"], list)
        tasks = [node for node in tree["nodes"] if isinstance(node, dict) and node.get("key") == task.node_id]
        assert tasks == [expected]
