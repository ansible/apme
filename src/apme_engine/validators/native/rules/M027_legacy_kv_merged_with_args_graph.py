"""GraphRule M027: mixing inline k=v arguments with args: mapping is deprecated (2.23)."""

import re
from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

_KV_INLINE = re.compile(r"\w+=\S")


@dataclass
class LegacyKvMergedWithArgsGraphRule(GraphRule):
    """Detect tasks that mix inline k=v args with an args: mapping.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "M027"
    description: str = "Mixing inline k=v arguments with args: mapping is deprecated (2.23)"
    enabled: bool = True
    name: str = "LegacyKvMergedWithArgs"
    version: str = "v0.0.1"
    severity: str = Severity.LOW
    tags: tuple[str, ...] = (Tag.CODING,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task or handler nodes for inline k=v plus args: checks.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task or handler.
        """
        node = graph.get_node(node_id)
        return node is not None and node.node_type in _TASK_TYPES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report when inline k=v parameters coexist with a non-empty args: map.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result with ``verdict`` True when both patterns appear, or
            None if the node is missing.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        opts = node.options
        if not isinstance(opts, dict):
            opts = {}
        args_val = opts.get("args")
        has_args_key = "args" in opts and isinstance(args_val, dict) and bool(args_val)

        mo = node.module_options
        if not isinstance(mo, dict):
            mo = {}

        raw = mo.get("_raw_params", "")
        has_inline_kv = (isinstance(raw, str) and _KV_INLINE.search(raw) is not None) or any(
            isinstance(val, str) and _KV_INLINE.search(val) is not None for val in mo.values()
        )

        verdict = has_args_key and has_inline_kv

        if not verdict:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        detail: YAMLDict = {
            "message": (
                "Inline k=v args merged with args: mapping is deprecated"
                " in 2.23; move all params into args: or module key"
            )
        }
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
