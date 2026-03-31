"""GraphRule L098: duplicate YAML mapping keys at the same indent."""

import re
from dataclasses import dataclass
from typing import cast

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

_KEY_LINE = re.compile(r"^(\s*)([^\s#:][^:]*?)\s*:")


@dataclass
class YamlKeyDuplicatesGraphRule(GraphRule):
    """Detect duplicate keys at the same indentation level in raw YAML.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L098"
    description: str = "YAML files should not have duplicate mapping keys"
    enabled: bool = True
    name: str = "YamlKeyDuplicates"
    version: str = "v0.0.1"
    severity: str = Severity.HIGH
    tags: tuple[str, ...] = (Tag.CODING,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task or handler nodes that have raw YAML text.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task or handler with non-empty ``yaml_lines``.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return False
        return bool(node.yaml_lines)

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report duplicate mapping keys at the same indent.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result with ``duplicates`` when violated, else pass.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        raw = node.yaml_lines or ""
        if not raw:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        seen: dict[tuple[int, str], int] = {}
        duplicates: list[str] = []
        for line in raw.splitlines():
            m = _KEY_LINE.match(line)
            if not m:
                continue
            indent_len = len(m.group(1))
            key = m.group(2).strip()
            loc = (indent_len, key)
            if loc in seen:
                duplicates.append(f"duplicate key '{key}' at indent {indent_len}")
            seen[loc] = seen.get(loc, 0) + 1

        if not duplicates:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        detail = cast(
            YAMLDict,
            {
                "duplicates": duplicates,
                "message": "; ".join(duplicates),
            },
        )
        return GraphRuleResult(
            verdict=True,
            node_id=node_id,
            file=(node.file_path, node.line_start),
            detail=detail,
        )
