"""Tests for ContentGraph, ContentNode, NodeIdentity, and GraphBuilder (ADR-044 Phase 1)."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from apme_engine.engine.content_graph import (
    ContentGraph,
    ContentNode,
    EdgeType,
    GraphBuilder,
    NodeIdentity,
    NodeScope,
    NodeType,
)

# ---------------------------------------------------------------------------
# NodeIdentity
# ---------------------------------------------------------------------------


class TestNodeIdentity:
    """Tests for ``NodeIdentity`` path and identity semantics."""

    def test_str_representation(self) -> None:
        """Verify string representation matches node path."""
        nid = NodeIdentity(path="site.yml/plays[0]/tasks[1]", node_type=NodeType.TASK)
        assert str(nid) == "site.yml/plays[0]/tasks[1]"

    def test_parent_path(self) -> None:
        """Verify parent path strips the final path segment."""
        nid = NodeIdentity(path="site.yml/plays[0]/tasks[1]", node_type=NodeType.TASK)
        assert nid.parent_path == "site.yml/plays[0]"

    def test_parent_path_root(self) -> None:
        """Verify root playbook identity has no parent path."""
        nid = NodeIdentity(path="site.yml", node_type=NodeType.PLAYBOOK)
        assert nid.parent_path is None

    def test_equality(self) -> None:
        """Verify equal identities compare equal and hash consistently."""
        a = NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY)
        b = NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY)
        assert a == b
        assert hash(a) == hash(b)

    def test_frozen(self) -> None:
        """Verify ``NodeIdentity`` instances are immutable."""
        nid = NodeIdentity(path="site.yml", node_type=NodeType.PLAYBOOK)
        with pytest.raises(AttributeError):
            nid.path = "other.yml"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ContentNode
# ---------------------------------------------------------------------------


class TestContentNode:
    """Tests for ``ContentNode`` properties."""

    def test_node_type_property(self) -> None:
        """Verify node type is exposed from identity."""
        identity = NodeIdentity(path="site.yml", node_type=NodeType.PLAYBOOK)
        node = ContentNode(identity=identity, file_path="site.yml")
        assert node.node_type == NodeType.PLAYBOOK

    def test_node_id_property(self) -> None:
        """Verify node id matches identity path."""
        identity = NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY)
        node = ContentNode(identity=identity)
        assert node.node_id == "site.yml/plays[0]"

    def test_default_scope(self) -> None:
        """Verify default scope is owned."""
        identity = NodeIdentity(path="t.yml", node_type=NodeType.TASK)
        node = ContentNode(identity=identity)
        assert node.scope == NodeScope.OWNED


# ---------------------------------------------------------------------------
# ContentGraph
# ---------------------------------------------------------------------------


def _make_graph() -> ContentGraph:
    """Build a small graph for testing.

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
        name="Setup play",
    )
    g.add_node(play)
    g.add_edge("site.yml", "site.yml/plays[0]", EdgeType.CONTAINS, position=0)

    task0 = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
        file_path="site.yml",
        name="Install nginx",
        module="ansible.builtin.package",
        register="pkg_result",
    )
    g.add_node(task0)
    g.add_edge("site.yml/plays[0]", "site.yml/plays[0]/tasks[0]", EdgeType.CONTAINS, position=0)

    task1 = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[1]", node_type=NodeType.TASK),
        file_path="site.yml",
        name="Verify install",
        notify=["restart nginx"],
    )
    g.add_node(task1)
    g.add_edge("site.yml/plays[0]", "site.yml/plays[0]/tasks[1]", EdgeType.CONTAINS, position=1)

    handler = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/handlers[0]", node_type=NodeType.HANDLER),
        file_path="site.yml",
        name="restart nginx",
    )
    g.add_node(handler)
    g.add_edge("site.yml/plays[0]", "site.yml/plays[0]/handlers[0]", EdgeType.CONTAINS, position=2)
    g.add_edge("site.yml/plays[0]/tasks[1]", "site.yml/plays[0]/handlers[0]", EdgeType.NOTIFY)

    g.add_edge("site.yml/plays[0]/tasks[0]", "site.yml/plays[0]/tasks[1]", EdgeType.DATA_FLOW)

    return g


class TestContentGraph:
    """Tests for ``ContentGraph`` queries and structure."""

    def test_node_count(self) -> None:
        """Verify node count matches built graph."""
        g = _make_graph()
        assert g.node_count() == 5

    def test_get_node(self) -> None:
        """Verify get_node returns the expected node."""
        g = _make_graph()
        node = g.get_node("site.yml/plays[0]")
        assert node is not None
        assert node.name == "Setup play"

    def test_get_node_missing(self) -> None:
        """Verify get_node returns None for unknown ids."""
        g = _make_graph()
        assert g.get_node("nonexistent") is None

    def test_get_node_by_ari_key(self) -> None:
        """Verify lookup by ARI key resolves the playbook node."""
        g = _make_graph()
        node = g.get_node_by_ari_key("playbook playbook:site.yml")
        assert node is not None
        assert node.node_id == "site.yml"

    def test_nodes_filtered(self) -> None:
        """Verify nodes iterator filters by type."""
        g = _make_graph()
        tasks = list(g.nodes(NodeType.TASK))
        assert len(tasks) == 2

    def test_edges_from(self) -> None:
        """Verify edges_from returns outgoing edges of a type."""
        g = _make_graph()
        contains = g.edges_from("site.yml/plays[0]", EdgeType.CONTAINS)
        assert len(contains) == 3

    def test_edges_to(self) -> None:
        """Verify edges_to returns incoming notify edges."""
        g = _make_graph()
        incoming = g.edges_to("site.yml/plays[0]/handlers[0]", EdgeType.NOTIFY)
        assert len(incoming) == 1
        assert incoming[0][0] == "site.yml/plays[0]/tasks[1]"

    def test_ancestors(self) -> None:
        """Verify ancestors lists play then playbook for a task."""
        g = _make_graph()
        ancs = g.ancestors("site.yml/plays[0]/tasks[0]")
        assert len(ancs) == 2
        assert ancs[0].node_id == "site.yml/plays[0]"
        assert ancs[1].node_id == "site.yml"

    def test_children(self) -> None:
        """Verify children lists direct contains for a play."""
        g = _make_graph()
        kids = g.children("site.yml/plays[0]")
        assert len(kids) == 3

    def test_descendants(self) -> None:
        """Verify descendants includes all nodes under the playbook root."""
        g = _make_graph()
        desc = g.descendants("site.yml")
        assert len(desc) == 4

    def test_subgraph(self) -> None:
        """Verify subgraph rooted at a play contains expected nodes."""
        g = _make_graph()
        sub = g.subgraph("site.yml/plays[0]")
        assert sub.node_count() == 4

    def test_topological_order(self) -> None:
        """Verify topological order starts at the playbook root."""
        g = _make_graph()
        order = g.topological_order()
        assert order[0] == "site.yml"

    def test_is_acyclic(self) -> None:
        """Verify sample graph is reported acyclic."""
        g = _make_graph()
        assert g.is_acyclic()

    def test_edge_attributes(self) -> None:
        """Verify custom edge attributes round-trip on INCLUDE edges."""
        g = ContentGraph()
        n1 = ContentNode(identity=NodeIdentity(path="a", node_type=NodeType.PLAY))
        n2 = ContentNode(identity=NodeIdentity(path="b", node_type=NodeType.TASK))
        g.add_node(n1)
        g.add_node(n2)
        g.add_edge("a", "b", EdgeType.INCLUDE, conditional=True, dynamic=True, when_expr="x is defined")

        edges = g.edges_from("a", EdgeType.INCLUDE)
        assert len(edges) == 1
        _, attrs = edges[0]
        assert attrs["conditional"] is True
        assert attrs["dynamic"] is True
        assert attrs["when_expr"] == "x is defined"


# ---------------------------------------------------------------------------
# GraphBuilder smoke test (requires ARI model stubs)
# ---------------------------------------------------------------------------


class TestGraphBuilderMinimal:
    """Minimal GraphBuilder tests using hand-crafted definition dicts.

    These don't use real ARI parsing — they verify that GraphBuilder
    correctly processes the definition structures.
    """

    def test_empty_definitions(self) -> None:
        """Verify builder yields empty graph for empty input."""
        builder = GraphBuilder({}, {})
        graph = builder.build()
        assert graph.node_count() == 0

    def test_definitions_with_empty_mappings(self) -> None:
        """Verify empty definitions/mappings dict yields empty graph."""
        defs: dict[str, object] = {"definitions": {}, "mappings": None}
        builder = GraphBuilder(defs, {})
        graph = builder.build()
        assert graph.node_count() == 0


# ---------------------------------------------------------------------------
# GraphBuilder — loop variant extraction
# ---------------------------------------------------------------------------


def _build_task_node_with_options(options: dict[str, object]) -> ContentNode:
    """Build a ContentNode for a task with the given options dict.

    Creates a minimal GraphBuilder scaffold (playbook -> play -> task)
    and returns the resulting task node.

    Args:
        options: Task-level options dict (loop, when, with_*, etc.).

    Returns:
        The single task ContentNode produced by the builder.
    """
    from apme_engine.engine.models import ObjectList, Play, Playbook, Task, YAMLDict

    task = Task(
        key="task test_pb.yml#play[0]#task[0]",
        module="ansible.builtin.set_fact",
        options=cast(YAMLDict, options),
        module_options={"filtered": "{{ items }}"},
    )
    play = Play(
        key="play test_pb.yml#play[0]",
        defined_in="test_pb.yml",
        tasks=[task],
    )
    pb = Playbook(
        key="playbook test_pb.yml",
        defined_in="test_pb.yml",
        plays=[play],
    )
    root_defs: dict[str, object] = {
        "definitions": {
            "playbooks": ObjectList(items=[pb]),
        },
        "mappings": None,
    }
    builder = GraphBuilder(root_defs, {})
    graph = builder.build()
    task_nodes = [n for n in graph.nodes(NodeType.TASK)]
    assert len(task_nodes) == 1
    return task_nodes[0]


class TestGraphBuilderLoopVariants:
    """Verify _build_task populates node.loop for all loop constructs."""

    def test_modern_loop(self) -> None:
        """Modern ``loop:`` keyword populates node.loop."""
        node = _build_task_node_with_options({"loop": "{{ all_services }}"})
        assert node.loop is not None
        assert node.loop == "{{ all_services }}"

    def test_with_items(self) -> None:
        """Legacy ``with_items`` populates node.loop."""
        node = _build_task_node_with_options({"with_items": "{{ services }}"})
        assert node.loop is not None

    def test_with_dict(self) -> None:
        """``with_dict`` populates node.loop."""
        node = _build_task_node_with_options({"with_dict": {"a": 1, "b": 2}})
        assert node.loop is not None
        assert node.loop == {"a": 1, "b": 2}

    def test_with_sequence(self) -> None:
        """``with_sequence`` populates node.loop."""
        node = _build_task_node_with_options({"with_sequence": "start=1 end=5"})
        assert node.loop is not None

    def test_with_fileglob(self) -> None:
        """``with_fileglob`` populates node.loop."""
        node = _build_task_node_with_options({"with_fileglob": "/etc/*.conf"})
        assert node.loop is not None

    def test_with_subelements(self) -> None:
        """``with_subelements`` populates node.loop."""
        node = _build_task_node_with_options({"with_subelements": ["{{ users }}", "keys"]})
        assert node.loop is not None

    def test_with_nested(self) -> None:
        """``with_nested`` populates node.loop."""
        node = _build_task_node_with_options({"with_nested": [["a", "b"], [1, 2]]})
        assert node.loop is not None

    def test_with_together(self) -> None:
        """``with_together`` populates node.loop."""
        node = _build_task_node_with_options({"with_together": [["a", "b"], [1, 2]]})
        assert node.loop is not None

    def test_with_flattened(self) -> None:
        """``with_flattened`` populates node.loop."""
        node = _build_task_node_with_options({"with_flattened": [["a"], ["b", "c"]]})
        assert node.loop is not None

    def test_with_indexed_items(self) -> None:
        """``with_indexed_items`` populates node.loop."""
        node = _build_task_node_with_options({"with_indexed_items": ["a", "b"]})
        assert node.loop is not None

    def test_with_random_choice(self) -> None:
        """``with_random_choice`` populates node.loop."""
        node = _build_task_node_with_options({"with_random_choice": ["a", "b", "c"]})
        assert node.loop is not None

    def test_with_cartesian(self) -> None:
        """``with_cartesian`` populates node.loop."""
        node = _build_task_node_with_options({"with_cartesian": [["a", "b"], [1, 2]]})
        assert node.loop is not None

    def test_no_loop(self) -> None:
        """Task without any loop construct has node.loop == None."""
        node = _build_task_node_with_options({"when": "some_condition"})
        assert node.loop is None

    def test_loop_preferred_over_with(self) -> None:
        """Modern ``loop:`` takes precedence when both present."""
        node = _build_task_node_with_options(
            {
                "loop": "{{ primary_list }}",
                "with_items": "{{ fallback_list }}",
            }
        )
        assert node.loop == "{{ primary_list }}"

    def test_empty_list_loop_treated_as_no_loop(self) -> None:
        """Falsy ``loop: []`` is treated as no loop.

        In Ansible, ``loop: []`` is a no-op (zero iterations), so
        treating it as absent is correct behavior.
        """
        node = _build_task_node_with_options({"loop": []})
        assert node.loop is None

    def test_empty_dict_loop_treated_as_no_loop(self) -> None:
        """Falsy ``loop: {}`` is treated as no loop."""
        node = _build_task_node_with_options({"loop": {}})
        assert node.loop is None

    def test_empty_loop_does_not_mask_with(self) -> None:
        """Falsy ``loop`` falls through to ``with_*`` if present."""
        node = _build_task_node_with_options(
            {
                "loop": [],
                "with_items": "{{ services }}",
            }
        )
        assert node.loop is not None


# ---------------------------------------------------------------------------
# ContentGraph serialization roundtrip
# ---------------------------------------------------------------------------


class TestContentGraphSerialization:
    """Tests for ``ContentGraph.to_dict()`` / ``from_dict()`` roundtrip."""

    def test_roundtrip_topology(self) -> None:
        """Verify node count, edge count, and topology survive serialization."""
        original = _make_graph()
        d = original.to_dict()
        restored = ContentGraph.from_dict(d)

        assert restored.node_count() == original.node_count()
        assert restored.edge_count() == original.edge_count()

    def test_roundtrip_node_attributes(self) -> None:
        """Verify all ContentNode fields survive a roundtrip."""
        original = _make_graph()
        d = original.to_dict()
        restored = ContentGraph.from_dict(d)

        for orig_node in original.nodes():
            rest_node = restored.get_node(orig_node.node_id)
            assert rest_node is not None, f"Missing node {orig_node.node_id}"
            assert rest_node.node_type == orig_node.node_type
            assert rest_node.file_path == orig_node.file_path
            assert rest_node.name == orig_node.name
            assert rest_node.module == orig_node.module
            assert rest_node.register == orig_node.register
            assert rest_node.scope == orig_node.scope

    def test_roundtrip_ari_key_index(self) -> None:
        """Verify ARI key lookup works after deserialization."""
        original = _make_graph()
        restored = ContentGraph.from_dict(original.to_dict())

        node = restored.get_node_by_ari_key("playbook playbook:site.yml")
        assert node is not None
        assert node.node_id == "site.yml"

    def test_roundtrip_graph_queries(self) -> None:
        """Verify ancestors, children, descendants work on deserialized graph."""
        original = _make_graph()
        restored = ContentGraph.from_dict(original.to_dict())

        ancs = restored.ancestors("site.yml/plays[0]/tasks[0]")
        assert len(ancs) == 2
        assert ancs[0].node_id == "site.yml/plays[0]"

        kids = restored.children("site.yml/plays[0]")
        assert len(kids) == 3

        desc = restored.descendants("site.yml")
        assert len(desc) == 4

    def test_roundtrip_edge_attributes(self) -> None:
        """Verify edge types and custom attributes survive roundtrip."""
        g = ContentGraph()
        n1 = ContentNode(identity=NodeIdentity(path="a.yml", node_type=NodeType.PLAYBOOK))
        n2 = ContentNode(identity=NodeIdentity(path="a.yml/plays[0]", node_type=NodeType.PLAY))
        g.add_node(n1)
        g.add_node(n2)
        g.add_edge(
            "a.yml",
            "a.yml/plays[0]",
            EdgeType.INCLUDE,
            conditional=True,
            dynamic=True,
            when_expr="x is defined",
            tags=["deploy"],
        )

        restored = ContentGraph.from_dict(g.to_dict())
        edges = restored.edges_from("a.yml", EdgeType.INCLUDE)
        assert len(edges) == 1
        _, attrs = edges[0]
        assert attrs["conditional"] is True
        assert attrs["dynamic"] is True
        assert attrs["when_expr"] == "x is defined"
        assert attrs["tags"] == ["deploy"]

    def test_roundtrip_rich_node(self) -> None:
        """Verify a fully-populated ContentNode roundtrips all fields."""
        g = ContentGraph()
        node = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="p.yml",
            line_start=10,
            line_end=25,
            name="Deploy app",
            module="ansible.builtin.copy",
            module_options={"src": "/app", "dest": "/srv"},
            options={"when": "deploy_enabled"},
            variables={"app_version": "1.0"},
            become={"become": True, "become_user": "root"},
            when_expr="deploy_enabled",
            tags=["deploy", "app"],
            loop=["a", "b"],
            loop_control={"loop_var": "item"},
            register="copy_result",
            set_facts={"deployed": True},
            notify=["restart app"],
            listen=["deploy topic"],
            environment={"PATH": "/usr/bin"},
            no_log=True,
            ignore_errors=False,
            changed_when="false",
            failed_when="result.rc != 0",
            delegate_to="localhost",
            yaml_lines="- name: Deploy app\n  copy:\n    src: /app\n",
            role_fqcn="myorg.deploy",
            default_variables={"port": 8080},
            role_variables={"env": "prod"},
            role_metadata={"galaxy_info": {"author": "test"}},
            collection_namespace="myorg",
            collection_name="deploy",
            ari_key="task playbook:p.yml#play[0]#task[0]",
            scope=NodeScope.REFERENCED,
        )
        g.add_node(node)

        restored = ContentGraph.from_dict(g.to_dict())
        rn = restored.get_node("p.yml/plays[0]/tasks[0]")
        assert rn is not None
        assert rn.file_path == "p.yml"
        assert rn.line_start == 10
        assert rn.line_end == 25
        assert rn.name == "Deploy app"
        assert rn.module == "ansible.builtin.copy"
        assert rn.module_options == {"src": "/app", "dest": "/srv"}
        assert rn.options == {"when": "deploy_enabled"}
        assert rn.variables == {"app_version": "1.0"}
        assert rn.become == {"become": True, "become_user": "root"}
        assert rn.when_expr == "deploy_enabled"
        assert rn.tags == ["deploy", "app"]
        assert rn.loop == ["a", "b"]
        assert rn.loop_control == {"loop_var": "item"}
        assert rn.register == "copy_result"
        assert rn.set_facts == {"deployed": True}
        assert rn.notify == ["restart app"]
        assert rn.listen == ["deploy topic"]
        assert rn.environment == {"PATH": "/usr/bin"}
        assert rn.no_log is True
        assert rn.ignore_errors is False
        assert rn.changed_when == "false"
        assert rn.failed_when == "result.rc != 0"
        assert rn.delegate_to == "localhost"
        assert rn.yaml_lines == "- name: Deploy app\n  copy:\n    src: /app\n"
        assert rn.role_fqcn == "myorg.deploy"
        assert rn.default_variables == {"port": 8080}
        assert rn.role_variables == {"env": "prod"}
        assert rn.role_metadata == {"galaxy_info": {"author": "test"}}
        assert rn.collection_namespace == "myorg"
        assert rn.collection_name == "deploy"
        assert rn.ari_key == "task playbook:p.yml#play[0]#task[0]"
        assert rn.scope == NodeScope.REFERENCED

    def test_empty_graph_roundtrip(self) -> None:
        """Verify an empty graph survives roundtrip."""
        g = ContentGraph()
        restored = ContentGraph.from_dict(g.to_dict())
        assert restored.node_count() == 0
        assert restored.edge_count() == 0

    def test_version_check(self) -> None:
        """Verify unsupported version raises ValueError."""
        with pytest.raises(ValueError, match="Unsupported"):
            ContentGraph.from_dict({"version": 99, "nodes": [], "edges": []})

    def test_malformed_payload_raises_valueerror(self) -> None:
        """Verify malformed payloads raise ValueError, not KeyError/TypeError."""
        with pytest.raises(ValueError, match="Malformed"):
            ContentGraph.from_dict({"version": 1})
        with pytest.raises(ValueError, match="Malformed"):
            ContentGraph.from_dict({"version": 1, "nodes": [{"bad": True}], "edges": []})

    def test_json_serializable(self) -> None:
        """Verify to_dict output is JSON-serializable."""
        import json

        g = _make_graph()
        d = g.to_dict()
        serialized = json.dumps(d)
        assert isinstance(serialized, str)
        roundtripped = ContentGraph.from_dict(json.loads(serialized))
        assert roundtripped.node_count() == g.node_count()


# ---------------------------------------------------------------------------
# graph_report_to_violations converter
# ---------------------------------------------------------------------------


class TestGraphReportToViolations:
    """Tests for ``graph_report_to_violations`` conversion."""

    def test_verdict_true_becomes_violation(self) -> None:
        """Verify results with verdict=True are included in violations."""
        from apme_engine.engine.graph_scanner import (
            GraphNodeResult,
            GraphScanReport,
            graph_report_to_violations,
        )
        from apme_engine.engine.models import RuleMetadata

        node = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=5,
        )
        from apme_engine.validators.native.rules.graph_rule_base import GraphRuleResult

        report = GraphScanReport(
            node_results=[
                GraphNodeResult(
                    node_id="site.yml/plays[0]/tasks[0]",
                    node=node,
                    rule_results=[
                        GraphRuleResult(
                            rule=RuleMetadata(rule_id="L042", severity="warning", scope="task"),
                            verdict=True,
                            detail={"message": "too complex"},
                            node_id="site.yml/plays[0]/tasks[0]",
                            file=("site.yml", 5),
                        ),
                    ],
                ),
            ],
        )
        violations = graph_report_to_violations(report)
        assert len(violations) == 1
        assert violations[0]["rule_id"] == "L042"
        assert violations[0]["message"] == "too complex"
        assert violations[0]["file"] == "site.yml"
        assert violations[0]["line"] == 5
        assert violations[0]["source"] == "native"

    def test_verdict_false_excluded(self) -> None:
        """Verify results with verdict=False are excluded from violations."""
        from apme_engine.engine.graph_scanner import (
            GraphNodeResult,
            GraphScanReport,
            graph_report_to_violations,
        )
        from apme_engine.engine.models import RuleMetadata
        from apme_engine.validators.native.rules.graph_rule_base import GraphRuleResult

        node = ContentNode(
            identity=NodeIdentity(path="t.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="t.yml",
        )
        report = GraphScanReport(
            node_results=[
                GraphNodeResult(
                    node_id="t.yml/plays[0]/tasks[0]",
                    node=node,
                    rule_results=[
                        GraphRuleResult(
                            rule=RuleMetadata(rule_id="L042", severity="warning"),
                            verdict=False,
                            node_id="t.yml/plays[0]/tasks[0]",
                        ),
                    ],
                ),
            ],
        )
        violations = graph_report_to_violations(report)
        assert violations == []

    def test_empty_report(self) -> None:
        """Verify empty report produces empty violations."""
        from apme_engine.engine.graph_scanner import GraphScanReport, graph_report_to_violations

        assert graph_report_to_violations(GraphScanReport()) == []


# ---------------------------------------------------------------------------
# GraphBuilder block structure integration (Issue #164)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")  # type: ignore[untyped-decorator]
def block_graph() -> ContentGraph:
    """Parse graph-patterns/site.yml and build a ContentGraph (cached per module).

    Returns:
        ContentGraph from the graph-patterns fixture.
    """
    from apme_engine.engine.scan_state import SingleScan
    from apme_engine.runner import run_scan

    fixture = Path(__file__).resolve().parent / "fixtures" / "graph-patterns"
    context = run_scan(str(fixture / "site.yml"), str(fixture), include_scandata=True)
    sd = context.scandata
    assert isinstance(sd, SingleScan), "run_scan produced no scandata for graph-patterns fixture"
    builder = GraphBuilder(
        cast(dict[str, object], sd.root_definitions),
        cast(dict[str, object], sd.ext_definitions),
    )
    return builder.build()


class TestGraphBuilderBlockNodes:
    """Verify GraphBuilder produces BLOCK nodes, RESCUE and ALWAYS edges."""

    def test_block_nodes_exist(self, block_graph: ContentGraph) -> None:
        """ContentGraph contains NodeType.BLOCK nodes for block wrappers.

        Args:
            block_graph: Cached ContentGraph from graph-patterns fixture.
        """
        block_nodes = list(block_graph.nodes(NodeType.BLOCK))
        assert len(block_nodes) >= 2, (
            f"Expected at least 2 BLOCK nodes (migration + outer cert), got {len(block_nodes)}"
        )

    def test_rescue_edges_exist(self, block_graph: ContentGraph) -> None:
        """Block with rescue: produces RESCUE edges.

        Args:
            block_graph: Cached ContentGraph from graph-patterns fixture.
        """
        all_rescue = []
        for node in block_graph.nodes(NodeType.BLOCK):
            rescue_edges = block_graph.edges_from(node.node_id, EdgeType.RESCUE)
            all_rescue.extend(rescue_edges)
        assert len(all_rescue) >= 2, f"Expected at least 2 RESCUE edges, got {len(all_rescue)}"

    def test_always_edges_exist(self, block_graph: ContentGraph) -> None:
        """Block with always: produces ALWAYS edges.

        Args:
            block_graph: Cached ContentGraph from graph-patterns fixture.
        """
        all_always = []
        for node in block_graph.nodes(NodeType.BLOCK):
            always_edges = block_graph.edges_from(node.node_id, EdgeType.ALWAYS)
            all_always.extend(always_edges)
        assert len(all_always) >= 1, f"Expected at least 1 ALWAYS edge, got {len(all_always)}"

    def test_block_children_are_contains_children_of_block(self, block_graph: ContentGraph) -> None:
        """Block's children are CONTAINS children of the BLOCK, not the PLAY.

        Args:
            block_graph: Cached ContentGraph from graph-patterns fixture.
        """
        block_nodes = list(block_graph.nodes(NodeType.BLOCK))
        for block_node in block_nodes:
            contains_children = block_graph.children(block_node.node_id)
            assert len(contains_children) >= 1, f"Block {block_node.node_id} has no CONTAINS children"
            for child in contains_children:
                assert child.node_type in (NodeType.TASK, NodeType.BLOCK), (
                    f"Block child {child.node_id} is {child.node_type}, expected TASK or BLOCK"
                )

    def test_nested_block_produces_nested_block_node(self, block_graph: ContentGraph) -> None:
        """A block inside a block produces a nested BLOCK node.

        Args:
            block_graph: Cached ContentGraph from graph-patterns fixture.
        """
        block_nodes = list(block_graph.nodes(NodeType.BLOCK))
        nested_found = False
        for block_node in block_nodes:
            children = block_graph.children(block_node.node_id)
            for child in children:
                if child.node_type == NodeType.BLOCK:
                    nested_found = True
                    break
        assert nested_found, "Expected at least one nested BLOCK node (inner cert block)"

    def test_block_level_properties_on_block_node(self, block_graph: ContentGraph) -> None:
        """Block-level name is on the BLOCK node.

        Args:
            block_graph: Cached ContentGraph from graph-patterns fixture.
        """
        block_nodes = list(block_graph.nodes(NodeType.BLOCK))
        named_blocks = [b for b in block_nodes if b.name]
        assert len(named_blocks) >= 1, "Expected at least one named block node"
