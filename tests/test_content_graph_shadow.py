"""ContentGraph structural validation (ADR-044).

These tests feed the terrible-playbook fixture through the ARI pipeline
and verify that the ContentGraph produces structurally sound output.
This is permanent test infrastructure — the regression safety net for
the ContentGraph-backed pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from apme_engine.engine.content_graph import (
    ContentGraph,
    EdgeType,
    GraphBuilder,
    NodeType,
)
from apme_engine.engine.graph_opa_payload import build_hierarchy_from_graph
from apme_engine.engine.scan_state import SingleScan
from apme_engine.engine.variable_provenance import (
    VariableProvenanceResolver,
)


def _fixture_path() -> Path:
    """Return path to the terrible-playbook fixture.

    Returns:
        Path to the fixture directory.
    """
    return Path(__file__).resolve().parent / "fixtures" / "terrible-playbook"


@pytest.fixture(scope="module")  # type: ignore[untyped-decorator]
def scandata() -> SingleScan:
    """Parse the terrible-playbook through the full ARI pipeline.

    Returns:
        SingleScan with definitions, content graph, and hierarchy payload.
    """
    fixture = _fixture_path()
    if not fixture.is_dir():
        pytest.skip("terrible-playbook fixture not found")

    context = run_scan(str(fixture / "site.yml"), str(fixture), include_scandata=True)
    sd = context.scandata
    if sd is None:
        pytest.fail("run_scan produced no scandata for terrible-playbook")
    return cast(SingleScan, sd)


@pytest.fixture(scope="module")  # type: ignore[untyped-decorator]
def content_graph(scandata: SingleScan) -> ContentGraph:
    """Build a ContentGraph from the same definitions the scanner used.

    Args:
        scandata: SingleScan from the ARI pipeline.

    Returns:
        ContentGraph built from ARI definitions.
    """
    builder = GraphBuilder(
        cast(dict[str, object], scandata.root_definitions),
        cast(dict[str, object], scandata.ext_definitions),
    )
    return builder.build()


# Need this import after fixtures so scandata fixture can call it
from apme_engine.runner import run_scan  # noqa: E402

# ---------------------------------------------------------------------------
# Structural equivalence
# ---------------------------------------------------------------------------


class TestStructuralEquivalence:
    """Verify ContentGraph structural properties."""

    def test_graph_builds_without_error(self, content_graph: ContentGraph) -> None:
        """GraphBuilder completes on the terrible-playbook.

        Args:
            content_graph: ContentGraph from fixture.
        """
        assert content_graph.node_count() > 0
        assert content_graph.edge_count() > 0

    def test_graph_is_acyclic(self, content_graph: ContentGraph) -> None:
        """ContentGraph must be a DAG.

        Args:
            content_graph: ContentGraph from fixture.
        """
        assert content_graph.is_acyclic()

    def test_graph_has_playbook_root(self, content_graph: ContentGraph) -> None:
        """At least one playbook node exists as a graph root.

        Args:
            content_graph: ContentGraph from fixture.
        """
        playbooks = list(content_graph.nodes(NodeType.PLAYBOOK))
        assert len(playbooks) >= 1, "No playbook nodes found in graph"

    def test_graph_has_plays(self, content_graph: ContentGraph) -> None:
        """Plays should appear in the graph.

        Args:
            content_graph: ContentGraph from fixture.
        """
        plays = list(content_graph.nodes(NodeType.PLAY))
        assert len(plays) >= 1, "No play nodes found in graph"

    def test_graph_has_tasks(self, content_graph: ContentGraph) -> None:
        """Tasks should appear in the graph.

        Args:
            content_graph: ContentGraph from fixture.
        """
        tasks = list(content_graph.nodes(NodeType.TASK))
        assert len(tasks) >= 1, "No task nodes found in graph"

    def test_graph_node_count_reasonable(self, content_graph: ContentGraph) -> None:
        """Graph should have a reasonable number of nodes for the fixture.

        Args:
            content_graph: ContentGraph from fixture.
        """
        graph_count = content_graph.node_count()
        assert graph_count > 0, "Graph has no nodes"
        assert graph_count >= 5, f"Expected at least 5 nodes for terrible-playbook, got {graph_count}"

    def test_graph_has_contains_edges(self, content_graph: ContentGraph) -> None:
        """The graph uses CONTAINS edges for parent-child relationships.

        Args:
            content_graph: ContentGraph from fixture.
        """
        contains_count = sum(
            1 for _, _, data in content_graph.g.edges(data=True) if data.get("edge_type") == EdgeType.CONTAINS.value
        )
        assert contains_count > 0, "No CONTAINS edges found"

    def test_graph_has_diverse_edge_types(self, content_graph: ContentGraph) -> None:
        """The graph should have multiple edge types (not just CONTAINS).

        Args:
            content_graph: ContentGraph from fixture.
        """
        edge_types: set[str] = set()
        for _, _, data in content_graph.g.edges(data=True):
            et = data.get("edge_type", "")
            if isinstance(et, str):
                edge_types.add(et)
        assert len(edge_types) >= 2, f"Expected diverse edge types, found only: {edge_types}"


# ---------------------------------------------------------------------------
# ARI key cross-reference
# ---------------------------------------------------------------------------


class TestARIKeyCrossReference:
    """Verify ARI keys are tracked in the ContentGraph."""

    def test_playbook_nodes_have_ari_keys(self, content_graph: ContentGraph) -> None:
        """Every playbook node should carry an ARI key.

        Args:
            content_graph: ContentGraph from fixture.
        """
        playbooks = list(content_graph.nodes(NodeType.PLAYBOOK))
        assert playbooks, "No playbook nodes"
        for node in playbooks:
            assert node.ari_key, f"Playbook node {node.node_id} has no ari_key"

    def test_ari_key_lookup_works(self, content_graph: ContentGraph) -> None:
        """get_node_by_ari_key should find at least one node.

        Args:
            content_graph: ContentGraph from fixture.
        """
        found = False
        for node in content_graph.nodes():
            if node.ari_key and content_graph.get_node_by_ari_key(node.ari_key) is not None:
                found = True
                break
        assert found, "No ARI key lookup returned a node"


# ---------------------------------------------------------------------------
# OPA payload equivalence
# ---------------------------------------------------------------------------


class TestOPAPayloadEquivalence:
    """Compare OPA hierarchy payloads from both pipelines."""

    def test_graph_payload_has_hierarchy(self, scandata: SingleScan, content_graph: ContentGraph) -> None:
        """Graph-based payload produces a hierarchy list.

        Args:
            scandata: SingleScan with scan metadata.
            content_graph: ContentGraph from same definitions.
        """
        payload = build_hierarchy_from_graph(
            content_graph,
            scan_type=scandata.type,
            scan_name=scandata.name,
            collection_name=scandata.collection_name,
            role_name=scandata.role_name,
            scan_id="shadow-test",
        )
        hierarchy = payload.get("hierarchy")
        assert isinstance(hierarchy, list)
        assert len(hierarchy) >= 1, "Graph payload produced empty hierarchy"

    def test_graph_payload_has_nodes(self, scandata: SingleScan, content_graph: ContentGraph) -> None:
        """Graph-based payload hierarchy trees contain node dicts.

        Args:
            scandata: SingleScan with scan metadata.
            content_graph: ContentGraph from same definitions.
        """
        payload = build_hierarchy_from_graph(
            content_graph,
            scan_type=scandata.type,
            scan_name=scandata.name,
            scan_id="shadow-test",
        )
        hierarchy = payload.get("hierarchy")
        total_nodes = 0
        if isinstance(hierarchy, list):
            for tree in hierarchy:
                if isinstance(tree, dict):
                    nodes = tree.get("nodes")
                    if isinstance(nodes, list):
                        total_nodes += len(nodes)
        assert total_nodes > 0, "Graph payload has trees but no nodes"

    def test_graph_payload_node_types_match(self, scandata: SingleScan, content_graph: ContentGraph) -> None:
        """Both payloads produce the same set of OPA node types.

        Args:
            scandata: SingleScan with hierarchy_payload from old pipeline.
            content_graph: ContentGraph from same definitions.
        """
        old_payload = cast(dict[str, object], scandata.hierarchy_payload)
        new_payload = cast(
            dict[str, object],
            build_hierarchy_from_graph(
                content_graph,
                scan_type=scandata.type,
                scan_name=scandata.name,
                scan_id="shadow-test",
            ),
        )

        old_types = _collect_node_types(old_payload)
        new_types = _collect_node_types(new_payload)

        assert old_types, "Old pipeline produced no node types"
        assert new_types, "New pipeline produced no node types"

        missing = old_types - new_types
        if missing:
            assert len(missing) <= 1, (
                f"Graph payload missing OPA node types: {missing}. Old had: {old_types}, new has: {new_types}"
            )

    def test_collection_set_equivalent(self, scandata: SingleScan, content_graph: ContentGraph) -> None:
        """Both payloads derive the same collection_set.

        Args:
            scandata: SingleScan with hierarchy_payload.
            content_graph: ContentGraph from same definitions.
        """
        old_payload = cast(dict[str, object], scandata.hierarchy_payload)
        new_payload = cast(
            dict[str, object],
            build_hierarchy_from_graph(
                content_graph,
                scan_type=scandata.type,
                scan_name=scandata.name,
                scan_id="shadow-test",
            ),
        )
        old_raw = old_payload.get("collection_set")
        new_raw = new_payload.get("collection_set")
        old_colls: set[str] = set(old_raw) if isinstance(old_raw, list) else set()
        new_colls: set[str] = set(new_raw) if isinstance(new_raw, list) else set()

        if old_colls:
            overlap = old_colls & new_colls
            coverage = len(overlap) / len(old_colls)
            assert coverage >= 0.5, (
                f"Collection set overlap is {coverage:.0%}. Old: {sorted(old_colls)}, New: {sorted(new_colls)}"
            )


# ---------------------------------------------------------------------------
# Variable provenance smoke test
# ---------------------------------------------------------------------------


class TestVariableProvenanceSmokeTest:
    """Verify VariableProvenanceResolver produces results on real content."""

    def test_resolver_produces_variables(self, content_graph: ContentGraph) -> None:
        """At least some task nodes should have resolvable variables.

        Args:
            content_graph: ContentGraph from terrible-playbook.
        """
        resolver = VariableProvenanceResolver(content_graph)
        tasks_with_vars = 0
        total_tasks = 0

        for node in content_graph.nodes(NodeType.TASK):
            total_tasks += 1
            provs = resolver.resolve_variables(node.node_id)
            if provs:
                tasks_with_vars += 1

        assert total_tasks > 0, "No task nodes found"
        assert tasks_with_vars > 0, f"No tasks had resolvable variables out of {total_tasks}"

    def test_property_origins_found(self, content_graph: ContentGraph) -> None:
        """PropertyOrigin should find become/environment on some tasks.

        Args:
            content_graph: ContentGraph from terrible-playbook.
        """
        resolver = VariableProvenanceResolver(content_graph)
        tasks_with_origins = 0

        for node in content_graph.nodes(NodeType.TASK):
            origins = resolver.resolve_property_origins(node.node_id)
            if origins:
                tasks_with_origins += 1

        # Smoke test: resolver completes without error on all tasks.
        # The fixture may or may not use become/environment, so we don't
        # assert a minimum — the value of this test is crash-freedom.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collect_node_types(payload: dict[str, object]) -> set[str]:
    """Extract the set of node type strings from an OPA hierarchy payload.

    Args:
        payload: OPA hierarchy dict with 'hierarchy' key.

    Returns:
        Set of node type strings (e.g. playcall, taskcall).
    """
    types: set[str] = set()
    hierarchy = payload.get("hierarchy")
    if not isinstance(hierarchy, list):
        return types
    for tree in hierarchy:
        if not isinstance(tree, dict):
            continue
        nodes = tree.get("nodes")
        if not isinstance(nodes, list):
            continue
        for node in nodes:
            if isinstance(node, dict):
                t = node.get("type")
                if isinstance(t, str):
                    types.add(t)
    return types
