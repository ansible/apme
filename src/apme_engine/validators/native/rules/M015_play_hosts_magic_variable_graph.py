"""GraphRule M015: play_hosts magic variable is deprecated (removed in 2.23).

Graph-aware port of ``M015_play_hosts_magic_variable.py``.
"""

import re
from dataclasses import dataclass
from typing import cast

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

_PLAY_HOSTS_REF = re.compile(r"\bplay_hosts\b")


@dataclass
class PlayHostsMagicVariableGraphRule(GraphRule):
    """Detect deprecated play_hosts variable usage.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "M015"
    description: str = "Use ansible_play_batch instead of deprecated play_hosts variable (removed in 2.23)"
    enabled: bool = True
    name: str = "PlayHostsMagicVariable"
    version: str = "v0.0.1"
    severity: str = Severity.MEDIUM
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
        """Scan Jinja2 expressions for deprecated play_hosts variable.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with ``message`` / ``replacement`` when violated; else pass.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        yaml_lines = getattr(node, "yaml_lines", "") or ""
        options = getattr(node, "options", None) or {}
        module_options = getattr(node, "module_options", None) or {}
        all_text_parts = [yaml_lines]
        for v in list(options.values()) + list(module_options.values()):
            if isinstance(v, str):
                all_text_parts.append(v)
        text = " ".join(all_text_parts)
        found = bool(_PLAY_HOSTS_REF.search(text))
        detail: YAMLDict | None = None
        if found:
            detail = cast(
                YAMLDict,
                {
                    "message": "play_hosts is deprecated in 2.23; use ansible_play_batch",
                    "replacement": "ansible_play_batch",
                },
            )
        return GraphRuleResult(
            verdict=found,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
