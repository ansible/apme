"""Regression tests for GraphBuilder DATA_FLOW edge wiring.

``GraphBuilder._wire_data_flow`` previously ran its consumer pass twice,
inserting duplicate parallel ``DATA_FLOW`` edges into the backing
``networkx.MultiDiGraph``. These tests build real projects on disk and
assert each producer→consumer flow is represented by exactly one edge.
"""

from __future__ import annotations

from pathlib import Path

from apme_engine.engine.graph_builder import GraphBuilder
from apme_engine.engine.model_loader import load_repository
from apme_engine.graph.content_graph import ContentGraph, EdgeType, NodeType

_PLAYBOOK = """---
- name: Test play
  hosts: localhost
  tasks:
    - name: Produce
      ansible.builtin.shell: cat /etc/hosts
      register: out
    - name: Consume
      ansible.builtin.debug:
        msg: "{{ out.stdout }}"
    - name: Consume in when
      ansible.builtin.debug:
        msg: static
      when: "{{ out.stdout != 'x' }}"
"""

_SET_FACT_PLAYBOOK = """---
- name: Test play
  hosts: localhost
  tasks:
    - name: Produce fact
      ansible.builtin.set_fact:
        shared_value: one
    - name: Consume fact
      ansible.builtin.debug:
        msg: "{{ shared_value }}"
"""


def _build_graph(root: Path) -> ContentGraph:
    """Build a ContentGraph from real files under ``root``.

    Args:
        root: Project directory holding playbook source files.

    Returns:
        Built ContentGraph for the project.
    """
    repository = load_repository(str(root), use_ansible_doc=False, load_children=True)
    definitions = {
        "playbooks": repository.playbooks,
        "taskfiles": repository.taskfiles,
        "roles": repository.roles,
    }
    return GraphBuilder({"root": {"definitions": definitions}}, {}).build()


def _incoming_data_flow_pairs(graph: ContentGraph, node_id: str) -> list[tuple[str, object]]:
    """Return (source_id, edge_type) pairs for DATA_FLOW edges into a node.

    Args:
        graph: ContentGraph to query.
        node_id: Target task node id.

    Returns:
        List of (source id, edge type) tuples, one per parallel edge.
    """
    return [(source, attrs.get("edge_type")) for source, attrs in graph.edges_to(node_id, EdgeType.DATA_FLOW)]


def test_register_consumer_has_single_data_flow_edge(tmp_path: Path) -> None:
    """Each register→consumer flow is wired with exactly one edge.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    (tmp_path / "site.yml").write_text(_PLAYBOOK)
    graph = _build_graph(tmp_path)

    consume_nodes = [n for n in graph.nodes() if n.node_type == NodeType.TASK and n.module == "ansible.builtin.debug"]
    assert len(consume_nodes) == 2

    for node in consume_nodes:
        pairs = _incoming_data_flow_pairs(graph, node.node_id)
        assert pairs, f"expected a DATA_FLOW edge into {node.node_id}"
        assert len(pairs) == 1, f"duplicate DATA_FLOW edges into {node.node_id}: {pairs}"
        producer = graph.get_node(pairs[0][0])
        assert producer is not None
        assert producer.register == "out"


def test_set_fact_consumer_has_single_data_flow_edge(tmp_path: Path) -> None:
    """Each set_fact→consumer flow is wired with exactly one edge.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    (tmp_path / "site.yml").write_text(_SET_FACT_PLAYBOOK)
    graph = _build_graph(tmp_path)

    for node in graph.nodes():
        if node.node_type != NodeType.TASK or node.module != "ansible.builtin.debug":
            continue
        pairs = _incoming_data_flow_pairs(graph, node.node_id)
        assert pairs, f"expected a DATA_FLOW edge into {node.node_id}"
        assert len(pairs) == 1, f"duplicate DATA_FLOW edges into {node.node_id}: {pairs}"
        producer = graph.get_node(pairs[0][0])
        assert producer is not None
        assert "shared_value" in producer.set_facts
