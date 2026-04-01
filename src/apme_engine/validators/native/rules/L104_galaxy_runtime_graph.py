"""GraphRule L104: collection should have meta/runtime.yml."""

from __future__ import annotations

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult


def _has_meta_runtime(files: list[str]) -> bool:
    """Return True if exactly ``meta/runtime.yml`` (or ``.yaml``) exists.

    Paths are relative to the collection root, so the only valid matches are
    ``meta/runtime.yml`` and ``meta/runtime.yaml`` (case-insensitive).
    Nested paths like ``vendor/ns/col/meta/runtime.yml`` are not the
    collection's own runtime file.

    Args:
        files: Relative paths within the collection root.

    Returns:
        True when the collection's own runtime file is present.
    """
    for raw in files:
        lower = raw.replace("\\", "/").lower()
        if lower in ("meta/runtime.yml", "meta/runtime.yaml"):
            return True
    return False


@dataclass
class GalaxyRuntimeGraphRule(GraphRule):
    """Flag collections missing ``meta/runtime.yml`` (or ``.yaml``).

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L104"
    description: str = "Collection should have meta/runtime.yml"
    enabled: bool = True
    name: str = "GalaxyRuntime"
    version: str = "v0.0.1"
    severity: Severity = Severity.MEDIUM
    tags: tuple[str, ...] = (Tag.QUALITY,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match COLLECTION nodes that carry a non-empty file listing.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a COLLECTION with ``collection_files`` populated.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type != NodeType.COLLECTION:
            return False
        return bool(node.collection_files)

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report a violation when ``meta/runtime.yml`` is not in the file listing.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            ``GraphRuleResult`` with ``verdict`` True when runtime file is missing,
            ``verdict`` False when present, or None if the node is not applicable.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type != NodeType.COLLECTION or not node.collection_files:
            return None
        files = list(node.collection_files)
        if _has_meta_runtime(files):
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        detail: YAMLDict = {
            "message": "meta/runtime.yml not found at collection root",
            "expected_paths": ["meta/runtime.yml", "meta/runtime.yaml"],
        }
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
