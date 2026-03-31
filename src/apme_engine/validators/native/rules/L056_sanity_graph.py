"""GraphRule L056: detect files in paths that should be excluded from lint/sanity."""

import re
from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleScope, Severity, YAMLDict
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_MATCH_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER, NodeType.ROLE})

_SANITY_IGNORE_PATTERNS = [
    re.compile(r"\.git/"),
    re.compile(r"/\.ansible/"),
    re.compile(r"\.pyc$"),
    re.compile(r"__pycache__"),
]


@dataclass
class SanityGraphRule(GraphRule):
    """Flag nodes whose ``file_path`` matches common ignore patterns.

    Files inside ``.git/``, ``.ansible/``, ``__pycache__``, or ``.pyc``
    paths should typically be excluded from scanning.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
        scope: Structural scope.
    """

    rule_id: str = "L056"
    description: str = "File may be in a path that should be excluded from lint/sanity"
    enabled: bool = True
    name: str = "Sanity"
    version: str = "v0.0.1"
    severity: str = Severity.VERY_LOW
    tags: tuple[str, ...] = (Tag.QUALITY,)
    scope: str = RuleScope.PLAYBOOK

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task, handler, and role nodes.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True for task, handler, or role nodes.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        return node.node_type in _MATCH_TYPES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Flag when the node's file path matches a sanity ignore pattern.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult, or None if node is missing.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        file_path = node.file_path
        verdict = any(p.search(file_path) for p in _SANITY_IGNORE_PATTERNS)

        detail: YAMLDict | None = None
        if verdict:
            detail = {
                "path": file_path,
                "message": "path matches common ignore pattern; consider excluding from scan",
            }

        return GraphRuleResult(
            verdict=verdict,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
