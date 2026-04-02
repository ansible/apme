"""GraphRule L105: galaxy.yml should have a repository key."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult


def _repository_nonempty(meta: Mapping[str, object]) -> bool:
    """Return True if ``repository`` is present and non-empty.

    Handles both flat ``galaxy.yml`` metadata (``repository`` at top level) and
    ``MANIFEST.json`` metadata (``repository`` nested under ``collection_info``).

    Args:
        meta: Parsed metadata mapping (``galaxy.yml`` or ``MANIFEST.json``).

    Returns:
        True when ``repository`` is set to a non-empty value.
    """
    ci = meta.get("collection_info")
    source = ci if isinstance(ci, Mapping) else meta

    if "repository" not in source:
        return False
    value = source["repository"]
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


@dataclass
class GalaxyRepositoryGraphRule(GraphRule):
    """Flag collections whose ``galaxy.yml`` metadata lacks a non-empty ``repository``.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L105"
    description: str = "galaxy.yml should have a repository key"
    enabled: bool = True
    name: str = "GalaxyRepository"
    version: str = "v0.0.1"
    severity: Severity = Severity.LOW
    tags: tuple[str, ...] = (Tag.QUALITY,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match COLLECTION nodes with parsed ``galaxy.yml`` metadata.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a COLLECTION with ``collection_metadata`` populated.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type != NodeType.COLLECTION:
            return False
        return bool(node.collection_metadata)

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report a violation when ``repository`` is missing or empty.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            ``GraphRuleResult`` with ``verdict`` True when ``repository`` is absent
            or empty, ``verdict`` False when set, or None if the node is not applicable.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type != NodeType.COLLECTION:
            return None
        metadata = node.collection_metadata
        if not metadata:
            return None
        meta = metadata if isinstance(metadata, dict) else {}
        if _repository_nonempty(meta):
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        detail: YAMLDict = {
            "message": "galaxy.yml metadata is missing a non-empty repository key",
            "expected_key": "repository",
        }
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
