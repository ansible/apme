"""Regression tests for graph rules on tasks shared by multiple plays."""

from __future__ import annotations

from typing import cast

import pytest

from apme_engine.engine.graph_builder import GraphBuilder
from apme_engine.engine.models import ObjectList, Play, Playbook, Role, RoleInPlay, Task, TaskFile
from apme_engine.graph.content_graph import (
    ContentGraph,
    ContentNode,
    EdgeType,
    NodeIdentity,
    NodeScope,
    NodeType,
)
from apme_engine.graph.rules.L032_changed_data_dependence_graph import ChangedDataDependenceGraphRule
from apme_engine.graph.rules.L034_unused_override_graph import UnusedOverrideGraphRule
from apme_engine.graph.rules.L039_undefined_variable_graph import UndefinedVariableGraphRule
from apme_engine.graph.rules.L047_no_log_password_graph import NoLogPasswordGraphRule
from apme_engine.graph.rules.L110_debug_sensitive_vars_graph import DebugSensitiveVarsGraphRule
from apme_engine.graph.rules.M026_invalid_inventory_variable_names_graph import (
    InvalidInventoryVariableNamesGraphRule,
)
from apme_engine.graph.rules.R404_show_variables_graph import ShowVariablesGraphRule, _should_redact_value
from apme_engine.graph.types import YAMLDict
from apme_engine.graph.variable_provenance import (
    ProvenanceSource,
    VariableProvenance,
    VariableProvenanceResolver,
)


def _shared_include_graph(
    *,
    play_vars: tuple[YAMLDict, YAMLDict] = ({}, {}),
    play_no_logs: tuple[bool | None, bool | None] = (None, None),
    task_module: str = "ansible.builtin.debug",
    task_module_options: YAMLDict | None = None,
    task_variables: YAMLDict | None = None,
    task_set_facts: YAMLDict | None = None,
) -> tuple[ContentGraph, tuple[str, str], str, tuple[str, str]]:
    """Build two plays that include the same task node.

    Args:
        play_vars: Variable mappings defined by each play.
        play_no_logs: Explicit no_log values for the two plays.
        task_module: Module assigned to the shared task.
        task_module_options: Module arguments for the shared task.
        task_variables: Task-local variable definitions.
        task_set_facts: Variables set by the shared task.

    Returns:
        Tuple containing the graph, play IDs, shared task ID, and include-task IDs.
    """
    graph = ContentGraph()
    play_ids: list[str] = []
    include_ids: list[str] = []
    for index, (variables, no_log) in enumerate(zip(play_vars, play_no_logs, strict=True)):
        play = ContentNode(
            identity=NodeIdentity(path=f"site.yml/plays[{index}]", node_type=NodeType.PLAY),
            file_path="site.yml",
            variables=variables,
            no_log=no_log,
            scope=NodeScope.OWNED,
        )
        include = ContentNode(
            identity=NodeIdentity(path=f"site.yml/plays[{index}]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            module="ansible.builtin.include_tasks",
            module_options={"file": "shared.yml"},
            scope=NodeScope.OWNED,
        )
        graph.add_node(play)
        graph.add_node(include)
        graph.add_edge(play.node_id, include.node_id, EdgeType.CONTAINS, position=1)
        play_ids.append(play.node_id)
        include_ids.append(include.node_id)

    shared_task = ContentNode(
        identity=NodeIdentity(path="shared.yml/tasks[0]", node_type=NodeType.TASK),
        file_path="shared.yml",
        line_start=4,
        module=task_module,
        module_options=task_module_options or {},
        variables=task_variables or {},
        set_facts=task_set_facts or {},
        scope=NodeScope.OWNED,
    )
    graph.add_node(shared_task)
    for include_id in include_ids:
        graph.add_edge(include_id, shared_task.node_id, EdgeType.INCLUDE, position=1)

    return graph, (play_ids[0], play_ids[1]), shared_task.node_id, (include_ids[0], include_ids[1])


def test_l039_resolves_shared_task_vars_in_each_play() -> None:
    """Resolve shared task variables independently in both plays."""
    graph, _play_ids, task_id, _include_ids = _shared_include_graph(
        play_vars=({"shared_value": "one"}, {"shared_value": "two"}),
        task_module_options={"msg": "{{ shared_value }}"},
    )

    result = UndefinedVariableGraphRule().process(graph, task_id)

    assert result is not None
    assert result.verdict is False


def test_l039_requires_vars_on_every_include_path_within_a_play() -> None:
    """Require a variable to exist on every include path in a play."""
    graph, play_ids, task_id, _include_ids = _shared_include_graph(
        task_module_options={"msg": "{{ provided }}"},
    )
    optional_include = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[1]", node_type=NodeType.TASK),
        file_path="site.yml",
        module="ansible.builtin.include_tasks",
        module_options={"file": "shared.yml"},
        variables={"provided": True},
        scope=NodeScope.OWNED,
    )
    graph.add_node(optional_include)
    graph.add_edge(play_ids[0], optional_include.node_id, EdgeType.CONTAINS)
    graph.add_edge(optional_include.node_id, task_id, EdgeType.INCLUDE)

    result = UndefinedVariableGraphRule().process(graph, task_id)

    assert result is not None
    assert result.verdict is True
    assert result.detail is not None
    assert "provided" in cast(list[str], result.detail["undefined_vars"])


def test_runtime_definition_on_one_include_path_is_not_shared_across_paths() -> None:
    """Keep runtime definitions isolated to the include path that sets them."""
    graph, play_ids, task_id, include_ids = _shared_include_graph(task_module_options={"msg": "{{ path_result }}"})
    alternate_include = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[alternate]", node_type=NodeType.TASK),
        file_path="site.yml",
        module="ansible.builtin.include_tasks",
        module_options={"file": "shared.yml"},
        scope=NodeScope.OWNED,
    )
    producer = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]/tasks[0]", node_type=NodeType.TASK),
        file_path="shared-parent.yml",
        register="path_result",
        scope=NodeScope.OWNED,
    )
    graph.add_node(alternate_include)
    graph.add_node(producer)
    graph.add_edge(play_ids[0], alternate_include.node_id, EdgeType.CONTAINS)
    graph.add_edge(alternate_include.node_id, task_id, EdgeType.INCLUDE)
    graph.add_edge(include_ids[0], producer.node_id, EdgeType.CONTAINS, position=0)
    graph.add_edge(producer.node_id, task_id, EdgeType.DATA_FLOW)

    undefined_result = UndefinedVariableGraphRule().process(graph, task_id)
    variables_result = ShowVariablesGraphRule().process(graph, task_id)

    assert undefined_result is not None
    assert undefined_result.verdict is True
    assert undefined_result.detail is not None
    assert "path_result" in cast(list[str], undefined_result.detail["undefined_vars"])
    assert variables_result is not None
    assert variables_result.detail is not None
    variable_entries = cast(list[YAMLDict], variables_result.detail["variable_set"])
    path_entries = [entry for entry in variable_entries if entry["name"] == "path_result"]
    assert len(path_entries) == 1
    assert cast(list[str], path_entries[0]["execution_path"])[-2] == "site.yml/plays[0]/tasks[0]"


def test_static_and_runtime_definitions_together_cover_all_include_paths() -> None:
    """Accept a name statically defined on one path and at runtime on another."""
    graph, play_ids, task_id, include_ids = _shared_include_graph(
        play_vars=({}, {"shared_value": "playbook value"}),
        task_module_options={"msg": "{{ shared_value }}"},
    )
    static_include = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[static]", node_type=NodeType.TASK),
        file_path="site.yml",
        module="ansible.builtin.include_tasks",
        module_options={"file": "shared.yml"},
        variables={"shared_value": "static"},
        scope=NodeScope.OWNED,
    )
    producer = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]/tasks[0]", node_type=NodeType.TASK),
        file_path="shared-parent.yml",
        register="shared_value",
        scope=NodeScope.OWNED,
    )
    graph.add_node(static_include)
    graph.add_node(producer)
    graph.add_edge(play_ids[0], static_include.node_id, EdgeType.CONTAINS, position=0)
    graph.add_edge(static_include.node_id, task_id, EdgeType.INCLUDE, position=1)
    graph.add_edge(include_ids[0], producer.node_id, EdgeType.CONTAINS, position=0)
    graph.add_edge(producer.node_id, task_id, EdgeType.DATA_FLOW)

    result = UndefinedVariableGraphRule().process(graph, task_id)

    assert result is not None
    assert result.verdict is False


def test_runtime_definition_is_resolved_with_more_than_256_include_paths() -> None:
    """Keep play-scoped runtime vars when a shared task has many paths."""
    graph, play_ids, task_id, _include_ids = _shared_include_graph(
        play_vars=({}, {"many_path_result": "other play value"}),
        task_module_options={"msg": "{{ many_path_result }}"},
    )
    producer = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[producer]", node_type=NodeType.TASK),
        file_path="site.yml",
        register="many_path_result",
        scope=NodeScope.OWNED,
    )
    graph.add_node(producer)
    graph.add_edge(play_ids[0], producer.node_id, EdgeType.CONTAINS, position=0)
    graph.add_edge(producer.node_id, task_id, EdgeType.DATA_FLOW)

    for index in range(256):
        include = ContentNode(
            identity=NodeIdentity(path=f"site.yml/plays[0]/tasks[branch-{index:03}]", node_type=NodeType.TASK),
            file_path="site.yml",
            module="ansible.builtin.include_tasks",
            module_options={"file": "shared.yml"},
            scope=NodeScope.OWNED,
        )
        graph.add_node(include)
        graph.add_edge(play_ids[0], include.node_id, EdgeType.CONTAINS, position=index + 2)
        graph.add_edge(include.node_id, task_id, EdgeType.INCLUDE, position=1)

    result = UndefinedVariableGraphRule().process(graph, task_id)

    assert result is not None
    assert result.verdict is False


def test_runtime_definition_in_static_play_role_is_available_to_later_tasks() -> None:
    """Include static role tasks in play runtime provenance and execution order."""
    graph = ContentGraph()
    play = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
        file_path="site.yml",
        scope=NodeScope.OWNED,
    )
    role = ContentNode(
        identity=NodeIdentity(path="roles/web", node_type=NodeType.ROLE),
        file_path="roles/web",
        scope=NodeScope.OWNED,
    )
    taskfile = ContentNode(
        identity=NodeIdentity(path="roles/web/tasks/main.yml", node_type=NodeType.TASKFILE),
        file_path="roles/web/tasks/main.yml",
        scope=NodeScope.OWNED,
    )
    producer = ContentNode(
        identity=NodeIdentity(path="roles/web/tasks/main.yml/tasks[0]", node_type=NodeType.TASK),
        file_path="roles/web/tasks/main.yml",
        register="role_result",
        scope=NodeScope.OWNED,
    )
    consumer = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
        file_path="site.yml",
        module="ansible.builtin.debug",
        module_options={"msg": "{{ role_result }}"},
        scope=NodeScope.OWNED,
    )
    for node in (play, role, taskfile, producer, consumer):
        graph.add_node(node)
    graph.add_edge(play.node_id, role.node_id, EdgeType.DEPENDENCY, position=0)
    graph.add_edge(role.node_id, taskfile.node_id, EdgeType.CONTAINS, position=0)
    graph.add_edge(taskfile.node_id, producer.node_id, EdgeType.CONTAINS, position=0)
    graph.add_edge(play.node_id, consumer.node_id, EdgeType.CONTAINS, position=1)
    graph.add_edge(producer.node_id, consumer.node_id, EdgeType.DATA_FLOW)

    result = UndefinedVariableGraphRule().process(graph, consumer.node_id)

    assert producer.node_id in graph.play_scoped_node_ids(play.node_id)
    assert result is not None
    assert result.verdict is False


def test_pre_tasks_run_before_static_roles_for_variable_provenance() -> None:
    """Keep pre-task register values available to tasks in a static role."""
    pre_task = Task(
        key="task site.yml#play[0]#pre_tasks[0]",
        module="ansible.builtin.command",
        options={"register": "pre_task_result"},
        module_options={"cmd": "true"},
    )
    role_task = Task(
        key="task roles/web/tasks/main.yml#task[0]",
        module="ansible.builtin.debug",
        module_options={"msg": "{{ pre_task_result }}"},
    )
    role_taskfile = TaskFile(
        key="taskfile roles/web/tasks/main.yml",
        name="main.yml",
        defined_in="roles/web/tasks/main.yml",
        tasks=[role_task],
    )
    role = Role(
        key="role roles/web",
        name="web",
        fqcn="web",
        defined_in="roles/web",
        taskfiles=[role_taskfile],
    )
    play = Play(
        key="play site.yml#play[0]",
        defined_in="site.yml",
        pre_tasks=[pre_task],
        roles=[RoleInPlay(name="web", defined_in="site.yml")],
    )
    playbook = Playbook(
        key="playbook site.yml",
        defined_in="site.yml",
        plays=[play],
    )
    definitions: dict[str, object] = {
        "definitions": {
            "roles": ObjectList(items=[role]),
            "playbooks": ObjectList(items=[playbook]),
        },
        "mappings": None,
    }

    graph = GraphBuilder(definitions, {}).build()
    role_task_node_id = "roles/web/tasks/main.yml/tasks[0]"
    play_id = "site.yml/plays[0]"
    play_scope = graph.play_scoped_node_ids(play_id)
    resolved = VariableProvenanceResolver(graph).resolve_variables(
        role_task_node_id,
        play_context_id=play_id,
        play_scope=play_scope,
    )

    assert "pre_task_result" in resolved


def test_static_play_role_tasks_resolve_play_context_and_no_log() -> None:
    """Resolve play variables and protection inherited by a static role task."""
    graph = ContentGraph()
    play = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
        file_path="site.yml",
        variables={"play_value": "available"},
        no_log=True,
        scope=NodeScope.OWNED,
    )
    role = ContentNode(
        identity=NodeIdentity(path="roles/web", node_type=NodeType.ROLE),
        file_path="roles/web",
        scope=NodeScope.OWNED,
    )
    taskfile = ContentNode(
        identity=NodeIdentity(path="roles/web/tasks/main.yml", node_type=NodeType.TASKFILE),
        file_path="roles/web/tasks/main.yml",
        scope=NodeScope.OWNED,
    )
    task = ContentNode(
        identity=NodeIdentity(path="roles/web/tasks/main.yml/tasks[0]", node_type=NodeType.TASK),
        file_path="roles/web/tasks/main.yml",
        module="ansible.builtin.user",
        module_options={"password": "{{ play_value }}"},
        scope=NodeScope.OWNED,
    )
    for node in (play, role, taskfile, task):
        graph.add_node(node)
    graph.add_edge(play.node_id, role.node_id, EdgeType.DEPENDENCY, position=0)
    graph.add_edge(role.node_id, taskfile.node_id, EdgeType.CONTAINS, position=0)
    graph.add_edge(taskfile.node_id, task.node_id, EdgeType.CONTAINS, position=0)

    assert play.node_id in graph.positional_ancestor_ids(task.node_id)
    assert play.node_id in {
        ancestor.node_id
        for ancestor in graph.play_scoped_positional_ancestors(
            task.node_id,
            graph.play_scoped_node_ids(play.node_id),
        )
    }
    undefined = UndefinedVariableGraphRule().process(graph, task.node_id)
    assert undefined is not None
    assert undefined.verdict is False
    no_log = NoLogPasswordGraphRule().process(graph, task.node_id)
    assert no_log is not None
    assert no_log.verdict is False
    variables = ShowVariablesGraphRule().process(graph, task.node_id)
    assert variables is not None
    assert variables.detail is not None
    entries = cast(list[YAMLDict], variables.detail["variable_set"])
    assert any(entry["name"] == "play_value" for entry in entries)


@pytest.mark.parametrize("branch_edge", (EdgeType.RESCUE, EdgeType.ALWAYS))  # type: ignore[untyped-decorator]
def test_runtime_producer_in_rescue_or_always_reaches_task_after_block(branch_edge: EdgeType) -> None:
    """Keep runtime definitions from a block branch available after the block.

    Args:
        branch_edge: Rescue or always branch containing the producer.
    """
    graph = ContentGraph()
    play = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
        file_path="site.yml",
        scope=NodeScope.OWNED,
    )
    block = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/block[0]", node_type=NodeType.BLOCK),
        file_path="site.yml",
        scope=NodeScope.OWNED,
    )
    mainline = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/block[0]/tasks[0]", node_type=NodeType.TASK),
        file_path="site.yml",
        scope=NodeScope.OWNED,
    )
    producer = ContentNode(
        identity=NodeIdentity(path=f"site.yml/plays[0]/block[0]/{branch_edge.value}[0]", node_type=NodeType.TASK),
        file_path="site.yml",
        register="branch_result",
        scope=NodeScope.OWNED,
    )
    consumer = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[after-block]", node_type=NodeType.TASK),
        file_path="site.yml",
        module="ansible.builtin.debug",
        module_options={"msg": "{{ branch_result }}"},
        scope=NodeScope.OWNED,
    )
    for node in (play, block, mainline, producer, consumer):
        graph.add_node(node)
    graph.add_edge(play.node_id, block.node_id, EdgeType.CONTAINS, position=0)
    graph.add_edge(play.node_id, consumer.node_id, EdgeType.CONTAINS, position=1)
    graph.add_edge(block.node_id, mainline.node_id, EdgeType.CONTAINS, position=0)
    graph.add_edge(block.node_id, producer.node_id, EdgeType.CONTAINS, position=1)
    graph.add_edge(block.node_id, producer.node_id, branch_edge, position=0)
    graph.add_edge(producer.node_id, consumer.node_id, EdgeType.DATA_FLOW)

    result = UndefinedVariableGraphRule().process(graph, consumer.node_id)

    assert result is not None
    assert result.verdict is False


def test_shared_play_runtime_definitions_are_recovered_from_each_play_scope() -> None:
    """Recover matching runtime definitions separately for each play."""
    graph, play_ids, task_id, _include_ids = _shared_include_graph(task_module_options={"msg": "{{ result }}"})
    for index, play_id in enumerate(play_ids):
        block = ContentNode(
            identity=NodeIdentity(path=f"site.yml/plays[{index}]/block[0]", node_type=NodeType.BLOCK),
            file_path="site.yml",
            scope=NodeScope.OWNED,
        )
        producer = ContentNode(
            identity=NodeIdentity(path=f"site.yml/plays[{index}]/block[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            register="result",
            scope=NodeScope.OWNED,
        )
        graph.add_node(block)
        graph.add_node(producer)
        graph.add_edge(play_id, block.node_id, EdgeType.CONTAINS, position=0)
        graph.add_edge(block.node_id, producer.node_id, EdgeType.CONTAINS, position=0)
        if index == 0:
            # The graph builder currently emits one global-name edge for the
            # shared consumer; play 1's same-named producer is not linked.
            graph.add_edge(producer.node_id, task_id, EdgeType.DATA_FLOW)

    undefined_result = UndefinedVariableGraphRule().process(graph, task_id)
    variables_result = ShowVariablesGraphRule().process(graph, task_id)

    assert undefined_result is not None
    assert undefined_result.verdict is False
    assert variables_result is not None
    assert variables_result.detail is not None
    variable_entries = cast(list[YAMLDict], variables_result.detail["variable_set"])
    assert {(entry["name"], entry.get("play")) for entry in variable_entries} == {
        ("result", "site.yml/plays[0]"),
        ("result", "site.yml/plays[1]"),
    }


def test_l039_does_not_recover_same_named_register_after_shared_include() -> None:
    """Do not reuse a shared task's register from a later play task."""
    graph, play_ids, task_id, _include_ids = _shared_include_graph(task_module_options={"msg": "{{ result }}"})
    earlier = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
        file_path="site.yml",
        register="result",
        scope=NodeScope.OWNED,
    )
    later = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[1]/tasks[1]", node_type=NodeType.TASK),
        file_path="site.yml",
        register="result",
        scope=NodeScope.OWNED,
    )
    graph.add_node(earlier)
    graph.add_node(later)
    graph.add_edge(play_ids[0], earlier.node_id, EdgeType.CONTAINS, position=0)
    graph.add_edge(play_ids[1], later.node_id, EdgeType.CONTAINS, position=2)
    # Model the graph builder's global-name edge, which only points at the
    # play-0 producer even though the shared task also runs under play 1.
    graph.add_edge(earlier.node_id, task_id, EdgeType.DATA_FLOW)

    undefined_result = UndefinedVariableGraphRule().process(graph, task_id)
    variables_result = ShowVariablesGraphRule().process(graph, task_id)

    assert undefined_result is not None
    assert undefined_result.verdict is True
    assert undefined_result.detail is not None
    assert "result" in cast(list[str], undefined_result.detail["undefined_vars"])
    assert variables_result is not None
    assert variables_result.detail is not None
    variable_entries = cast(list[YAMLDict], variables_result.detail["variable_set"])
    result_plays = {entry.get("play") for entry in variable_entries if entry["name"] == "result"}
    assert result_plays == {"site.yml/plays[0]"}
    resolver = VariableProvenanceResolver(graph)
    second_scope = graph.play_scoped_node_ids(play_ids[1])
    assert "result" not in resolver.resolve_variables(
        task_id,
        play_context_id=play_ids[1],
        play_scope=second_scope,
    )


def test_l039_does_not_reuse_a_later_producer_across_repeated_includes() -> None:
    """Do not leak a producer from a later invocation of a shared include."""
    graph, play_ids, task_id, include_ids = _shared_include_graph(task_module_options={"msg": "{{ result }}"})
    later_include = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[1]", node_type=NodeType.TASK),
        file_path="site.yml",
        module="ansible.builtin.include_tasks",
        module_options={"file": "shared.yml"},
        scope=NodeScope.OWNED,
    )
    producer = ContentNode(
        identity=NodeIdentity(path="shared.yml/tasks[1]", node_type=NodeType.TASK),
        file_path="shared.yml",
        line_start=8,
        register="result",
        scope=NodeScope.OWNED,
    )
    graph.add_node(later_include)
    graph.add_node(producer)
    graph.add_edge(play_ids[0], later_include.node_id, EdgeType.CONTAINS, position=2)
    graph.add_edge(include_ids[0], task_id, EdgeType.INCLUDE, position=0)
    graph.add_edge(include_ids[0], producer.node_id, EdgeType.INCLUDE, position=1)
    graph.add_edge(later_include.node_id, task_id, EdgeType.INCLUDE, position=0)
    graph.add_edge(later_include.node_id, producer.node_id, EdgeType.INCLUDE, position=1)
    graph.add_edge(producer.node_id, task_id, EdgeType.DATA_FLOW)

    undefined_result = UndefinedVariableGraphRule().process(graph, task_id)
    variables_result = ShowVariablesGraphRule().process(graph, task_id)

    assert undefined_result is not None
    assert undefined_result.verdict is True
    assert undefined_result.detail is not None
    assert "result" in cast(list[str], undefined_result.detail["undefined_vars"])
    assert variables_result is not None
    if variables_result.detail is not None:
        variable_entries = cast(list[YAMLDict], variables_result.detail["variable_set"])
        assert all(entry["name"] != "result" for entry in variable_entries)


@pytest.mark.parametrize("consumer_inside_include", (True, False))  # type: ignore[untyped-decorator]
def test_runtime_definition_survives_nested_include_return(consumer_inside_include: bool) -> None:
    """Retain runtime definitions across nested includes and return to the play.

    Args:
        consumer_inside_include: Whether the consumer is inside the nested include.
    """
    graph = ContentGraph()
    play = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
        file_path="site.yml",
        scope=NodeScope.OWNED,
    )
    outer_include = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
        file_path="site.yml",
        module="ansible.builtin.include_tasks",
        module_options={"file": "outer.yml"},
        scope=NodeScope.OWNED,
    )
    producer = ContentNode(
        identity=NodeIdentity(path="outer.yml/tasks[0]", node_type=NodeType.TASK),
        file_path="outer.yml",
        line_start=4,
        set_facts={"outer_result": "available"},
        scope=NodeScope.OWNED,
    )
    nested_include = ContentNode(
        identity=NodeIdentity(path="outer.yml/tasks[1]", node_type=NodeType.TASK),
        file_path="outer.yml",
        module="ansible.builtin.include_tasks",
        module_options={"file": "nested.yml"},
        scope=NodeScope.OWNED,
    )
    consumer = ContentNode(
        identity=NodeIdentity(
            path="nested.yml/tasks[0]" if consumer_inside_include else "site.yml/plays[0]/tasks[1]",
            node_type=NodeType.TASK,
        ),
        file_path="nested.yml" if consumer_inside_include else "site.yml",
        module="ansible.builtin.debug",
        module_options={"msg": "{{ outer_result }}"},
        scope=NodeScope.OWNED,
    )
    for node in (play, outer_include, producer, nested_include, consumer):
        graph.add_node(node)
    graph.add_edge(play.node_id, outer_include.node_id, EdgeType.CONTAINS, position=0)
    graph.add_edge(outer_include.node_id, producer.node_id, EdgeType.INCLUDE, position=0)
    if consumer_inside_include:
        graph.add_edge(outer_include.node_id, nested_include.node_id, EdgeType.INCLUDE, position=1)
        graph.add_edge(nested_include.node_id, consumer.node_id, EdgeType.INCLUDE, position=0)
    else:
        graph.add_edge(play.node_id, consumer.node_id, EdgeType.CONTAINS, position=1)
    graph.add_edge(producer.node_id, consumer.node_id, EdgeType.DATA_FLOW)

    play_scope = graph.play_scoped_node_ids(play.node_id)
    resolved = VariableProvenanceResolver(graph).resolve_variables(
        consumer.node_id,
        play_context_id=play.node_id,
        play_scope=play_scope,
    )

    assert resolved["outer_result"].defining_node_id == producer.node_id


def test_latest_runtime_definition_wins_by_execution_order_not_node_id() -> None:
    """Select the last executed producer rather than the lexically last node."""
    graph = ContentGraph()
    play = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
        file_path="site.yml",
        scope=NodeScope.OWNED,
    )
    earlier = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[2]", node_type=NodeType.TASK),
        file_path="site.yml",
        set_facts={"shared_result": "earlier"},
        scope=NodeScope.OWNED,
    )
    later = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[10]", node_type=NodeType.TASK),
        file_path="site.yml",
        set_facts={"shared_result": "later"},
        scope=NodeScope.OWNED,
    )
    consumer = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[11]", node_type=NodeType.TASK),
        file_path="site.yml",
        module="ansible.builtin.debug",
        module_options={"msg": "{{ shared_result }}"},
        scope=NodeScope.OWNED,
    )
    for node in (play, earlier, later, consumer):
        graph.add_node(node)
    graph.add_edge(play.node_id, earlier.node_id, EdgeType.CONTAINS, position=2)
    graph.add_edge(play.node_id, later.node_id, EdgeType.CONTAINS, position=10)
    graph.add_edge(play.node_id, consumer.node_id, EdgeType.CONTAINS, position=11)
    graph.add_edge(earlier.node_id, consumer.node_id, EdgeType.DATA_FLOW)
    graph.add_edge(later.node_id, consumer.node_id, EdgeType.DATA_FLOW)

    play_scope = graph.play_scoped_node_ids(play.node_id)
    resolved = VariableProvenanceResolver(graph).resolve_variables(
        consumer.node_id,
        play_context_id=play.node_id,
        play_scope=play_scope,
    )

    assert resolved["shared_result"].defining_node_id == later.node_id


@pytest.mark.parametrize("branch_edge", (EdgeType.RESCUE, EdgeType.ALWAYS))  # type: ignore[untyped-decorator]
def test_runtime_producer_reaches_later_task_within_rescue_or_always(branch_edge: EdgeType) -> None:
    """Resolve a producer for later tasks within rescue and always sections.

    Args:
        branch_edge: Rescue or always branch containing the producer and consumer.
    """
    graph = ContentGraph()
    play = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
        file_path="site.yml",
        scope=NodeScope.OWNED,
    )
    block = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/block[0]", node_type=NodeType.BLOCK),
        file_path="site.yml",
        scope=NodeScope.OWNED,
    )
    producer = ContentNode(
        identity=NodeIdentity(path=f"site.yml/plays[0]/block[0]/{branch_edge.value}[0]", node_type=NodeType.TASK),
        file_path="site.yml",
        register="branch_result",
        scope=NodeScope.OWNED,
    )
    consumer = ContentNode(
        identity=NodeIdentity(path=f"site.yml/plays[0]/block[0]/{branch_edge.value}[1]", node_type=NodeType.TASK),
        file_path="site.yml",
        line_start=8,
        module="ansible.builtin.debug",
        module_options={"msg": "{{ branch_result }}"},
        scope=NodeScope.OWNED,
    )
    for node in (play, block, producer, consumer):
        graph.add_node(node)
    graph.add_edge(play.node_id, block.node_id, EdgeType.CONTAINS, position=0)
    graph.add_edge(block.node_id, producer.node_id, EdgeType.CONTAINS, position=0)
    graph.add_edge(block.node_id, producer.node_id, branch_edge, position=0)
    graph.add_edge(block.node_id, consumer.node_id, EdgeType.CONTAINS, position=1)
    graph.add_edge(block.node_id, consumer.node_id, branch_edge, position=1)
    graph.add_edge(producer.node_id, consumer.node_id, EdgeType.DATA_FLOW)

    undefined_result = UndefinedVariableGraphRule().process(graph, consumer.node_id)
    variables_result = ShowVariablesGraphRule().process(graph, consumer.node_id)

    assert undefined_result is not None
    assert undefined_result.verdict is False
    assert variables_result is not None
    assert variables_result.detail is not None
    variable_entries = cast(list[YAMLDict], variables_result.detail["variable_set"])
    assert any(entry["name"] == "branch_result" for entry in variable_entries)


@pytest.mark.parametrize("branch_edge", (EdgeType.RESCUE, EdgeType.ALWAYS))  # type: ignore[untyped-decorator]
def test_mainline_runtime_producer_reaches_rescue_or_always(branch_edge: EdgeType) -> None:
    """Make main-section runtime definitions available in rescue and always.

    Args:
        branch_edge: Rescue or always branch containing the consumer.
    """
    graph = ContentGraph()
    play = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
        file_path="site.yml",
        scope=NodeScope.OWNED,
    )
    block = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/block[0]", node_type=NodeType.BLOCK),
        file_path="site.yml",
        scope=NodeScope.OWNED,
    )
    producer = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/block[0]/tasks[0]", node_type=NodeType.TASK),
        file_path="site.yml",
        register="mainline_result",
        scope=NodeScope.OWNED,
    )
    consumer = ContentNode(
        identity=NodeIdentity(path=f"site.yml/plays[0]/block[0]/{branch_edge.value}[0]", node_type=NodeType.TASK),
        file_path="site.yml",
        module="ansible.builtin.debug",
        module_options={"msg": "{{ mainline_result }}"},
        scope=NodeScope.OWNED,
    )
    for node in (play, block, producer, consumer):
        graph.add_node(node)
    graph.add_edge(play.node_id, block.node_id, EdgeType.CONTAINS, position=0)
    graph.add_edge(block.node_id, producer.node_id, EdgeType.CONTAINS, position=0)
    graph.add_edge(block.node_id, consumer.node_id, EdgeType.CONTAINS, position=1)
    graph.add_edge(block.node_id, consumer.node_id, branch_edge, position=0)
    graph.add_edge(producer.node_id, consumer.node_id, EdgeType.DATA_FLOW)

    play_scope = graph.play_scoped_node_ids(play.node_id)
    resolved = VariableProvenanceResolver(graph).resolve_variables(
        consumer.node_id,
        play_context_id=play.node_id,
        play_scope=play_scope,
    )

    assert resolved["mainline_result"].defining_node_id == producer.node_id


def test_r404_redacts_values_using_the_definition_scope_no_log() -> None:
    """Redact a value based on the no_log scope where it was defined."""
    graph, play_ids, task_id, _include_ids = _shared_include_graph()
    block = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/block[0]", node_type=NodeType.BLOCK),
        file_path="site.yml",
        no_log=True,
        scope=NodeScope.OWNED,
    )
    source = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/block[0]/tasks[0]", node_type=NodeType.TASK),
        file_path="site.yml",
        scope=NodeScope.OWNED,
    )
    graph.add_node(block)
    graph.add_node(source)
    graph.add_edge(play_ids[0], block.node_id, EdgeType.CONTAINS, position=0)
    graph.add_edge(block.node_id, source.node_id, EdgeType.CONTAINS, position=0)
    provenance = VariableProvenance(
        name="ordinary_config",
        value={"region": "east", "mode": "safe"},
        source=ProvenanceSource.LOCAL,
        defining_node_id=source.node_id,
    )

    assert _should_redact_value(
        graph,
        task_id,
        provenance,
        play_ids[0],
        graph.play_scoped_node_ids(play_ids[0]),
    )


def test_l032_finds_play_var_redefined_by_shared_set_fact() -> None:
    """Detect a play variable redefined by a shared set_fact task."""
    graph, _play_ids, task_id, _include_ids = _shared_include_graph(
        play_vars=({"port": 80}, {"port": 81}),
        task_module="ansible.builtin.set_fact",
        task_module_options={"port": 8080},
        task_set_facts={"port": 8080},
    )

    result = ChangedDataDependenceGraphRule().process(graph, task_id)

    assert result is not None
    assert result.verdict is True
    assert result.detail is not None
    assert any(item["name"] == "port" for item in cast(list[YAMLDict], result.detail["variables"]))


def test_m026_finds_invalid_inventory_name_in_shared_play_scope() -> None:
    """Find invalid variable names in the selected play's shared task."""
    graph, _play_ids, task_id, _include_ids = _shared_include_graph(
        play_vars=({"valid_name": "one"}, {"bad-name": "two"}),
    )

    result = InvalidInventoryVariableNamesGraphRule().process(graph, task_id)

    assert result is not None
    assert result.verdict is True
    assert result.detail is not None
    assert "bad-name" in cast(list[str], result.detail["invalid_names"])


def test_l034_runtime_definitions_are_limited_to_the_selected_play() -> None:
    """Limit runtime override analysis to the selected play context."""
    graph, play_ids, task_id, include_ids = _shared_include_graph(task_variables={"result": "local"})
    producer = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[1]", node_type=NodeType.TASK),
        file_path="site.yml",
        module="ansible.builtin.command",
        register="result",
        scope=NodeScope.OWNED,
    )
    graph.add_node(producer)
    graph.add_edge(play_ids[0], producer.node_id, EdgeType.CONTAINS)
    graph.add_edge(producer.node_id, task_id, EdgeType.DATA_FLOW)

    resolver = VariableProvenanceResolver(graph)
    first_scope = graph.play_scoped_node_ids(play_ids[0])
    second_scope = graph.play_scoped_node_ids(play_ids[1])
    first_defs = resolver.resolve_all_definitions(task_id, play_context_id=play_ids[0], play_scope=first_scope)
    second_defs = resolver.resolve_all_definitions(task_id, play_context_id=play_ids[1], play_scope=second_scope)

    assert any(item.defining_node_id == producer.node_id for item in first_defs["result"])
    assert all(item.defining_node_id != producer.node_id for item in second_defs["result"])

    result = UnusedOverrideGraphRule().process(graph, task_id)
    assert result is not None
    assert result.verdict is True


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("play_no_logs", "expected_violation"),
    [((True, True), False), ((True, False), True)],
)
def test_l047_requires_no_log_for_every_shared_play(
    play_no_logs: tuple[bool | None, bool | None],
    expected_violation: bool,
) -> None:
    """Require no_log protection for password tasks in every shared play.

    Args:
        play_no_logs: no_log settings for each enclosing play.
        expected_violation: Whether the rule should report an unprotected path.
    """
    graph, _play_ids, task_id, _include_ids = _shared_include_graph(
        play_no_logs=play_no_logs,
        task_module="ansible.builtin.user",
        task_module_options={"password": "secret"},
    )

    result = NoLogPasswordGraphRule().process(graph, task_id)

    assert result is not None
    assert result.verdict is expected_violation


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("unscoped_no_log", "expected_violation"),
    [(None, True), (True, False)],
)
def test_l047_checks_unscoped_paths_alongside_play_paths(
    unscoped_no_log: bool | None,
    expected_violation: bool,
) -> None:
    """Check unscoped executions when a shared task also has a play path.

    Args:
        unscoped_no_log: no_log value on the unscoped include path.
        expected_violation: Whether the unprotected path should be reported.
    """
    graph = ContentGraph()
    play = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
        file_path="site.yml",
        no_log=True,
        scope=NodeScope.OWNED,
    )
    play_include = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
        file_path="site.yml",
        module="ansible.builtin.include_tasks",
        no_log=None,
        scope=NodeScope.OWNED,
    )
    unscoped_include = ContentNode(
        identity=NodeIdentity(path="standalone.yml/tasks[0]", node_type=NodeType.TASK),
        file_path="standalone.yml",
        module="ansible.builtin.include_tasks",
        no_log=unscoped_no_log,
        scope=NodeScope.OWNED,
    )
    task = ContentNode(
        identity=NodeIdentity(path="shared.yml/tasks[0]", node_type=NodeType.TASK),
        file_path="shared.yml",
        module="ansible.builtin.user",
        module_options={"password": "secret"},
        scope=NodeScope.OWNED,
    )
    for node in (play, play_include, unscoped_include, task):
        graph.add_node(node)
    graph.add_edge(play.node_id, play_include.node_id, EdgeType.CONTAINS, position=0)
    graph.add_edge(play_include.node_id, task.node_id, EdgeType.INCLUDE, position=0)
    graph.add_edge(unscoped_include.node_id, task.node_id, EdgeType.INCLUDE, position=0)

    result = NoLogPasswordGraphRule().process(graph, task.node_id)

    assert result is not None
    assert result.verdict is expected_violation


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("parent_no_logs", "expected_violation"),
    [((True, True), False), ((True, None), True)],
)
def test_l047_requires_no_log_on_every_unscoped_include_path(
    parent_no_logs: tuple[bool | None, bool | None],
    expected_violation: bool,
) -> None:
    """Require no_log protection on every unscoped include path.

    Args:
        parent_no_logs: no_log settings for each include parent.
        expected_violation: Whether the rule should report an unprotected path.
    """
    graph = ContentGraph()
    parents = [
        ContentNode(
            identity=NodeIdentity(path=f"shared-parent-{index}", node_type=NodeType.TASK),
            file_path=f"shared-parent-{index}.yml",
            module="ansible.builtin.include_tasks",
            no_log=no_log,
            scope=NodeScope.OWNED,
        )
        for index, no_log in enumerate(parent_no_logs)
    ]
    task = ContentNode(
        identity=NodeIdentity(path="shared.yml/tasks[0]", node_type=NodeType.TASK),
        file_path="shared.yml",
        module="ansible.builtin.user",
        module_options={"password": "secret"},
        scope=NodeScope.OWNED,
    )
    graph.add_node(task)
    for parent in parents:
        graph.add_node(parent)
        graph.add_edge(parent.node_id, task.node_id, EdgeType.INCLUDE)

    result = NoLogPasswordGraphRule().process(graph, task.node_id)

    assert result is not None
    assert result.verdict is expected_violation


def test_l110_requires_no_log_on_every_unscoped_include_path() -> None:
    """Require debug-task protection on every unscoped include path."""
    graph = ContentGraph()
    parents = [
        ContentNode(
            identity=NodeIdentity(path=f"shared-parent-{index}", node_type=NodeType.TASK),
            file_path=f"shared-parent-{index}.yml",
            module="ansible.builtin.include_tasks",
            no_log=no_log,
            scope=NodeScope.OWNED,
        )
        for index, no_log in enumerate((True, None))
    ]
    task = ContentNode(
        identity=NodeIdentity(path="shared.yml/tasks[0]", node_type=NodeType.TASK),
        file_path="shared.yml",
        module="ansible.builtin.debug",
        module_options={"msg": "{{ db_password }}"},
        scope=NodeScope.OWNED,
    )
    graph.add_node(task)
    for parent in parents:
        graph.add_node(parent)
        graph.add_edge(parent.node_id, task.node_id, EdgeType.INCLUDE)

    result = DebugSensitiveVarsGraphRule().process(graph, task.node_id)

    assert result is not None
    assert result.verdict is True


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("unscoped_no_log", "expected_violation"),
    [(None, True), (True, False)],
)
def test_l110_checks_unscoped_paths_alongside_play_paths(
    unscoped_no_log: bool | None,
    expected_violation: bool,
) -> None:
    """Check unscoped executions when a debug task also has a play path.

    Args:
        unscoped_no_log: no_log value on the unscoped include path.
        expected_violation: Whether the unprotected path should be reported.
    """
    graph = ContentGraph()
    play = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
        file_path="site.yml",
        no_log=True,
        scope=NodeScope.OWNED,
    )
    play_include = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
        file_path="site.yml",
        module="ansible.builtin.include_tasks",
        scope=NodeScope.OWNED,
    )
    unscoped_include = ContentNode(
        identity=NodeIdentity(path="standalone.yml/tasks[0]", node_type=NodeType.TASK),
        file_path="standalone.yml",
        module="ansible.builtin.include_tasks",
        no_log=unscoped_no_log,
        scope=NodeScope.OWNED,
    )
    task = ContentNode(
        identity=NodeIdentity(path="shared.yml/tasks[0]", node_type=NodeType.TASK),
        file_path="shared.yml",
        module="ansible.builtin.debug",
        module_options={"msg": "{{ db_password }}"},
        scope=NodeScope.OWNED,
    )
    for node in (play, play_include, unscoped_include, task):
        graph.add_node(node)
    graph.add_edge(play.node_id, play_include.node_id, EdgeType.CONTAINS, position=0)
    graph.add_edge(play_include.node_id, task.node_id, EdgeType.INCLUDE, position=0)
    graph.add_edge(unscoped_include.node_id, task.node_id, EdgeType.INCLUDE, position=0)

    result = DebugSensitiveVarsGraphRule().process(graph, task.node_id)

    assert result is not None
    assert result.verdict is expected_violation


def test_r404_reports_each_shared_play_variable_scope() -> None:
    """Report variables from each enclosing play for a shared task."""
    graph, _play_ids, task_id, _include_ids = _shared_include_graph(
        play_vars=({"first_only": "one"}, {"second_only": "two"}),
    )
    rule = ShowVariablesGraphRule()
    result = rule.process(graph, task_id)

    assert result is not None
    assert result.detail is not None
    variables = cast(list[YAMLDict], result.detail["variable_set"])
    assert {(item["name"], item["play"]) for item in variables} == {
        ("first_only", "site.yml/plays[0]"),
        ("second_only", "site.yml/plays[1]"),
    }


def test_r404_reports_separate_scope_for_each_include_path() -> None:
    """Report variable provenance separately for each include path."""
    graph, play_ids, task_id, _include_ids = _shared_include_graph()
    left_include = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[left]", node_type=NodeType.TASK),
        file_path="site.yml",
        module="ansible.builtin.include_tasks",
        module_options={"file": "shared.yml"},
        variables={"left_only": "left"},
        scope=NodeScope.OWNED,
    )
    right_include = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[right]", node_type=NodeType.TASK),
        file_path="site.yml",
        module="ansible.builtin.include_tasks",
        module_options={"file": "shared.yml"},
        variables={"right_only": "right"},
        scope=NodeScope.OWNED,
    )
    graph.add_node(left_include)
    graph.add_node(right_include)
    graph.add_edge(play_ids[0], left_include.node_id, EdgeType.CONTAINS)
    graph.add_edge(play_ids[0], right_include.node_id, EdgeType.CONTAINS)
    graph.add_edge(left_include.node_id, task_id, EdgeType.INCLUDE)
    graph.add_edge(right_include.node_id, task_id, EdgeType.INCLUDE)

    result = ShowVariablesGraphRule().process(graph, task_id)

    assert result is not None
    assert result.detail is not None
    variables = cast(list[YAMLDict], result.detail["variable_set"])
    scopes = {
        (item["name"], cast(list[str], item["execution_path"])[-2])
        for item in variables
        if item["name"] in {"left_only", "right_only"}
    }
    assert scopes == {
        ("left_only", "site.yml/plays[0]/tasks[left]"),
        ("right_only", "site.yml/plays[0]/tasks[right]"),
    }


def test_r404_retains_later_play_after_an_earlier_play_reaches_its_path_limit() -> None:
    """Reserve a context for each play before using the shared path budget."""
    graph, play_ids, task_id, _include_ids = _shared_include_graph(
        play_vars=({}, {"later_play_value": "visible"}),
    )
    for index in range(64):
        include = ContentNode(
            identity=NodeIdentity(path=f"site.yml/plays[0]/tasks[branch-{index:03}]", node_type=NodeType.TASK),
            file_path="site.yml",
            module="ansible.builtin.include_tasks",
            module_options={"file": "shared.yml"},
            scope=NodeScope.OWNED,
        )
        graph.add_node(include)
        graph.add_edge(play_ids[0], include.node_id, EdgeType.CONTAINS, position=index + 2)
        graph.add_edge(include.node_id, task_id, EdgeType.INCLUDE, position=1)

    result = ShowVariablesGraphRule().process(graph, task_id)

    assert result is not None
    assert result.verdict is True
    assert result.detail is not None
    variables = cast(list[YAMLDict], result.detail["variable_set"])
    assert any(entry["name"] == "later_play_value" and entry.get("play") == "site.yml/plays[1]" for entry in variables)
    assert result.detail["execution_paths_truncated"] is True


def test_r404_reports_truncation_when_only_omitted_paths_have_variables() -> None:
    """Do not return a clean result when every retained scope is empty."""
    graph, play_ids, task_id, _include_ids = _shared_include_graph()
    for index in range(63):
        include = ContentNode(
            identity=NodeIdentity(path=f"site.yml/plays[0]/tasks[branch-{index:03}]", node_type=NodeType.TASK),
            file_path="site.yml",
            module="ansible.builtin.include_tasks",
            module_options={"file": "shared.yml"},
            scope=NodeScope.OWNED,
        )
        graph.add_node(include)
        graph.add_edge(play_ids[0], include.node_id, EdgeType.CONTAINS, position=index + 2)
        graph.add_edge(include.node_id, task_id, EdgeType.INCLUDE, position=1)
    last_include = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[zzzz]", node_type=NodeType.TASK),
        file_path="site.yml",
        module="ansible.builtin.include_tasks",
        module_options={"file": "shared.yml"},
        variables={"omitted_path_value": "visible"},
        scope=NodeScope.OWNED,
    )
    graph.add_node(last_include)
    graph.add_edge(play_ids[0], last_include.node_id, EdgeType.CONTAINS, position=100)
    graph.add_edge(last_include.node_id, task_id, EdgeType.INCLUDE, position=1)

    result = ShowVariablesGraphRule().process(graph, task_id)

    assert result is not None
    assert result.verdict is True
    assert result.detail is not None
    assert result.detail["variable_set"] == []
    assert result.detail["execution_paths_truncated"] is True
