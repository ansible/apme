"""GraphRule L078: detect dot notation for dict access in Jinja; prefer bracket notation.

Graph-aware port of ``L078_dot_notation.py``.
"""

import re
from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

_DOT_ACCESS = re.compile(
    r"\bitem\.\w+"
    r"|\bresult\.\w+"
    r"|\boutput\.\w+"
    r"|\bhostvars\.\w+"
    r"|\bgroups\.\w+"
)


@dataclass
class DotNotationGraphRule(GraphRule):
    """Rule for detecting dot notation in Jinja dict access.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L078"
    description: str = "Use bracket notation for dict key access in Jinja"
    enabled: bool = True
    name: str = "DotNotation"
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
        """Check for dot notation dict access in Jinja and return result.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with ``found_patterns`` / ``message`` when violated, else pass.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        yaml_lines = getattr(node, "yaml_lines", "") or ""
        found = sorted(set(_DOT_ACCESS.findall(yaml_lines)))
        verdict = len(found) > 0
        detail: YAMLDict | None = None
        if found:
            detail = {
                "found_patterns": found,
                "message": "use bracket notation (e.g. item['key']) instead of dot notation",
            }
        return GraphRuleResult(
            verdict=verdict,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
