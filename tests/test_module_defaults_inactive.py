"""Regression coverage for inactive defaults scopes and shared invocations."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from apme_engine.engine.graph_opa_payload import content_node_to_opa_dict
from apme_engine.graph.content_graph import ContentGraph, ContentNode, EdgeType, NodeIdentity, NodeType
from apme_engine.graph.types import YAMLDict, YAMLValue
from apme_engine.validators.ansible.module_defaults import resolve_module_defaults
from apme_engine.validators.ansible.rules import L058_argspec_doc, L059_argspec_mock


def _node(graph: ContentGraph, name: str, kind: NodeType, defaults: YAMLValue = None) -> ContentNode:
    """Add an attributed graph node with optional authored defaults.

    Args:
        graph: Graph receiving the node.
        name: Unique node identity.
        kind: Content scope type.
        defaults: Authored defaults whose values remain shared.

    Returns:
        The added content node.
    """
    node = ContentNode(
        identity=NodeIdentity(path=name, node_type=kind),
        file_path="roles/demo/tasks/main.yml",
        line_start=1,
        line_end=3,
        options={"module_defaults": defaults} if defaults is not None else {},
    )
    graph.add_node(node)
    return node


def _oversized_defaults(shape: str) -> YAMLDict:
    """Create small authored values that exceed literal serialization limits.

    Args:
        shape: Shared aliases, excessive nesting, or repeated long scalars.

    Returns:
        Defaults whose expansion must be treated as unavailable when invoked.
    """
    value: YAMLValue = "leaf"
    if shape == "aliases":
        for _ in range(15):
            value = [value, value]
    elif shape == "depth":
        for _ in range(2000):
            value = {"nested": value}
    else:
        value = ["x" * 100_000] * 100
    return {"ansible.builtin.file": {"owner": value}}


def _shared_graph(
    kind: NodeType,
    defaults: YAMLValue = None,
    *,
    selected: bool = False,
    include_role: bool = False,
) -> tuple[ContentGraph, ContentNode, ContentNode, ContentNode]:
    """Build selected and unselected role entries that share another target.

    Args:
        kind: Scope carrying local defaults in the first role invocation.
        defaults: Local defaults, potentially exceeding assessment limits.
        selected: Whether the scope belongs to the main role entry.
        include_role: Whether callers share a role rather than a task file.

    Returns:
        Graph, local scope, its include caller, and the shared executable task.
    """
    graph = ContentGraph()
    role = _node(graph, "demo", NodeType.ROLE)
    section = "handlers" if kind == NodeType.HANDLER else "tasks"
    main = _node(graph, "demo/main.yml", NodeType.TASKFILE)
    unused = _node(graph, "demo/unused.yml", NodeType.TASKFILE)
    for entry, filename in ((main, "main.yml"), (unused, "unused.yml")):
        graph.add_edge(role.node_id, entry.node_id, EdgeType.CONTAINS)
        graph.g[role.node_id][entry.node_id][0].update(role_section=section, role_file=filename)

    scope = _node(graph, "local-scope", kind, defaults)
    graph.add_edge((main if selected else unused).node_id, scope.node_id, EdgeType.CONTAINS)
    caller = scope
    if kind == NodeType.BLOCK:
        caller = _node(graph, "local-include", NodeType.TASK)
        graph.add_edge(scope.node_id, caller.node_id, EdgeType.CONTAINS)

    active = _node(graph, "active-include", NodeType.TASK)
    target = _node(graph, "shared", NodeType.ROLE if include_role else NodeType.TASKFILE)
    module = "ansible.builtin.include_role" if include_role else "ansible.builtin.include_tasks"
    for invocation in (active, caller):
        invocation.module = module
        graph.add_edge(invocation.node_id, target.node_id, EdgeType.INCLUDE)
    if include_role:
        entry = _node(graph, "shared/main.yml", NodeType.TASKFILE)
        graph.add_edge(target.node_id, entry.node_id, EdgeType.CONTAINS)
        graph.g[target.node_id][entry.node_id][0].update(role_section="tasks", role_file="main.yml")
        target = entry

    task = _node(graph, "shared-task", NodeType.TASK)
    task.module = "ansible.builtin.file"
    task.module_options = {"path": "/tmp/example", "unsupported_literal": True}
    graph.add_edge(target.node_id, task.node_id, EdgeType.CONTAINS)
    return graph, scope, caller, task


def _assert_incomplete(payload: YAMLDict, tmp_path: Path) -> None:
    """Require unavailable ancestry to remain an explicit coverage warning.

    Args:
        payload: Serialized task whose ancestry cannot be fully assessed.
        tmp_path: Unused runtime root; incomplete contexts must need no Ansible.
    """
    assert payload["module_defaults_incomplete"] is True
    assert "module_defaults_inactive" not in payload
    result = resolve_module_defaults([cast(dict[str, object], payload)], tmp_path)
    assert result.tasks == []
    assert len(result.violations) == 1
    assert result.violations[0]["severity"] == "warning"
    assert "incomplete" in str(result.violations[0]["message"])


@pytest.mark.parametrize("kind", [NodeType.BLOCK, NodeType.TASK, NodeType.HANDLER])  # type: ignore[untyped-decorator]
@pytest.mark.parametrize("shape", ["aliases", "depth", "bytes"])  # type: ignore[untyped-decorator]
def test_inactive_local_defaults_do_not_suppress_shared_argument_checks(
    tmp_path: Path, kind: NodeType, shape: str
) -> None:
    """Unselected local values cannot make an independent invocation incomplete.

    Args:
        tmp_path: Missing runtime root, unnecessary for the active empty context.
        kind: Unselected scope whose defaults must not affect shared content.
        shape: Resource-limit failure that would occur if that scope were invoked.
    """
    defaults = _oversized_defaults(shape)
    graph, scope, caller, task = _shared_graph(kind, defaults)
    payload = content_node_to_opa_dict(task, graph)
    assert payload["module_defaults_contexts"] == [[]]
    assert "module_defaults_incomplete" not in payload
    assert "module_defaults_inactive" not in payload
    inactive_tasks = (caller,) if kind == NodeType.BLOCK else (scope,)
    for inactive in inactive_tasks:
        inactive_payload = content_node_to_opa_dict(inactive, graph)
        assert inactive_payload["module_defaults_contexts"] == []
        assert inactive_payload["module_defaults_inactive"] is True
        assert "module_defaults_incomplete" not in inactive_payload
    assert scope.options["module_defaults"] is defaults

    prepared = resolve_module_defaults([cast(dict[str, object], payload)], tmp_path)
    assert prepared.violations == []
    assert len(prepared.tasks) == 1
    assert prepared.tasks[0]["module_options"] == task.module_options
    parameter_spec = {"path": {"required": True}}
    for rule, field in ((L058_argspec_doc, "options"), (L059_argspec_mock, "argument_spec")):
        findings = rule._check_tasks_against_specs({task.module: {field: parameter_spec}}, prepared.tasks)
        assert len(findings) == 1
        assert "Unsupported parameters" in str(findings[0]["message"])
        assert "unsupported_literal" in str(findings[0]["message"])


@pytest.mark.parametrize("shape", ["aliases", "depth", "bytes"])  # type: ignore[untyped-decorator]
def test_selected_local_defaults_still_report_unavailable_coverage(tmp_path: Path, shape: str) -> None:
    """The inactivity guard must not hide resource failures on selected scopes.

    Args:
        tmp_path: Missing runtime root, unnecessary for unavailable contexts.
        shape: Active defaults expansion failure.
    """
    graph, _scope, _caller, task = _shared_graph(NodeType.BLOCK, _oversized_defaults(shape), selected=True)
    _assert_incomplete(content_node_to_opa_dict(task, graph), tmp_path)


@pytest.mark.parametrize("selector", ["{{ selected_entry }}", "missing"])  # type: ignore[untyped-decorator]
@pytest.mark.parametrize("selected", [False, True])  # type: ignore[untyped-decorator]
def test_role_selector_metadata_only_affects_invoked_callers(tmp_path: Path, selector: str, selected: bool) -> None:
    """Unknown role selectors affect active calls without reviving unused files.

    Args:
        tmp_path: Missing runtime root, unnecessary for coverage assessment.
        selector: Templated or unavailable literal role entry.
        selected: Whether the caller is in the selected main role entry.
    """
    graph, _scope, caller, task = _shared_graph(NodeType.TASK, selected=selected, include_role=True)
    caller.module_options = {"name": "shared", "tasks_from": selector}
    graph.g[caller.node_id]["shared"][0]["role_invocation"] = {"tasks_from": selector}
    payload = content_node_to_opa_dict(task, graph)
    if selected:
        _assert_incomplete(payload, tmp_path)
    else:
        assert payload["module_defaults_contexts"] == [[]]
        assert "module_defaults_incomplete" not in payload
        assert "module_defaults_inactive" not in payload


@pytest.mark.parametrize("selected", [False, True])  # type: ignore[untyped-decorator]
def test_dynamic_role_name_only_affects_reachable_callers(tmp_path: Path, selected: bool) -> None:
    """A dynamic role target in an unused entry cannot suppress active checks.

    Args:
        tmp_path: Runtime root for checking incomplete-context reporting.
        selected: Whether the dynamic caller belongs to the selected entry.
    """
    graph, _scope, caller, task = _shared_graph(NodeType.TASK, selected=selected, include_role=True)
    caller.module_options = {"name": "{{ chosen_role }}"}
    payload = content_node_to_opa_dict(task, graph)
    if selected:
        _assert_incomplete(payload, tmp_path)
    else:
        assert payload["module_defaults_contexts"] == [[]]
        assert "module_defaults_incomplete" not in payload


def test_unknown_source_role_entry_is_not_treated_as_proven_inactive(tmp_path: Path) -> None:
    """A role with unknown entry selection still needs conservative coverage.

    Args:
        tmp_path: Missing runtime root, unnecessary for unavailable ancestry.
    """
    graph, _scope, _caller, task = _shared_graph(NodeType.BLOCK, _oversized_defaults("aliases"))
    play = _node(graph, "play", NodeType.PLAY)
    include = _node(graph, "source-include", NodeType.TASK)
    include.module = "ansible.builtin.include_role"
    include.module_options = {"name": "demo", "tasks_from": "{{ source_entry }}"}
    graph.add_edge(play.node_id, include.node_id, EdgeType.CONTAINS)
    graph.add_edge(include.node_id, "demo", EdgeType.INCLUDE)
    graph.g[include.node_id]["demo"][0]["role_invocation"] = {"tasks_from": "{{ source_entry }}"}
    _assert_incomplete(content_node_to_opa_dict(task, graph), tmp_path)


def test_empty_but_incomplete_parent_preserves_shared_coverage_warning(tmp_path: Path) -> None:
    """A truncated active path remains unknown after all its paths are dropped.

    Args:
        tmp_path: Missing runtime root, unnecessary for incomplete ancestry.
    """
    graph, _scope, caller, task = _shared_graph(NodeType.TASK)
    parent = None
    for index in range(257):
        block = _node(graph, f"block-{index:03}", NodeType.BLOCK, {"file": {"owner": "root"}})
        if parent is not None:
            graph.add_edge(parent.node_id, block.node_id, EdgeType.CONTAINS)
        parent = block
    assert parent is not None
    graph.add_edge(parent.node_id, caller.node_id, EdgeType.CONTAINS)
    caller_payload = content_node_to_opa_dict(caller, graph)
    assert caller_payload["module_defaults_contexts"] == []
    _assert_incomplete(caller_payload, tmp_path)
    task_payload = content_node_to_opa_dict(task, graph)
    assert task_payload["module_defaults_contexts"] == [[]]
    _assert_incomplete(task_payload, tmp_path)
