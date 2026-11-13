"""Role invocation contexts track changes to task YAML."""

from __future__ import annotations

import pytest

from apme_engine.engine.graph_opa_payload import content_node_to_opa_dict
from apme_engine.graph.content_graph import ContentGraph, ContentNode, EdgeType, NodeIdentity, NodeType
from apme_engine.graph.types import YAMLDict


def _node(
    graph: ContentGraph,
    path: str,
    node_type: NodeType,
    *,
    module: str = "",
    module_options: YAMLDict | None = None,
    options: YAMLDict | None = None,
) -> ContentNode:
    """Add an identified node to a test graph.

    Args:
        graph: Graph receiving the node.
        path: Stable node identity.
        node_type: Graph node kind.
        module: Task action name.
        module_options: Authored action arguments.
        options: Task-level options.

    Returns:
        The added node.
    """
    node = ContentNode(
        identity=NodeIdentity(path=path, node_type=node_type),
        file_path=path,
        module=module,
        module_options=module_options or {},
        options=options or {},
    )
    graph.add_node(node)
    return node


@pytest.mark.parametrize("module", ["include_role", "import_role"])  # type: ignore[untyped-decorator]
def test_role_call_uses_current_selectors_and_defaults_after_apply_yaml(module: str) -> None:
    """Edited task and handler selectors use live arguments across round trips.

    Args:
        module: Dynamic include or static import of the same role.
    """
    graph = ContentGraph()
    caller = _node(
        graph,
        "caller",
        NodeType.TASK,
        module=module,
        module_options={"name": "demo", "tasks_from": "main", "handlers_from": "main"},
        options={"module_defaults": {"file": {"owner": "old"}}},
    )
    other = _node(graph, "other", NodeType.PLAY)
    role = _node(graph, "role", NodeType.ROLE)
    edge_type = EdgeType.INCLUDE if module == "include_role" else EdgeType.IMPORT
    graph.add_edge(caller.node_id, role.node_id, edge_type)
    graph.g[caller.node_id][role.node_id][0]["role_invocation"] = {"tasks_from": "main", "handlers_from": "main"}
    graph.add_edge(other.node_id, role.node_id, EdgeType.DEPENDENCY)
    graph.g[other.node_id][role.node_id][0]["role_invocation"] = {"module_defaults": {"file": {"owner": "other"}}}
    tasks: dict[str, ContentNode] = {}
    for section in ("tasks", "handlers"):
        for entry in ("main", "alternate"):
            taskfile = _node(graph, f"{section}/{entry}.yml", NodeType.TASKFILE)
            graph.add_edge(role.node_id, taskfile.node_id, EdgeType.CONTAINS)
            graph.g[role.node_id][taskfile.node_id][0].update(role_section=section, role_file=f"{entry}.yml")
            task = _node(graph, f"{section}/{entry}/task", NodeType.TASK, module="file")
            graph.add_edge(taskfile.node_id, task.node_id, EdgeType.CONTAINS)
            tasks[f"{section}/{entry}"] = task

    updated = (
        f"- {module}:\n    name: demo\n    tasks_from: alternate\n"
        "  args:\n    tasks_from: main\n    handlers_from: alternate\n"
        "  module_defaults:\n    file:\n      owner: new\n"
    )
    assert graph.apply_yaml(caller.node_id, updated)
    for current in (graph, ContentGraph.from_dict(graph.to_dict(slim=True))):
        for section in ("tasks", "handlers"):
            main = current.get_node(tasks[f"{section}/main"].node_id)
            alternate = current.get_node(tasks[f"{section}/alternate"].node_id)
            assert main is not None and alternate is not None
            assert content_node_to_opa_dict(main, current)["module_defaults_contexts"] == [
                [{"file": {"owner": "other"}}]
            ]
            assert content_node_to_opa_dict(alternate, current)["module_defaults_contexts"] == [
                [{"file": {"owner": "new"}}]
            ]

    assert graph.apply_yaml(caller.node_id, f"- {module}:\n    name: demo\n")
    for section in ("tasks", "handlers"):
        main_payload = content_node_to_opa_dict(tasks[f"{section}/main"], graph)
        alternate_payload = content_node_to_opa_dict(tasks[f"{section}/alternate"], graph)
        contexts = main_payload["module_defaults_contexts"]
        assert isinstance(contexts, list)
        assert [] in contexts
        assert [{"file": {"owner": "other"}}] in contexts
        assert alternate_payload["module_defaults_inactive"] is True


@pytest.mark.parametrize("module", ["include_role", "import_role"])  # type: ignore[untyped-decorator]
def test_role_call_templated_args_mark_context_incomplete(module: str) -> None:
    """An unknown task-level args mapping cannot select a role entry safely.

    Args:
        module: Dynamic include or static import of the same role.
    """
    graph = ContentGraph()
    caller = _node(graph, "caller", NodeType.TASK, module=module, module_options={"name": "demo"})
    role = _node(graph, "role", NodeType.ROLE)
    task = _node(graph, "task", NodeType.TASK, module="file")
    graph.add_edge(caller.node_id, role.node_id, EdgeType.INCLUDE if module == "include_role" else EdgeType.IMPORT)
    graph.add_edge(role.node_id, task.node_id, EdgeType.CONTAINS)
    assert graph.apply_yaml(caller.node_id, f"- {module}:\n    name: demo\n  args: '{{{{ role_args }}}}'\n")
    assert content_node_to_opa_dict(task, graph)["module_defaults_incomplete"] is True


@pytest.mark.parametrize("change", ["name", "dynamic_name", "action", "role_action"])  # type: ignore[untyped-decorator]
def test_changed_role_target_or_action_does_not_assess_stale_edges(change: str) -> None:
    """Changed topology marks the old and any newly selected roles incomplete.

    Args:
        change: Changed target, dynamic target, non-role action, or role action kind.
    """
    graph = ContentGraph()
    caller = _node(graph, "caller", NodeType.TASK, module="include_role", module_options={"name": "first"})
    first = _node(graph, "first", NodeType.ROLE)
    first.role_fqcn = "first"
    second = _node(graph, "second", NodeType.ROLE)
    second.role_fqcn = "second"
    first_task = _node(graph, "first-task", NodeType.TASK, module="file")
    second_task = _node(graph, "second-task", NodeType.TASK, module="file")
    graph.add_edge(caller.node_id, first.node_id, EdgeType.INCLUDE)
    graph.add_edge(first.node_id, first_task.node_id, EdgeType.CONTAINS)
    graph.add_edge(second.node_id, second_task.node_id, EdgeType.CONTAINS)
    replacement = {
        "name": "- include_role:\n    name: second\n",
        "dynamic_name": "- include_role:\n    name: '{{ role_name }}'\n",
        "action": "- debug:\n    msg: hello\n",
        "role_action": "- import_role:\n    name: first\n",
    }[change]
    assert graph.apply_yaml(caller.node_id, replacement)
    for current in (graph, ContentGraph.from_dict(graph.to_dict(slim=True))):
        old_task = current.get_node(first_task.node_id)
        new_task = current.get_node(second_task.node_id)
        assert old_task is not None and new_task is not None
        assert content_node_to_opa_dict(old_task, current)["module_defaults_incomplete"] is True
        if change in {"name", "dynamic_name"}:
            assert content_node_to_opa_dict(new_task, current)["module_defaults_incomplete"] is True
