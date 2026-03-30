"""GraphRule M030: detect ``when`` expressions that fail Jinja2 parsing.

Graph-aware port of ``M030_broken_conditional_expressions.py``.  Collects
``when`` conditions from the task/handler and ``CONTAINS`` ancestors so
inherited conditionals are validated the same as local ones.
"""

from dataclasses import dataclass
from typing import cast

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict, YAMLValue
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

try:
    from jinja2 import Environment as _Env  # type: ignore[import-not-found]

    _JINJA_ENV = _Env()
    HAS_JINJA = True
except ImportError:
    HAS_JINJA = False
    _JINJA_ENV = None

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})


def _when_strings(when_expr: str | list[str] | None) -> list[str]:
    """Normalize ``when_expr`` to non-empty string conditions.

    Args:
        when_expr: Raw when value from a content node.

    Returns:
        Stripped string conditions suitable for Jinja parsing.
    """
    if when_expr is None:
        return []
    if isinstance(when_expr, str):
        s = when_expr.strip()
        return [s] if s else []
    return [x.strip() for x in when_expr if isinstance(x, str) and x.strip()]


def _collect_when_with_scope(graph: ContentGraph, node_id: str) -> list[tuple[str, str]]:
    """List (condition, defining_node_id) pairs from a node and ancestors.

    Args:
        graph: Full content graph.
        node_id: Task or handler node id.

    Returns:
        Pairs in order: node first, then ancestors (parent toward root).
    """
    out: list[tuple[str, str]] = []
    node = graph.get_node(node_id)
    if node is None:
        return out
    for cond in _when_strings(node.when_expr):
        out.append((cond, node.node_id))
    for anc in graph.ancestors(node_id):
        for cond in _when_strings(anc.when_expr):
            out.append((cond, anc.node_id))
    return out


@dataclass
class BrokenConditionalExpressionsGraphRule(GraphRule):
    """Flag ``when`` values that are not valid Jinja2 expressions.

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

    rule_id: str = "M030"
    description: str = "Broken conditional expressions will error in 2.23"
    enabled: bool = True
    name: str = "BrokenConditionalExpressions"
    version: str = "v0.0.2"
    severity: str = Severity.MEDIUM
    tags: tuple[str, ...] = (Tag.CODING,)
    precedence: int = 10

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match tasks/handlers when Jinja2 is available.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True if Jinja2 is installed and the node is a task or handler.
        """
        if not HAS_JINJA:
            return False
        node = graph.get_node(node_id)
        if node is None:
            return False
        return node.node_type in _TASK_TYPES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Parse each effective ``when`` string as Jinja2 and collect failures.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result with ``broken_conditions`` detail, or None if the node is missing.
        """
        node = graph.get_node(node_id)
        if node is None or _JINJA_ENV is None:
            return None

        scoped = _collect_when_with_scope(graph, node_id)
        if not scoped:
            return GraphRuleResult(
                verdict=False,
                detail={},
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        broken_entries: list[YAMLDict] = []
        for cond, definer_id in scoped:
            try:
                _JINJA_ENV.parse("{{ " + cond + " }}")
            except Exception:
                broken_entries.append(
                    {
                        "condition": cond,
                        "defined_at": definer_id,
                    }
                )

        verdict = len(broken_entries) > 0
        detail: YAMLDict = {}
        if broken_entries:
            conds_only = [str(e["condition"]) for e in broken_entries]
            detail["message"] = f"Broken conditional(s) will error in 2.23: {conds_only}"
            detail["broken_conditions"] = cast("YAMLValue", broken_entries)

        return GraphRuleResult(
            verdict=verdict,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
