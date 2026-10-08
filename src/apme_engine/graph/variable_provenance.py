"""Variable provenance tracking for ContentGraph (ADR-044).

Determines *where* each variable in a node's scope originates by walking
the graph ancestry.  This replaces the flat accumulation done by
``Context.add()`` with a provenance-preserving model.

Public API
----------
- ``VariableProvenance``   — where a single variable was defined
- ``PropertyOrigin``       — where an inherited property (become, etc.) was defined
- ``VariableProvenanceResolver`` — resolves all variables for a node
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from apme_engine.graph.content_graph import ContentGraph, ContentNode, EdgeType, NodeType
from apme_engine.graph.types import YAMLValue

_MAX_RUNTIME_PATHS = 256

# ---------------------------------------------------------------------------
# Provenance classification
# ---------------------------------------------------------------------------


class ProvenanceSource(str, Enum):
    """Where a variable binding originated.

    Attributes:
        LOCAL: Defined on the task or handler itself.
        BLOCK: Defined on a containing block.
        ROLE_DEFAULT: Role defaults/main.
        ROLE_VAR: Role vars.
        PLAY: Play-level vars.
        PLAYBOOK: Playbook-level vars.
        RUNTIME: From register or set_fact (data_flow).
        INVENTORY_FILE: From inventory (placeholder classification).
        VARS_FILE: From a linked vars_file node.
        EXTERNAL: Unknown or out-of-graph origin.
    """

    LOCAL = "local"
    BLOCK = "block"
    ROLE_DEFAULT = "role_default"
    ROLE_VAR = "role_var"
    PLAY = "play"
    PLAYBOOK = "playbook"
    RUNTIME = "runtime"
    INVENTORY_FILE = "inventory_file"
    VARS_FILE = "vars_file"
    EXTERNAL = "external"


@dataclass(frozen=True, slots=True)
class VariableProvenance:
    """Record of a single variable's origin.

    Attributes:
        name: Variable name (e.g. ``nginx_port``).
        value: Resolved value at the defining scope.
        source: Provenance classification.
        defining_node_id: Node ID where the variable is defined.
        file_path: File containing the definition.
        line: Approximate line number (0 if unknown).
    """

    name: str
    value: YAMLValue | None = None
    source: ProvenanceSource = ProvenanceSource.EXTERNAL
    defining_node_id: str = ""
    file_path: str = ""
    line: int = 0


@dataclass(frozen=True, slots=True)
class PropertyOrigin:
    """Record of an inherited property's defining scope.

    Used for ``become``, ``environment``, ``no_log``, ``tags``, etc.
    so that violations can be attributed to the scope where the
    property was actually set rather than every inheriting child.

    Attributes:
        property_name: Name of the inherited property.
        value: The property value at the defining scope.
        defining_node_id: Node ID of the scope that set this property.
        file_path: File where the property was defined.
        line: Line number of the defining node.
    """

    property_name: str
    value: YAMLValue | None = None
    defining_node_id: str = ""
    file_path: str = ""
    line: int = 0


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

_INHERITED_PROPERTIES = frozenset(
    {
        "become",
        "environment",
        "no_log",
        "ignore_errors",
        "tags",
    }
)

_PROVENANCE_BY_NODE_TYPE: dict[NodeType, ProvenanceSource] = {
    NodeType.TASK: ProvenanceSource.LOCAL,
    NodeType.HANDLER: ProvenanceSource.LOCAL,
    NodeType.BLOCK: ProvenanceSource.BLOCK,
    NodeType.PLAY: ProvenanceSource.PLAY,
    NodeType.PLAYBOOK: ProvenanceSource.PLAYBOOK,
    NodeType.ROLE: ProvenanceSource.ROLE_VAR,
    NodeType.TASKFILE: ProvenanceSource.LOCAL,
    NodeType.VARS_FILE: ProvenanceSource.VARS_FILE,
}


class VariableProvenanceResolver:
    """Resolves variable bindings and property origins for ContentGraph nodes.

    Walk order follows Ansible's precedence rules (simplified):
    local task vars > block vars > role vars > play vars > role defaults
    > playbook vars > vars_files > inventory vars > external.
    """

    def __init__(self, graph: ContentGraph) -> None:
        """Create a resolver bound to a content graph.

        Args:
            graph: ``ContentGraph`` to walk for scope and data-flow edges.
        """
        self._graph = graph
        self._execution_successors: dict[str, set[str]] | None = None

    def resolve_variables(
        self,
        node_id: str,
        *,
        play_context_id: str | None = None,
        play_scope: set[str] | None = None,
        positional_scope: set[str] | None = None,
    ) -> dict[str, VariableProvenance]:
        """Resolve all variables in scope for a node.

        Returns a dict mapping variable names to their provenance.
        Variables from higher-precedence scopes shadow lower ones.

        Args:
            node_id: Graph node id whose effective variable scope is resolved.
            play_context_id: Optional play node id scoping shared included
                tasks (mirrors ``positional_ancestors`` play disambiguation).
            play_scope: Optional precomputed play scope for ``play_context_id``.
            positional_scope: Optional subset of positional nodes used to
                resolve one include path while ``play_scope`` continues to
                bound runtime data-flow definitions to the enclosing play.

        Returns:
            Map from variable name to ``VariableProvenance`` (shadowing applied).
        """
        result: dict[str, VariableProvenance] = {}
        node = self._graph.get_node(node_id)
        if node is None:
            return result

        scope_chain = self._build_scope_chain(node_id, play_context_id, play_scope, positional_scope)
        runtime_scope = play_scope
        if play_context_id is not None and runtime_scope is None:
            runtime_scope = self._graph.play_scoped_node_ids(play_context_id)

        for scope_node in scope_chain:
            source = _PROVENANCE_BY_NODE_TYPE.get(scope_node.node_type, ProvenanceSource.EXTERNAL)

            if scope_node.node_type == NodeType.ROLE:
                for name, value in scope_node.default_variables.items():
                    if name not in result:
                        result[name] = VariableProvenance(
                            name=name,
                            value=value,
                            source=ProvenanceSource.ROLE_DEFAULT,
                            defining_node_id=scope_node.node_id,
                            file_path=scope_node.file_path,
                            line=scope_node.line_start,
                        )
                for name, value in scope_node.role_variables.items():
                    result[name] = VariableProvenance(
                        name=name,
                        value=value,
                        source=ProvenanceSource.ROLE_VAR,
                        defining_node_id=scope_node.node_id,
                        file_path=scope_node.file_path,
                        line=scope_node.line_start,
                    )
            else:
                for name, value in scope_node.variables.items():
                    if scope_node == node or name not in result:
                        result[name] = VariableProvenance(
                            name=name,
                            value=value,
                            source=source,
                            defining_node_id=scope_node.node_id,
                            file_path=scope_node.file_path,
                            line=scope_node.line_start,
                        )

            self._collect_vars_file_vars(scope_node, result)

        self._collect_runtime_vars(
            node_id,
            result,
            play_scope=runtime_scope,
            play_context_id=play_context_id,
            positional_scope=positional_scope,
        )
        self._collect_loop_vars(node, result)

        return result

    def resolve_all_definitions(
        self,
        node_id: str,
        *,
        play_context_id: str | None = None,
        play_scope: set[str] | None = None,
    ) -> dict[str, list[VariableProvenance]]:
        """Return every variable definition visible to a node, without shadowing.

        Unlike ``resolve_variables()`` which returns only the winning
        definition, this returns *all* definitions at every scope level,
        ordered from innermost scope (self) to outermost (root).  Useful
        for detecting ineffective overrides (L034) where a lower-precedence
        definition is shadowed by a higher one.

        Args:
            node_id: Graph node id whose full variable scope is resolved.
            play_context_id: Optional play node id scoping shared included tasks.
            play_scope: Optional precomputed play scope for ``play_context_id``.

        Returns:
            Map from variable name to list of ``VariableProvenance`` entries
            (innermost scope first).
        """
        result: dict[str, list[VariableProvenance]] = {}
        node = self._graph.get_node(node_id)
        if node is None:
            return result

        scope_chain = self._build_scope_chain(node_id, play_context_id, play_scope)
        runtime_scope = play_scope
        if play_context_id is not None and runtime_scope is None:
            runtime_scope = self._graph.play_scoped_node_ids(play_context_id)

        for scope_node in scope_chain:
            source = _PROVENANCE_BY_NODE_TYPE.get(scope_node.node_type, ProvenanceSource.EXTERNAL)

            if scope_node.node_type == NodeType.ROLE:
                for name, value in scope_node.default_variables.items():
                    result.setdefault(name, []).append(
                        VariableProvenance(
                            name=name,
                            value=value,
                            source=ProvenanceSource.ROLE_DEFAULT,
                            defining_node_id=scope_node.node_id,
                            file_path=scope_node.file_path,
                            line=scope_node.line_start,
                        )
                    )
                for name, value in scope_node.role_variables.items():
                    result.setdefault(name, []).append(
                        VariableProvenance(
                            name=name,
                            value=value,
                            source=ProvenanceSource.ROLE_VAR,
                            defining_node_id=scope_node.node_id,
                            file_path=scope_node.file_path,
                            line=scope_node.line_start,
                        )
                    )
            else:
                for name, value in scope_node.variables.items():
                    result.setdefault(name, []).append(
                        VariableProvenance(
                            name=name,
                            value=value,
                            source=source,
                            defining_node_id=scope_node.node_id,
                            file_path=scope_node.file_path,
                            line=scope_node.line_start,
                        )
                    )

            self._collect_vars_file_all(scope_node, result)

        self._collect_runtime_vars_all(
            node_id,
            result,
            play_context_id=play_context_id,
            play_scope=runtime_scope,
        )
        self._collect_loop_vars_all(node, result)

        return result

    def variables_defined_on_all_paths(
        self,
        node_id: str,
        *,
        play_context_id: str,
        play_scope: set[str] | None = None,
    ) -> set[str]:
        """Return variables available on every positional path to a task.

        Shared include nodes may have several parents within one play. A
        normal play-scoped resolution merges those parents, which is useful
        for collecting possible definitions but too permissive for L039:
        a variable defined on only one include path is not safe to assume on
        every execution. This method retains only names defined along every
        parent path while still honoring task variables, vars-file edges,
        runtime data-flow inputs, loop variables, and playbook-level scope.

        Args:
            node_id: Task or handler whose variable names are checked.
            play_context_id: Enclosing PLAY node that scopes the paths.
            play_scope: Optional precomputed scope for ``play_context_id``.

        Returns:
            Variable names defined on all paths from the task to its play.
        """
        scope = play_scope if play_scope is not None else self._graph.play_scoped_node_ids(play_context_id)
        candidates = set(
            self.resolve_variables(
                node_id,
                play_context_id=play_context_id,
                play_scope=scope,
            )
        )
        playbook_ancestors = self._graph.ancestors(play_context_id)
        resolved: set[str] = set()

        def defines_at(current_id: str, variable_name: str, *, include_runtime: bool) -> bool:
            node = self._graph.get_node(current_id)
            if node is None:
                return False
            if variable_name in node.variables:
                return True
            if node.node_type == NodeType.ROLE and (
                variable_name in node.default_variables or variable_name in node.role_variables
            ):
                return True
            for target_id, _attrs in self._graph.edges_from(current_id, EdgeType.VARS_INCLUDE):
                vars_file = self._graph.get_node(target_id)
                if vars_file is not None and variable_name in vars_file.variables:
                    return True
            if include_runtime and node.loop is not None:
                loop_control = node.loop_control or {}
                loop_var = loop_control.get("loop_var", "item")
                index_var = loop_control.get("index_var")
                if variable_name in (loop_var, index_var):
                    return True
            return False

        runtime_sources = self._runtime_source_ids_by_name(node_id, scope)

        def runtime_defined_on_all_paths(variable_name: str) -> bool:
            source_ids = runtime_sources.get(variable_name, set())
            if not source_ids:
                return False
            paths, paths_truncated = self._positional_paths_to_play(node_id, play_context_id, scope)
            return (
                bool(paths)
                and not paths_truncated
                and all(
                    any(
                        self._runtime_source_applies_to_path(
                            source_id,
                            node_id=node_id,
                            play_context_id=play_context_id,
                            play_scope=scope,
                            positional_scope=set(path),
                        )
                        for source_id in source_ids
                    )
                    for path in paths
                )
            )

        def defined_on_all_paths(
            current_id: str,
            variable_name: str,
            cache: dict[str, bool],
            in_progress: set[str],
        ) -> bool:
            if current_id in cache:
                return cache[current_id]
            if current_id in in_progress:
                return False
            if defines_at(current_id, variable_name, include_runtime=current_id == node_id):
                cache[current_id] = True
                return True
            if current_id == play_context_id:
                value = any(
                    defines_at(ancestor.node_id, variable_name, include_runtime=False)
                    for ancestor in playbook_ancestors
                )
                cache[current_id] = value
                return value

            parents = sorted(
                {
                    parent_id
                    for edge_type in (EdgeType.CONTAINS, EdgeType.INCLUDE, EdgeType.IMPORT)
                    for parent_id, _attrs in self._graph.edges_to(current_id, edge_type)
                    if parent_id in scope
                }
            )
            if not parents:
                cache[current_id] = False
                return False
            in_progress.add(current_id)
            try:
                value = all(defined_on_all_paths(parent_id, variable_name, cache, in_progress) for parent_id in parents)
            finally:
                in_progress.discard(current_id)
            cache[current_id] = value
            return value

        for variable_name in candidates:
            if defined_on_all_paths(node_id, variable_name, {}, set()) or runtime_defined_on_all_paths(variable_name):
                resolved.add(variable_name)

        return resolved

    def _runtime_source_ids_by_name(
        self,
        node_id: str,
        play_scope: set[str] | None,
    ) -> dict[str, set[str]]:
        """Return runtime producers for referenced names, restored per play.

        Graph construction currently wires DATA_FLOW edges from a graph-wide
        name map. A shared task can therefore point to a producer in another
        play even when this play has its own producer for the same name. The
        edge identifies referenced runtime names; when a play scope is known,
        recover matching producers from that scope before resolving values.

        Args:
            node_id: Consumer task node ID.
            play_scope: Optional enclosing-play node IDs.

        Returns:
            Runtime variable names mapped to producer node IDs.
        """
        referenced_names: set[str] = set()
        direct_sources: set[str] = set()
        for source_id, _attrs in self._graph.edges_to(node_id, EdgeType.DATA_FLOW):
            source = self._graph.get_node(source_id)
            if source is None:
                continue
            direct_sources.add(source_id)
            if source.register:
                referenced_names.add(source.register)
            referenced_names.update(source.set_facts)

        result: dict[str, set[str]] = {}
        if play_scope is None:
            source_ids = direct_sources
        else:
            source_ids = set()
            for candidate_id in play_scope:
                candidate = self._graph.get_node(candidate_id)
                if candidate is None:
                    continue
                if candidate.register in referenced_names or referenced_names & candidate.set_facts.keys():
                    source_ids.add(candidate_id)

        for source_id in source_ids:
            source = self._graph.get_node(source_id)
            if source is None:
                continue
            if source.register and source.register in referenced_names:
                result.setdefault(source.register, set()).add(source_id)
            for variable_name in source.set_facts:
                if variable_name in referenced_names:
                    result.setdefault(variable_name, set()).add(source_id)
        return result

    def _order_runtime_sources(self, source_ids: set[str], scope: set[str] | None) -> list[str]:
        """Order runtime producers by execution precedence, not node ID.

        Args:
            source_ids: Applicable runtime producer node IDs.
            scope: Optional node IDs limiting execution-order comparisons.

        Returns:
            Producer IDs in execution order, with deterministic ordering for
            incomparable producers.
        """
        remaining = set(source_ids)
        ordered: list[str] = []
        while remaining:
            earliest = sorted(
                source_id
                for source_id in remaining
                if not any(
                    other_id != source_id and self._execution_reaches(other_id, source_id, scope)
                    for other_id in remaining
                )
            )
            if not earliest:
                # A malformed/cyclic execution graph should not make variable
                # resolution nondeterministic or prevent progress.
                earliest = sorted(remaining)
            ordered.extend(earliest)
            remaining.difference_update(earliest)
        return ordered

    def _runtime_source_context_ids(
        self,
        source_id: str,
        *,
        play_context_id: str | None,
        play_scope: set[str] | None,
    ) -> set[str]:
        """Return producer path nodes up to each include boundary.

        Ordinary play/block tasks are shared execution context for later
        tasks in that play. For producers nested in include/import task files,
        include the producer lineage through its include task but stop there;
        ancestors above that boundary are shared context, not path identity.

        Args:
            source_id: Runtime producer node ID.
            play_context_id: Enclosing play ID, if available.
            play_scope: Optional node IDs limiting traversal to that play.

        Returns:
            Producer and ancestor node IDs below each include/import edge.
            An empty set means the producer is ordinary play/block context.
        """
        stack: list[tuple[str, frozenset[str]]] = [(source_id, frozenset({source_id}))]
        seen: set[tuple[str, frozenset[str]]] = set()
        context_ids: set[str] = set()
        while stack:
            current_id, lineage = stack.pop()
            state = (current_id, lineage)
            if state in seen:
                continue
            seen.add(state)
            for parent_id, attrs in self._graph.edges_to(current_id):
                edge_type = attrs.get("edge_type")
                if edge_type not in {EdgeType.CONTAINS.value, EdgeType.INCLUDE.value, EdgeType.IMPORT.value}:
                    continue
                if play_scope is not None and parent_id not in play_scope:
                    continue
                if edge_type in {EdgeType.INCLUDE.value, EdgeType.IMPORT.value}:
                    context_ids.update(lineage)
                    context_ids.add(parent_id)
                    continue
                parent = self._graph.get_node(parent_id)
                parent_module = (parent.module or "").rsplit(".", maxsplit=1)[-1] if parent is not None else ""
                if parent_module in {
                    "include_role",
                    "include_tasks",
                    "import_role",
                    "import_tasks",
                }:
                    context_ids.update(lineage)
                    context_ids.add(parent_id)
                    continue
                if parent_id != play_context_id:
                    stack.append((parent_id, lineage | {parent_id}))
        return context_ids

    def _positional_paths_to_play(
        self,
        node_id: str,
        play_context_id: str | None,
        scope: set[str],
        *,
        limit: int = _MAX_RUNTIME_PATHS,
    ) -> tuple[list[tuple[str, ...]], bool]:
        """Enumerate positional paths from a node to a play or scoped root.

        Args:
            node_id: Starting task node.
            play_context_id: Optional play where each path should terminate.
            scope: Positional node IDs allowed during traversal.
            limit: Maximum number of paths to enumerate.

        Returns:
            Paths ordered from the node toward the play/root and a truncation
            flag when additional paths exist beyond ``limit``.
        """
        paths: list[tuple[str, ...]] = []
        truncated = False

        def walk(current_id: str, path: tuple[str, ...]) -> None:
            nonlocal truncated
            if len(paths) >= limit:
                truncated = True
                return
            if current_id == play_context_id:
                paths.append(path)
                return
            parents = sorted(
                {
                    parent_id
                    for edge_type in (EdgeType.CONTAINS, EdgeType.INCLUDE, EdgeType.IMPORT)
                    for parent_id, _attrs in self._graph.edges_to(current_id, edge_type)
                    if parent_id in scope and parent_id not in path
                }
            )
            if not parents:
                if play_context_id is None:
                    paths.append(path)
                return
            for parent_id in parents:
                walk(parent_id, (*path, parent_id))

        walk(node_id, (node_id,))
        return paths, truncated

    def _execution_reaches(self, source_id: str, target_id: str, scope: set[str] | None) -> bool:
        """Return whether a source precedes a target in the execution graph.

        Args:
            source_id: Candidate runtime producer.
            target_id: Consumer or include task that begins its path.
            scope: Optional play scope restricting traversal.

        Returns:
            True when an execution-order path exists from source to target.
        """
        if source_id == target_id or (scope is not None and (source_id not in scope or target_id not in scope)):
            return False
        if self._execution_successors is None:
            successors: dict[str, set[str]] = {}
            for edge in self._graph.execution_edges():
                successors.setdefault(edge["source"], set()).add(edge["target"])
            for parent in self._graph.nodes():
                parent_id = parent.node_id
                rescue_children = sorted(
                    self._graph.edges_from(parent_id, EdgeType.RESCUE),
                    key=lambda item: (item[1].get("position", 0), item[0]),
                )
                always_children = sorted(
                    self._graph.edges_from(parent_id, EdgeType.ALWAYS),
                    key=lambda item: (item[1].get("position", 0), item[0]),
                )
                if not rescue_children and not always_children:
                    continue

                def positional_descendants(roots: list[str]) -> set[str]:
                    descendants: set[str] = set()
                    pending = list(roots)
                    while pending:
                        current_id = pending.pop()
                        if current_id in descendants:
                            continue
                        descendants.add(current_id)
                        for edge_type in (EdgeType.CONTAINS, EdgeType.INCLUDE, EdgeType.IMPORT):
                            for child_id, _attrs in self._graph.edges_from(current_id, edge_type):
                                pending.append(child_id)
                    return descendants

                branch_child_ids = {
                    child_id
                    for branch_type in (EdgeType.RESCUE, EdgeType.ALWAYS)
                    for child_id, _attrs in self._graph.edges_from(parent_id, branch_type)
                }
                mainline_children = [
                    child_id
                    for child_id, attrs in self._graph.edges_from(parent_id, EdgeType.CONTAINS)
                    if child_id not in branch_child_ids
                ]
                mainline_nodes = positional_descendants(mainline_children)
                rescue_nodes = positional_descendants([child_id for child_id, _attrs in rescue_children])

                if rescue_children:
                    rescue_first = rescue_children[0][0]
                    rescue_sources = mainline_nodes or {parent_id}
                    for source_id in rescue_sources:
                        if source_id != rescue_first:
                            successors.setdefault(source_id, set()).add(rescue_first)
                for (previous_id, _previous_attrs), (next_id, _next_attrs) in zip(
                    rescue_children, rescue_children[1:], strict=False
                ):
                    successors.setdefault(previous_id, set()).add(next_id)

                if always_children:
                    always_first = always_children[0][0]
                    always_sources = mainline_nodes | rescue_nodes or {parent_id}
                    for source_id in always_sources:
                        if source_id != always_first:
                            successors.setdefault(source_id, set()).add(always_first)
                for (previous_id, _previous_attrs), (next_id, _next_attrs) in zip(
                    always_children, always_children[1:], strict=False
                ):
                    successors.setdefault(previous_id, set()).add(next_id)
            self._execution_successors = successors

        pending = [source_id]
        seen = {source_id}
        while pending:
            current_id = pending.pop()
            for next_id in self._execution_successors.get(current_id, set()):
                if scope is not None and next_id not in scope:
                    continue
                if next_id == target_id:
                    return True
                if next_id not in seen:
                    seen.add(next_id)
                    pending.append(next_id)
        return False

    def _positional_descendant_ids(self, root_id: str, scope: set[str]) -> set[str]:
        """Return descendants reachable from a node through positional edges.

        Args:
            root_id: Node whose positional subtree is selected.
            scope: Node IDs allowed in the subtree.

        Returns:
            The root and all in-scope positional descendants.
        """
        descendants = {root_id}
        pending = [root_id]
        while pending:
            current_id = pending.pop()
            for edge_type in (EdgeType.CONTAINS, EdgeType.INCLUDE, EdgeType.IMPORT):
                for child_id, _attrs in self._graph.edges_from(current_id, edge_type):
                    if child_id in scope and child_id not in descendants:
                        descendants.add(child_id)
                        pending.append(child_id)
        return descendants

    @staticmethod
    def _is_include_task(node: ContentNode | None) -> bool:
        """Return whether a node invokes a task/role include or import.

        Args:
            node: Candidate graph node.

        Returns:
            True when the node invokes an include/import module.
        """
        module = (node.module or "").rsplit(".", maxsplit=1)[-1] if node is not None else ""
        return module in {"include_role", "include_tasks", "import_role", "import_tasks"}

    def resolve_property_origins(
        self,
        node_id: str,
        *,
        play_context_id: str | None = None,
        play_scope: set[str] | None = None,
    ) -> dict[str, PropertyOrigin]:
        """Find the defining scope for each inherited property.

        For ``become``, ``environment``, ``no_log``, etc., walks up the
        ancestry to find the nearest scope where the property is defined.

        Args:
            node_id: Graph node id whose inherited properties are attributed.
            play_context_id: Optional play node id scoping shared included tasks.
            play_scope: Optional precomputed play scope for ``play_context_id``.

        Returns:
            Map from property name to ``PropertyOrigin`` for defined properties only.
        """
        result: dict[str, PropertyOrigin] = {}
        node = self._graph.get_node(node_id)
        if node is None:
            return result

        scope_chain = self._build_scope_chain(node_id, play_context_id, play_scope)

        for prop_name in _INHERITED_PROPERTIES:
            for scope_node in scope_chain:
                value = getattr(scope_node, prop_name, None)
                if value is not None:
                    result[prop_name] = PropertyOrigin(
                        property_name=prop_name,
                        value=value,
                        defining_node_id=scope_node.node_id,
                        file_path=scope_node.file_path,
                        line=scope_node.line_start,
                    )
                    break

        return result

    def _build_scope_chain(
        self,
        node_id: str,
        play_context_id: str | None = None,
        play_scope: set[str] | None = None,
        positional_scope: set[str] | None = None,
    ) -> list[ContentNode]:
        """Build the variable scope chain (self first, root last).

        With ``play_context_id`` set, positional ancestors (CONTAINS,
        INCLUDE, IMPORT) within that play's subtree disambiguate shared
        included tasks; the play's own unscoped ancestors (playbook, root)
        are appended so playbook-level variables keep resolving.

        Args:
            node_id: Node to resolve scope for.
            play_context_id: Optional play node id for shared includes.
            play_scope: Optional precomputed play scope for ``play_context_id``.
            positional_scope: Optional subset of positional nodes for one
                include path; defaults to the full play scope when omitted.

        Returns:
            The node (if present) followed by ancestors toward the root.
        """
        chain: list[ContentNode] = []
        node = self._graph.get_node(node_id)
        if node is not None:
            chain.append(node)
        if play_context_id is not None or positional_scope is not None:
            scope = positional_scope
            if scope is None and play_context_id is not None:
                scope = play_scope if play_scope is not None else self._graph.play_scoped_node_ids(play_context_id)
            if scope is None:
                scope = self._graph.positional_ancestor_ids(node_id) | {node_id}
            chain.extend(self._graph.play_scoped_positional_ancestors(node_id, scope))
            seen = {n.node_id for n in chain}
            if play_context_id is not None:
                for ancestor in self._graph.ancestors(play_context_id):
                    if ancestor.node_id not in seen:
                        seen.add(ancestor.node_id)
                        chain.append(ancestor)
        else:
            chain.extend(self._graph.ancestors(node_id))
        return chain

    def _collect_vars_file_vars(
        self,
        scope_node: ContentNode,
        result: dict[str, VariableProvenance],
    ) -> None:
        """Collect variables from vars_files linked to a scope node.

        Args:
            scope_node: Play, role, or other node with ``VARS_INCLUDE`` outgoing edges.
            result: Mutable provenance map updated in place.
        """
        for target_id, _attrs in self._graph.edges_from(scope_node.node_id, EdgeType.VARS_INCLUDE):
            vf_node = self._graph.get_node(target_id)
            if vf_node is None:
                continue
            for name, value in vf_node.variables.items():
                if name not in result:
                    result[name] = VariableProvenance(
                        name=name,
                        value=value,
                        source=ProvenanceSource.VARS_FILE,
                        defining_node_id=vf_node.node_id,
                        file_path=vf_node.file_path,
                        line=vf_node.line_start,
                    )

    def _collect_vars_file_all(
        self,
        scope_node: ContentNode,
        result: dict[str, list[VariableProvenance]],
    ) -> None:
        """Collect vars_file definitions into a multi-definition map.

        Args:
            scope_node: Scope node with potential ``VARS_INCLUDE`` edges.
            result: Multi-definition map updated in place.
        """
        for target_id, _attrs in self._graph.edges_from(scope_node.node_id, EdgeType.VARS_INCLUDE):
            vf_node = self._graph.get_node(target_id)
            if vf_node is None:
                continue
            for name, value in vf_node.variables.items():
                result.setdefault(name, []).append(
                    VariableProvenance(
                        name=name,
                        value=value,
                        source=ProvenanceSource.VARS_FILE,
                        defining_node_id=vf_node.node_id,
                        file_path=vf_node.file_path,
                        line=vf_node.line_start,
                    )
                )

    def _collect_runtime_vars(
        self,
        node_id: str,
        result: dict[str, VariableProvenance],
        *,
        play_scope: set[str] | None = None,
        play_context_id: str | None = None,
        positional_scope: set[str] | None = None,
    ) -> None:
        """Collect variables from data_flow edges (register/set_fact).

        Args:
            node_id: Consumer task node id.
            result: Mutable provenance map updated in place.
            play_scope: Optional enclosing-play scope used to exclude runtime
                definitions from other plays sharing an included task.
            play_context_id: Optional enclosing play used to check whether a
                runtime producer belongs to the selected include path.
            positional_scope: Optional positional path for the consumer task.
        """
        sources_by_name = self._runtime_source_ids_by_name(node_id, play_scope)
        source_ids = {source_id for ids in sources_by_name.values() for source_id in ids}
        applicable_sources: set[str] = set()
        for source_id in source_ids:
            if positional_scope is not None:
                if not self._runtime_source_applies_to_path(
                    source_id,
                    node_id=node_id,
                    play_context_id=play_context_id,
                    play_scope=play_scope,
                    positional_scope=positional_scope,
                ):
                    continue
            elif (
                play_context_id is not None
                and play_scope is not None
                and not self._runtime_source_applies_to_any_path(
                    source_id,
                    node_id=node_id,
                    play_context_id=play_context_id,
                    play_scope=play_scope,
                )
            ):
                continue
            applicable_sources.add(source_id)

        # Apply sources in execution order so the most recent preceding
        # register/set_fact wins if multiple tasks define the same name.
        for source_id in self._order_runtime_sources(applicable_sources, play_scope):
            source_node = self._graph.get_node(source_id)
            if source_node is None:
                continue
            if source_node.register:
                result[source_node.register] = VariableProvenance(
                    name=source_node.register,
                    value=None,
                    source=ProvenanceSource.RUNTIME,
                    defining_node_id=source_node.node_id,
                    file_path=source_node.file_path,
                    line=source_node.line_start,
                )
            for fact_name in source_node.set_facts:
                result[fact_name] = VariableProvenance(
                    name=fact_name,
                    value=source_node.set_facts.get(fact_name),
                    source=ProvenanceSource.RUNTIME,
                    defining_node_id=source_node.node_id,
                    file_path=source_node.file_path,
                    line=source_node.line_start,
                )

    def _runtime_source_applies_to_path(
        self,
        source_id: str,
        *,
        node_id: str,
        play_context_id: str | None,
        play_scope: set[str] | None,
        positional_scope: set[str],
    ) -> bool:
        """Return whether a runtime source can reach one selected include path.

        Args:
            source_id: Register/set_fact producer node ID.
            node_id: Runtime consumer node ID.
            play_context_id: Enclosing play ID, if available.
            play_scope: Optional full play scope for filtering unrelated plays.
            positional_scope: Positional nodes on one consumer execution path.

        Returns:
            True when the producer is on this include path and executes before
            the consumer, or before its nearest include task.
        """
        if play_scope is not None and source_id not in play_scope:
            return False
        path_scope = play_scope if play_scope is not None else positional_scope
        paths, _paths_truncated = self._positional_paths_to_play(node_id, play_context_id, path_scope)
        path = next((candidate for candidate in paths if set(candidate) == positional_scope), None)
        if path is None:
            return False
        source_context = self._runtime_source_context_ids(
            source_id,
            play_context_id=play_context_id,
            play_scope=play_scope,
        )
        if source_context:
            path_boundaries = [
                ancestor_id
                for ancestor_id in path[1:]
                if ancestor_id != play_context_id and self._is_include_task(self._graph.get_node(ancestor_id))
            ]
            source_boundaries = {
                context_id for context_id in source_context if self._is_include_task(self._graph.get_node(context_id))
            }
            shared_boundaries = [boundary for boundary in path_boundaries if boundary in source_boundaries]
            if shared_boundaries:
                # Use the shared invocation boundary (rather than the
                # consumer's nearest nested include) so producers in an
                # enclosing task file can flow into its nested includes.
                include_boundary = shared_boundaries[0]
                branch_scope = self._positional_descendant_ids(include_boundary, play_scope or positional_scope)
                branch_scope.discard(include_boundary)
                return self._execution_reaches(source_id, node_id, branch_scope)

            # A producer from an earlier include may remain available after
            # returning to the play or from a sibling include. Restrict it to
            # execution before the consumer's include boundary, if any.
            target_id = path_boundaries[0] if path_boundaries else node_id
            return self._execution_reaches(source_id, target_id, play_scope)
        else:
            target_id = next(
                (
                    ancestor_id
                    for ancestor_id in path[1:]
                    if ancestor_id != play_context_id and self._is_include_task(self._graph.get_node(ancestor_id))
                ),
                node_id,
            )
        return self._execution_reaches(source_id, target_id, play_scope)

    def _runtime_source_applies_to_any_path(
        self,
        source_id: str,
        *,
        node_id: str,
        play_context_id: str,
        play_scope: set[str],
    ) -> bool:
        """Return whether a runtime producer is available on any play path.

        Args:
            source_id: Register/set_fact producer node ID.
            node_id: Runtime consumer node ID.
            play_context_id: Enclosing play ID.
            play_scope: Positional node IDs in that play.

        Returns:
            True when at least one execution path can reach the producer first.
        """
        paths, paths_truncated = self._positional_paths_to_play(node_id, play_context_id, play_scope)
        return not paths_truncated and any(
            self._runtime_source_applies_to_path(
                source_id,
                node_id=node_id,
                play_context_id=play_context_id,
                play_scope=play_scope,
                positional_scope=set(path),
            )
            for path in paths
        )

    def _collect_runtime_vars_all(
        self,
        node_id: str,
        result: dict[str, list[VariableProvenance]],
        *,
        play_context_id: str | None = None,
        play_scope: set[str] | None = None,
    ) -> None:
        """Collect runtime definitions into a multi-definition map.

        Args:
            node_id: Consumer task node id.
            result: Multi-definition map updated in place.
            play_context_id: Optional enclosing play used for execution order.
            play_scope: Optional enclosing-play scope used to exclude runtime
                definitions from other plays sharing an included task.
        """
        sources_by_name = self._runtime_source_ids_by_name(node_id, play_scope)
        source_ids = {source_id for ids in sources_by_name.values() for source_id in ids}
        applicable_sources: set[str] = set()
        for source_id in source_ids:
            if (
                play_context_id is not None
                and play_scope is not None
                and not self._runtime_source_applies_to_any_path(
                    source_id,
                    node_id=node_id,
                    play_context_id=play_context_id,
                    play_scope=play_scope,
                )
            ):
                continue
            applicable_sources.add(source_id)

        for source_id in self._order_runtime_sources(applicable_sources, play_scope):
            source_node = self._graph.get_node(source_id)
            if source_node is None:
                continue
            if source_node.register:
                result.setdefault(source_node.register, []).append(
                    VariableProvenance(
                        name=source_node.register,
                        value=None,
                        source=ProvenanceSource.RUNTIME,
                        defining_node_id=source_node.node_id,
                        file_path=source_node.file_path,
                        line=source_node.line_start,
                    )
                )
            for fact_name in source_node.set_facts:
                result.setdefault(fact_name, []).append(
                    VariableProvenance(
                        name=fact_name,
                        value=source_node.set_facts.get(fact_name),
                        source=ProvenanceSource.RUNTIME,
                        defining_node_id=source_node.node_id,
                        file_path=source_node.file_path,
                        line=source_node.line_start,
                    )
                )

    def _collect_loop_vars(
        self,
        node: ContentNode,
        result: dict[str, VariableProvenance],
    ) -> None:
        """Collect loop variables from loop_control on a task node.

        When a task has a loop, Ansible defines a loop variable (default
        ``item``) and optionally an index variable. Custom names are set
        via ``loop_control.loop_var`` and ``loop_control.index_var``.

        Args:
            node: Task or handler node that may have a loop.
            result: Mutable provenance map updated in place.
        """
        if node.loop is None:
            return

        loop_ctrl = node.loop_control or {}
        loop_var = loop_ctrl.get("loop_var", "item")
        if isinstance(loop_var, str):
            result[loop_var] = VariableProvenance(
                name=loop_var,
                value=None,
                source=ProvenanceSource.LOCAL,
                defining_node_id=node.node_id,
                file_path=node.file_path,
                line=node.line_start,
            )

        index_var = loop_ctrl.get("index_var")
        if isinstance(index_var, str):
            result[index_var] = VariableProvenance(
                name=index_var,
                value=None,
                source=ProvenanceSource.LOCAL,
                defining_node_id=node.node_id,
                file_path=node.file_path,
                line=node.line_start,
            )

    def _collect_loop_vars_all(
        self,
        node: ContentNode,
        result: dict[str, list[VariableProvenance]],
    ) -> None:
        """Collect loop variables into a multi-definition map.

        Args:
            node: Task or handler node that may have a loop.
            result: Multi-definition map updated in place.
        """
        if node.loop is None:
            return

        loop_ctrl = node.loop_control or {}
        loop_var = loop_ctrl.get("loop_var", "item")
        if isinstance(loop_var, str):
            result.setdefault(loop_var, []).insert(
                0,
                VariableProvenance(
                    name=loop_var,
                    value=None,
                    source=ProvenanceSource.LOCAL,
                    defining_node_id=node.node_id,
                    file_path=node.file_path,
                    line=node.line_start,
                ),
            )

        index_var = loop_ctrl.get("index_var")
        if isinstance(index_var, str):
            result.setdefault(index_var, []).insert(
                0,
                VariableProvenance(
                    name=index_var,
                    value=None,
                    source=ProvenanceSource.LOCAL,
                    defining_node_id=node.node_id,
                    file_path=node.file_path,
                    line=node.line_start,
                ),
            )
