"""Tests for graph_opa_payload (ADR-044 Phase 1)."""

from __future__ import annotations

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
