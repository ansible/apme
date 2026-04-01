"""GraphRule L041: task keys should follow canonical order (e.g. name before module)."""

import re
from dataclasses import dataclass
from typing import cast

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})


def _top_level_keys_from_yaml(yaml_lines: str) -> list[str]:
    """Return top-level task mapping keys in source order.

    Only keys at the task mapping indent level are returned; nested
    module-argument keys (deeper indentation) are skipped.

    Args:
        yaml_lines: Raw YAML lines of the task.

    Returns:
        List of top-level task keys in source order.
    """
    keys: list[str] = []
    key_indent: int | None = None
    for line in yaml_lines.splitlines():
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(stripped)
        content = stripped
        if content.startswith("- ") and key_indent is None:
            content = content[2:].lstrip()
            key_indent = indent + 2
        elif key_indent is None:
            key_indent = indent
        elif indent != key_indent:
            continue
        match = re.match(r"^([\w.]+)\s*:", content)
        if match:
            keys.append(match.group(1))
    return keys


def _first_action_key(keys: list[str], module_name: str) -> str | None:
    """Return the first key that looks like a module/action key.

    Args:
        keys: List of task keys in source order.
        module_name: Resolved or declared module name.

    Returns:
        First action-like key, or None.
    """
    action_like = {"local_action", "action"}
    for k in keys:
        if k in action_like or k == module_name or (module_name and module_name.split(".")[-1] == k):
            return k
    return None


@dataclass
class KeyOrderGraphRule(GraphRule):
    """Ensure ``name`` appears before the module/action key when both exist.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L041"
    description: str = "Task keys should follow canonical order (e.g. name before module)"
    enabled: bool = True
    name: str = "KeyOrder"
    version: str = "v0.0.1"
    severity: str = Severity.VERY_LOW
    tags: tuple[str, ...] = (Tag.QUALITY,)

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
        """Report when ``name`` appears after the action/module key.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result with key-order detail when violated, else pass.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        yaml_text = node.yaml_lines or ""
        if not yaml_text.strip():
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        keys = _top_level_keys_from_yaml(yaml_text)
        if not keys:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        module_name = node.module or ""
        first_action = _first_action_key(keys, module_name)
        if not first_action:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        action_index = keys.index(first_action) if first_action in keys else -1
        name_index = keys.index("name") if "name" in keys else -1
        violated = name_index > action_index if (name_index >= 0 and action_index >= 0) else False
        if not violated:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        detail = cast(
            YAMLDict,
            {
                "keys_order": keys,
                "message": "name should appear before the action/module key",
            },
        )
        return GraphRuleResult(
            verdict=True,
            node_id=node_id,
            file=(node.file_path, node.line_start),
            detail=detail,
        )
