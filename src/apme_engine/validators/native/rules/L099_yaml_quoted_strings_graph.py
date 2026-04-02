"""GraphRule L099: prefer double quotes for YAML string values."""

import re
from dataclasses import dataclass
from typing import cast

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

_SINGLE_QUOTED_VALUE = re.compile(r":\s+'[^']*'\s*$")


@dataclass
class YamlQuotedStringsGraphRule(GraphRule):
    """Detect single-quoted scalar values (prefer double quotes or unquoted).

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L099"
    description: str = "Prefer double quotes for YAML string values"
    enabled: bool = True
    name: str = "YamlQuotedStrings"
    version: str = "v0.0.1"
    severity: Severity = Severity.INFO
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
        """Flag lines with single-quoted values after ``:``.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result with ``single_quoted_lines`` when violated, else pass.
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

        single_quoted_lines: list[int] = []
        for i, line in enumerate(raw.splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if _SINGLE_QUOTED_VALUE.search(line):
                single_quoted_lines.append(i)

        if not single_quoted_lines:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        detail = cast(
            YAMLDict,
            {
                "single_quoted_lines": single_quoted_lines[:10],
                "message": f"found {len(single_quoted_lines)} single-quoted string(s); prefer double quotes",
            },
        )
        return GraphRuleResult(
            verdict=True,
            node_id=node_id,
            file=(node.file_path, node.line_start),
            detail=detail,
        )
