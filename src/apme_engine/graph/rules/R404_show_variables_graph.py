"""GraphRule R404: expose the resolved variable set for each task.

Informational/debug rule (severity INFO) that reports the variable
names, sources, and redacted values visible in the scope of a task node
via ``VariableProvenanceResolver``. Intended for development and
troubleshooting workflows.

This rule is disabled by default — enable by including ``R404`` in the
rule_id_list when loading rules.
"""

from dataclasses import dataclass
from typing import cast

from apme_engine.graph.content_graph import ContentGraph, EdgeType
from apme_engine.graph.rule_base import GraphRule, GraphRuleResult
from apme_engine.graph.sensitivity import (
    REDACTED,
    redact_sensitive_structure,
    var_looks_sensitive,
)
from apme_engine.graph.types import RuleTag as Tag
from apme_engine.graph.types import Severity, YAMLDict, YAMLValue
from apme_engine.graph.variable_helpers import (
    TASK_TYPES,
    enclosing_play_ids,
    no_log_true_in_scope,
)
from apme_engine.graph.variable_provenance import VariableProvenance, VariableProvenanceResolver

_MAX_VARIABLE_SET = 500
_MAX_EXECUTION_PATHS = 64


@dataclass(frozen=True, slots=True)
class _ExecutionContext:
    """One play and positional include path by which a task can execute.

    Attributes:
        play_context_id: Enclosing play ID, if the task belongs to a play.
        play_scope: All nodes reachable within the enclosing play.
        positional_scope: Nodes on this specific include path.
        path_ids: Node IDs from the task up to its play or graph root.
    """

    play_context_id: str | None
    play_scope: set[str] | None
    positional_scope: set[str]
    path_ids: tuple[str, ...]


def _positional_paths(
    graph: ContentGraph,
    task_node_id: str,
    *,
    scope: set[str],
    stop_at: str | None = None,
    limit: int = _MAX_EXECUTION_PATHS,
) -> tuple[list[tuple[str, ...]], bool]:
    """Enumerate positional paths from a task to a play or graph root.

    Args:
        graph: ContentGraph under scan.
        task_node_id: Task whose incoming positional paths are enumerated.
        scope: Node IDs permitted in the path.
        stop_at: Optional enclosing play where each path ends.
        limit: Maximum number of paths to return.

    Returns:
        Paths ordered from task toward their play/root and whether more paths
        were omitted at the limit.
    """
    paths: list[tuple[str, ...]] = []
    truncated = False

    def walk(current_id: str, path: tuple[str, ...]) -> None:
        nonlocal truncated
        if len(paths) >= limit:
            truncated = True
            return
        if current_id == stop_at:
            paths.append(path)
            return
        parents = sorted(
            parent_id
            for parent_id, attrs in graph.edges_to(current_id)
            if attrs.get("edge_type") in {EdgeType.CONTAINS.value, EdgeType.INCLUDE.value, EdgeType.IMPORT.value}
            and parent_id in scope
            and parent_id not in path
        )
        if not parents:
            if stop_at is None:
                paths.append(path)
            return
        for parent_id in parents:
            walk(parent_id, (*path, parent_id))

    walk(task_node_id, (task_node_id,))
    return paths, truncated


def _play_contexts(
    graph: ContentGraph,
    task_node_id: str,
) -> tuple[list[_ExecutionContext], bool]:
    """Return every enclosing play context and positional include path.

    Args:
        graph: ContentGraph under scan.
        task_node_id: Task node being reported.

    Returns:
        One context per play/include path, or unscoped contexts for standalone
        task files, plus a flag indicating whether paths were capped.
    """
    play_ids = enclosing_play_ids(graph, task_node_id)
    if not play_ids:
        scope = graph.positional_ancestor_ids(task_node_id) | {task_node_id}
        paths, truncated = _positional_paths(graph, task_node_id, scope=scope)
        standalone_contexts = [_ExecutionContext(None, None, set(path), path) for path in paths]
        return standalone_contexts or [_ExecutionContext(None, None, scope, (task_node_id,))], truncated

    contexts: list[_ExecutionContext] = []
    truncated = False
    for play_id in play_ids:
        play_scope = graph.play_scoped_node_ids(play_id)
        paths, play_truncated = _positional_paths(
            graph,
            task_node_id,
            scope=play_scope,
            stop_at=play_id,
            limit=max(_MAX_EXECUTION_PATHS - len(contexts), 1),
        )
        truncated = truncated or play_truncated
        contexts.extend(_ExecutionContext(play_id, play_scope, set(path), path) for path in paths)
        if len(contexts) >= _MAX_EXECUTION_PATHS:
            truncated = truncated or len(play_ids) > 1 or play_truncated
            break
    return contexts, truncated


def _should_redact_value(
    graph: ContentGraph,
    task_node_id: str,
    prov: VariableProvenance,
    play_context_id: str | None = None,
    play_scope: set[str] | None = None,
) -> bool:
    """Return True when a variable value must be redacted in audit output.

    Args:
        graph: ContentGraph under scan.
        task_node_id: Task node being reported.
        prov: Resolved variable provenance entry.
        play_context_id: Play context used to resolve the task.
        play_scope: Precomputed scope for ``play_context_id``.

    Returns:
        True when the value should be fully hidden rather than shape-preserved.
    """
    if play_context_id is None and play_scope is None:
        contexts, _truncated = _play_contexts(graph, task_node_id)
        if contexts:
            context = contexts[0]
            play_context_id = context.play_context_id
            play_scope = context.play_scope
    if prov.defining_node_id and no_log_true_in_scope(
        graph,
        prov.defining_node_id,
        play_context_id=play_context_id,
        play_scope=play_scope,
    ):
        return True
    return var_looks_sensitive(prov.name)


def _display_var_name(prov: VariableProvenance) -> str:
    """Return a safe variable name for audit output.

    Args:
        prov: Resolved variable provenance entry.

    Returns:
        Variable name or ``[REDACTED]`` when the name looks sensitive.
    """
    return REDACTED if var_looks_sensitive(prov.name) else prov.name


def _redact_sensitive_keys(value: object, *, _depth: int = 0, _max_depth: int = 32) -> object:
    """Recursively redact nested values while preserving overall structure.

    Walks dicts and lists, replacing sensitive-keyed values and all scalar
    leaves with ``[REDACTED]`` so R404 can expose scope without cleartext.

    Args:
        value: Arbitrary nested structure from variable provenance.
        _depth: Current recursion depth (internal).
        _max_depth: Maximum nesting depth before redacting entirely.

    Returns:
        Structure with sensitive-keyed values replaced.
    """
    return redact_sensitive_structure(
        value,
        redact_all_scalars=True,
        depth=_depth,
        max_depth=_max_depth,
    )


def _serialize_value(value: object, *, redact: bool = False) -> str | object | None:
    """Serialize a variable value for audit output.

    Dicts and lists return redacted native structures (outer audit metadata
    serialization performs JSON). Scalars are always returned as
    ``[REDACTED]`` to avoid cleartext secret persistence.

    Args:
        value: Variable value from provenance.
        redact: When True, omit the cleartext value entirely.

    Returns:
        String, structure, or None for None values.
    """
    if redact:
        return REDACTED
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return _redact_sensitive_keys(value)
    return REDACTED


@dataclass
class ShowVariablesGraphRule(GraphRule):
    """Expose the full variable set available to a task for debugging.

    Reports every variable name, a redacted value placeholder/structure,
    and the provenance source (local, play, role_default, etc.) for each
    task/handler node.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "R404"
    description: str = "Expose variable_set for the task"
    enabled: bool = False
    name: str = "ShowVariables"
    version: str = "v0.0.1"
    severity: Severity = Severity.INFO
    tags: tuple[str, ...] = (Tag.DEBUG,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task and handler nodes.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True for task and handler nodes.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        return node.node_type in TASK_TYPES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Resolve and report all variables in scope for this task.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the task node.

        Returns:
            GraphRuleResult with ``variable_set`` list, or None if node missing.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        resolver = VariableProvenanceResolver(graph)
        play_contexts, paths_truncated = _play_contexts(graph, node_id)
        resolved_by_context: list[tuple[_ExecutionContext, dict[str, VariableProvenance]]] = []
        for context in play_contexts:
            resolved = resolver.resolve_variables(
                node_id,
                play_context_id=context.play_context_id,
                play_scope=context.play_scope,
                positional_scope=context.positional_scope,
            )
            resolved_by_context.append((context, resolved))

        if not any(resolved for _context, resolved in resolved_by_context):
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        var_list: list[YAMLValue] = []
        include_play = len({context.play_context_id for context in play_contexts}) > 1
        include_path = len(play_contexts) > 1
        for context, resolved in resolved_by_context:
            if context.play_context_id is None:
                task_no_log = no_log_true_in_scope(graph, node_id)
            else:
                task_no_log = no_log_true_in_scope(
                    graph,
                    node_id,
                    play_context_id=context.play_context_id,
                    play_scope=context.positional_scope,
                )
            play_node = graph.get_node(context.play_context_id) if context.play_context_id is not None else None
            for prov in sorted(resolved.values(), key=lambda p: p.name):
                redact = task_no_log or _should_redact_value(
                    graph,
                    node_id,
                    prov,
                    context.play_context_id,
                    context.play_scope,
                )
                entry: YAMLDict = {
                    "name": _display_var_name(prov),
                    "value": cast(YAMLValue, _serialize_value(prov.value, redact=redact)),
                    "source": prov.source.value,
                }
                if include_play and play_node is not None:
                    entry["play"] = str(play_node.identity.path)
                if include_path:
                    path = [
                        str(path_node.identity.path)
                        for path_node_id in reversed(context.path_ids)
                        if (path_node := graph.get_node(path_node_id)) is not None
                    ]
                    entry["execution_path"] = cast(YAMLValue, path)
                var_list.append(cast(YAMLValue, entry))
        total_vars = len(var_list)
        truncated = total_vars > _MAX_VARIABLE_SET
        if truncated:
            var_list = var_list[:_MAX_VARIABLE_SET]
        detail: YAMLDict = {
            "message": f"Task has {total_vars} variable(s) in scope" + (" (truncated)" if truncated else ""),
            "variable_set": cast(YAMLValue, var_list),
        }
        if paths_truncated:
            detail["execution_paths_truncated"] = True
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
