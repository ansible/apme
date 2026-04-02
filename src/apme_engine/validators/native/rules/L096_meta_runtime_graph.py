"""GraphRule L096: meta/runtime.yml should declare requires_ansible."""

from __future__ import annotations

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult


@dataclass
class MetaRuntimeGraphRule(GraphRule):
    """Flag collections whose parsed runtime metadata omits ``requires_ansible``.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L096"
    description: str = "meta/runtime.yml should declare requires_ansible"
    enabled: bool = True
    name: str = "MetaRuntime"
    version: str = "v0.0.1"
    severity: Severity = Severity.HIGH
    tags: tuple[str, ...] = (Tag.QUALITY,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match COLLECTION nodes with parsed ``meta/runtime.yml`` content.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a COLLECTION with ``collection_meta_runtime`` populated.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type != NodeType.COLLECTION:
            return False
        return bool(node.collection_meta_runtime)

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report a violation when ``requires_ansible`` is missing from runtime metadata.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            ``GraphRuleResult`` with ``verdict`` True when the key is absent,
            ``verdict`` False when present, or None if the node is not applicable.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type != NodeType.COLLECTION:
            return None
        runtime = node.collection_meta_runtime
        if not runtime:
            return None
        rt = runtime if isinstance(runtime, dict) else {}
        if "requires_ansible" in rt:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        detail: YAMLDict = {
            "message": "meta/runtime.yml is missing requires_ansible",
            "missing_key": "requires_ansible",
        }
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
