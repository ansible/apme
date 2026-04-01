"""GraphRule L049: loop variables should use an ``item_`` prefix.

Graph-aware port of ``L049_loop_var_prefix.py``.  Resolves effective
``loop_control`` from the task/handler and ``CONTAINS`` ancestors so
block-level loop variables are recognized.
"""

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, ContentNode, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

DEFAULT_LOOP_VAR = "item"
LOOP_VAR_PREFIX = "item_"


def _effective_loop_control(
    graph: ContentGraph,
    node: ContentNode,
) -> tuple[YAMLDict | None, str | None]:
    """Return the first non-empty ``loop_control`` dict and optional inheriting ancestor id.

    Args:
        graph: Full content graph.
        node: Task or handler with a loop.

    Returns:
        ``(loop_control_dict, inherited_from_node_id)`` where the second
        element is set when ``loop_control`` came from an ancestor, not the node.
    """
    lc = node.loop_control
    if isinstance(lc, dict) and lc:
        return lc, None
    for anc in graph.ancestors(node.node_id):
        alc = anc.loop_control
        if isinstance(alc, dict) and alc:
            return alc, anc.node_id
    return (lc if isinstance(lc, dict) else None), None


@dataclass
class LoopVarPrefixGraphRule(GraphRule):
    """Require loop variables to use the ``item_`` prefix when not default ``item``.

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

    rule_id: str = "L049"
    description: str = "Loop variable should use a prefix (e.g. item_) to avoid shadowing"
    enabled: bool = True
    name: str = "LoopVarPrefix"
    version: str = "v0.0.2"
    severity: Severity = Severity.LOW
    tags: tuple[str, ...] = (Tag.VARIABLE,)
    precedence: int = 10

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match tasks/handlers that declare a loop.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True if the node is a task/handler with ``loop`` set.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return False
        return node.loop is not None

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Check effective ``loop_var`` against the ``item_`` prefix rule.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result with ``loop_var`` / message detail, or None if the node is missing.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        eff_lc, inherited_from = _effective_loop_control(graph, node)
        loop_var: str | None = None
        if eff_lc:
            raw_lv = eff_lc.get("loop_var")
            if isinstance(raw_lv, str):
                loop_var = raw_lv

        detail: YAMLDict = {}
        if inherited_from is not None:
            detail["inherited_loop_control_from"] = inherited_from

        if not loop_var or loop_var == DEFAULT_LOOP_VAR:
            verdict = True
            detail["loop_var"] = loop_var or DEFAULT_LOOP_VAR
            detail["message"] = "use a loop variable with prefix (e.g. item_) to avoid shadowing"
        elif isinstance(loop_var, str) and loop_var.startswith(LOOP_VAR_PREFIX):
            verdict = False
        else:
            verdict = True
            detail["loop_var"] = loop_var
            detail["message"] = "use a loop variable with prefix (e.g. item_) to avoid shadowing"

        return GraphRuleResult(
            verdict=verdict,
            detail=detail if verdict else {},
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
