"""GraphRule L033: detect variables re-defined without conditions via ContentGraph.

Graph-aware port of ``L033_unconditional_override.py``.  Uses effective ``when``
and ``tags`` from the node and its ``CONTAINS`` ancestors so inherited
conditionals and tags protect against false positives.
"""

from dataclasses import dataclass
from typing import cast

from apme_engine.engine.content_graph import ContentGraph, ContentNode, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict, YAMLValue
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})


def _when_strings(when_expr: str | list[str] | None) -> list[str]:
    """Normalize ``when_expr`` to a list of non-empty condition strings.

    Args:
        when_expr: Raw when value from a content node.

    Returns:
        Stripped string conditions, skipping blanks and non-strings.
    """
    if when_expr is None:
        return []
    if isinstance(when_expr, str):
        s = when_expr.strip()
        return [s] if s else []
    return [x.strip() for x in when_expr if isinstance(x, str) and x.strip()]


def _effective_when_tags(
    graph: ContentGraph,
    node: ContentNode,
) -> tuple[bool, bool, str | None]:
    """Determine whether effective ``when`` or ``tags`` apply from node and ancestors.

    Args:
        graph: Full content graph.
        node: Task or handler node.

    Returns:
        Tuple of (has_when, has_tags, inherited_when_from) where
        ``inherited_when_from`` is the nearest ancestor node id that supplies
        ``when`` when the node itself has none, else None.
    """
    own_when = bool(_when_strings(node.when_expr))
    has_when = own_when
    has_tags = bool(node.tags)
    inherited_when_from: str | None = None

    for anc in graph.ancestors(node.node_id):
        if _when_strings(anc.when_expr):
            if not own_when and inherited_when_from is None:
                inherited_when_from = anc.node_id
            has_when = True
        if anc.tags:
            has_tags = True

    return has_when, has_tags, (inherited_when_from if not own_when else None)


def _defined_var_names(node: ContentNode) -> list[str]:
    """Return variable names defined by set_fact keys and ``register`` on a node.

    Args:
        node: Task or handler node.

    Returns:
        Distinct names in stable order (set_facts keys then register if set).
    """
    names: list[str] = []
    seen: set[str] = set()
    for k in node.set_facts:
        if k not in seen:
            seen.add(k)
            names.append(k)
    if node.register and node.register not in seen:
        names.append(node.register)
    return names


def _collect_definers_for_var(graph: ContentGraph, var_name: str) -> list[str]:
    """List node ids that define ``var_name``.

    Checks task/handler ``set_facts`` and ``register``, plus play-level
    ``variables`` so that ``vars:`` blocks count as a definition site.

    Args:
        graph: Full content graph.
        var_name: Variable name to look up.

    Returns:
        Node ids that define the variable.
    """
    definers: list[str] = []
    for n in graph.nodes(node_type=None):
        if n.node_type in _TASK_TYPES:
            if var_name in n.set_facts or n.register == var_name:
                definers.append(n.node_id)
        elif n.node_type == NodeType.PLAY and isinstance(n.variables, dict) and var_name in n.variables:
            definers.append(n.node_id)
    return definers


@dataclass
class UnconditionalOverrideGraphRule(GraphRule):
    """Flag variables re-defined without effective conditions or tags.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
        precedence: Evaluation order (lower = earlier).
    """

    rule_id: str = "L033"
    description: str = "A variable is re-defined without any conditions"
    enabled: bool = True
    name: str = "UnconditionalOverride"
    version: str = "v0.0.2"
    severity: str = Severity.VERY_LOW
    tags: tuple[str, ...] = (Tag.VARIABLE,)
    precedence: int = 10

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match tasks/handlers that define facts or a register variable.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True if the node is a task/handler with ``set_facts`` or ``register``.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return False
        return bool(node.set_facts) or bool(node.register)

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Detect unconditional re-definition when multiple sites define the same name.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result with ``variables`` detail, or None if the node is missing.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        has_when, has_tags, inherited_when_from = _effective_when_tags(graph, node)
        detail: YAMLDict = {"variables": []}

        if has_when or has_tags:
            if inherited_when_from is not None:
                detail["inherited_when_from"] = inherited_when_from
            return GraphRuleResult(
                verdict=False,
                detail=detail,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        defined = _defined_var_names(node)
        if not defined:
            return GraphRuleResult(
                verdict=False,
                detail=detail,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        variables_list: list[YAMLDict] = []
        verdict = False
        for v in defined:
            definers = _collect_definers_for_var(graph, v)
            if len(definers) > 1:
                variables_list.append(
                    {
                        "name": v,
                        "defined_by": cast("list[YAMLValue]", definers),
                    }
                )
                verdict = True

        detail["variables"] = cast("YAMLValue", variables_list)
        return GraphRuleResult(
            verdict=verdict,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
