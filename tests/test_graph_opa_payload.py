"""Tests for graph_opa_payload (ADR-044 Phase 1)."""

from __future__ import annotations

from apme_engine.engine.content_graph import (
    ContentGraph,
    ContentNode,
    EdgeType,
    NodeIdentity,
    NodeType,
)
from apme_engine.engine.graph_opa_payload import (
    build_hierarchy_from_graph,
    content_node_to_opa_dict,
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
        ari_key="playbook playbook:site.yml",
    )
    g.add_node(pb)

    play = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
        file_path="site.yml",
        line_start=1,
        line_end=25,
        name="Install web",
        become={"become": True, "become_user": "root"},
        ari_key="play playbook:site.yml#play:[0]",
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
        ari_key="task playbook:site.yml#play:[0]#task:[0]",
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
