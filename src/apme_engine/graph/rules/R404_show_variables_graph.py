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

from apme_engine.graph.content_graph import ContentGraph, ContentNode, NodeType
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
    no_log_true_in_scope,
)
from apme_engine.graph.variable_provenance import VariableProvenance, VariableProvenanceResolver

_MAX_VARIABLE_SET = 500


def _find_enclosing_play(graph: ContentGraph, node_id: str) -> ContentNode | None:
    """Walk ancestors to find the nearest PLAY node.

    Args:
        graph: The content graph.
        node_id: Starting node.

    Returns:
        The enclosing play ``ContentNode``, or ``None``.
    """
    for ancestor in graph.ancestors(node_id):
        if ancestor.node_type == NodeType.PLAY:
            return ancestor
    return None


def _play_no_log_context(graph: ContentGraph, task_node_id: str) -> tuple[str | None, set[str] | None]:
    """Return play context used to resolve ``no_log`` for a task.

    Args:
        graph: ContentGraph under scan.
        task_node_id: Task node being reported.

    Returns:
        Tuple of ``(play_context_id, play_scope)`` for ``no_log_true_in_scope``.
    """
    play = _find_enclosing_play(graph, task_node_id)
    if play is None:
        return None, None
    return play.node_id, graph.play_scoped_node_ids(play.node_id)


def _should_redact_value(graph: ContentGraph, task_node_id: str, prov: VariableProvenance) -> bool:
    """Return True when a variable value must be redacted in audit output.

    Args:
        graph: ContentGraph under scan.
        task_node_id: Task node being reported.
        prov: Resolved variable provenance entry.

    Returns:
        True when the value should be fully hidden rather than shape-preserved.
    """
    if prov.defining_node_id:
        play_context_id, play_scope = _play_no_log_context(graph, task_node_id)
        if no_log_true_in_scope(
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
        resolved = resolver.resolve_variables(node_id)
        play_context_id, play_scope = _play_no_log_context(graph, node_id)
        task_no_log = no_log_true_in_scope(
            graph,
            node_id,
            play_context_id=play_context_id,
            play_scope=play_scope,
        )

        if not resolved:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        var_list: list[YAMLValue] = []
        for prov in sorted(resolved.values(), key=lambda p: p.name):
            redact = task_no_log or _should_redact_value(graph, node_id, prov)
            var_list.append(
                cast(
                    YAMLValue,
                    {
                        "name": _display_var_name(prov),
                        "value": _serialize_value(prov.value, redact=redact),
                        "source": prov.source.value,
                    },
                )
            )
        total_vars = len(var_list)
        truncated = total_vars > _MAX_VARIABLE_SET
        if truncated:
            var_list = var_list[:_MAX_VARIABLE_SET]
        detail: YAMLDict = {
            "message": f"Task has {total_vars} variable(s) in scope" + (" (truncated)" if truncated else ""),
            "variable_set": cast(YAMLValue, var_list),
        }
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
