"""GraphRule M022: tree / oneline callback plugins removed in 2.23.

Graph-aware port of ``M022_tree___oneline_callback_plugins.py``.  Scans task
options, module arguments, and environment (local plus ancestor scopes) for
references to removed stdout callbacks.
"""

import re
from dataclasses import dataclass
from typing import cast

from apme_engine.engine.content_graph import ContentGraph, ContentNode, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict, YAMLValue
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

_REMOVED_CALLBACKS = frozenset({"tree", "oneline"})
_CALLBACK_REF = re.compile(r"\b(?:stdout_callback|callback_whitelist|callbacks_enabled)\s*[=:]\s*(\w+)")


def _string_values_from_mapping(mapping: YAMLDict) -> str:
    """Concatenate string values from a shallow mapping.

    Args:
        mapping: Options or module arguments mapping.

    Returns:
        Space-separated string fragments from string values.
    """
    parts: list[str] = []
    for v in mapping.values():
        if isinstance(v, str):
            parts.append(v)
    return " ".join(parts)


def _scope_chain(graph: ContentGraph, node_id: str) -> list[ContentNode]:
    """Return the node followed by CONTAINS ancestors (parent toward root).

    Args:
        graph: Content graph.
        node_id: Starting node id.

    Returns:
        Ordered scope chain, empty when the node is missing.
    """
    node = graph.get_node(node_id)
    if node is None:
        return []
    return [node] + graph.ancestors(node_id)


@dataclass
class TreeOnelineCallbackPluginsGraphRule(GraphRule):
    """Detect removed ``tree`` / ``oneline`` callback references.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "M022"
    description: str = "tree and oneline callback plugins are removed in 2.23"
    enabled: bool = True
    name: str = "TreeOnelineCallbackPlugins"
    version: str = "v0.0.2"
    severity: Severity = Severity.MEDIUM
    tags: tuple[str, ...] = (Tag.CODING,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task and handler nodes for callback inspection.

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
        """Search options, module args, and merged environment scopes for removed callbacks.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result; ``verdict`` True when a removed callback is referenced.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        all_text = _string_values_from_mapping(node.options) + " " + _string_values_from_mapping(node.module_options)

        found: set[str] = set()
        for m in _CALLBACK_REF.finditer(all_text):
            cb = m.group(1).strip()
            if cb in _REMOVED_CALLBACKS:
                found.add(cb)

        _CALLBACK_KEYS = {"stdout_callback", "callback_whitelist", "callbacks_enabled"}
        for mapping in (node.options, node.module_options):
            for key, val in mapping.items():
                if key in _CALLBACK_KEYS and isinstance(val, str) and val in _REMOVED_CALLBACKS:
                    found.add(val)

        for scope in _scope_chain(graph, node_id):
            env_raw = scope.environment
            if not isinstance(env_raw, dict):
                continue
            val = env_raw.get("ANSIBLE_STDOUT_CALLBACK", "")
            if isinstance(val, str) and val in _REMOVED_CALLBACKS:
                found.add(val)

        if not found:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        detail: YAMLDict = {
            "message": f"Removed callback plugin(s): {', '.join(sorted(found))}",
            "removed_callbacks": cast("YAMLValue", sorted(found)),
        }
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
