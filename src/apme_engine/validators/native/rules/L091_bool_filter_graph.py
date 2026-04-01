"""GraphRule L091: detect bare variables in when conditions missing | bool filter.

Graph-aware port of ``L091_bool_filter.py``.
"""

import re
from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

_BARE_VAR_WHEN = re.compile(
    r"when:\s*(\w+)\s*$"
    r"|when:\s*not\s+(\w+)\s*$",
    re.MULTILINE,
)


@dataclass
class BoolFilterGraphRule(GraphRule):
    """Rule for using | bool filter on bare variables in when conditions.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L091"
    description: str = "Use | bool for bare variables in when conditions"
    enabled: bool = True
    name: str = "BoolFilter"
    version: str = "v0.0.1"
    severity: Severity = Severity.LOW
    tags: tuple[str, ...] = (Tag.CODING,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task or handler nodes.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task or handler.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        return node.node_type in _TASK_TYPES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Check for bare variables in when without | bool.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with ``bare_variables`` / ``message`` when violated, else pass.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        yaml_lines = getattr(node, "yaml_lines", "") or ""
        matches = _BARE_VAR_WHEN.findall(yaml_lines)
        found = [m[0] or m[1] for m in matches if m[0] or m[1]]
        found = [f for f in found if f not in ("true", "false", "yes", "no")]
        verdict = len(found) > 0
        detail: YAMLDict | None = None
        if found:
            detail = {
                "bare_variables": found,
                "message": "use | bool filter for bare variables in when conditions",
            }
        return GraphRuleResult(
            verdict=verdict,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
