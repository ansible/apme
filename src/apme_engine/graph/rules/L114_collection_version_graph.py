"""GraphRule L114: require collection versions at least 1.0.0."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from apme_engine.graph.content_graph import ContentGraph, NodeScope, NodeType
from apme_engine.graph.rule_base import GraphRule, GraphRuleResult
from apme_engine.graph.types import RuleScope, Severity, YAMLDict
from apme_engine.graph.types import RuleTag as Tag

_SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$"
)


def _collection_version(metadata: Mapping[str, object]) -> object:
    """Read version from galaxy.yml or a MANIFEST collection_info object.

    Args:
        metadata: Collection metadata mapping.

    Returns:
        The version value, or None when no version is present.
    """
    collection_info = metadata.get("collection_info")
    source = collection_info if isinstance(collection_info, Mapping) else metadata
    return source.get("version")


@dataclass
class CollectionVersionGraphRule(GraphRule):
    """Flag malformed collection versions and versions before 1.0.0.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule implementation version.
        severity: Severity level.
        tags: Rule tags.
        scope: Graph scope in which the rule runs.
    """

    rule_id: str = "L114"
    description: str = "Collection version must be valid semantic version 1.0.0 or newer"
    enabled: bool = True
    name: str = "CollectionVersion"
    version: str = "v0.0.1"
    severity: Severity = Severity.LOW
    tags: tuple[str, ...] = (Tag.QUALITY,)
    scope: str = RuleScope.COLLECTION

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match owned collection nodes.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is an owned collection.
        """
        node = graph.get_node(node_id)
        return bool(node and node.node_type == NodeType.COLLECTION and node.scope == NodeScope.OWNED)

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report malformed collection versions or versions below 1.0.0.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            A violation result for an invalid or pre-1.0.0 version, a clean
            result for a valid version, or None if the node is not applicable.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type != NodeType.COLLECTION or node.scope != NodeScope.OWNED:
            return None
        metadata = node.collection_metadata
        version_value = _collection_version(metadata)
        # L095 reports a missing required version key. This rule handles only
        # present values, so the same metadata error is not reported twice.
        if version_value is None or (isinstance(version_value, str) and not version_value.strip()):
            unavailable_detail: YAMLDict = {}
            if not metadata:
                unavailable_detail = {
                    "message": "collection version could not be read because collection metadata is unavailable"
                }
            return GraphRuleResult(
                verdict=bool(unavailable_detail),
                detail=unavailable_detail or None,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        version = str(version_value).strip()
        match = _SEMVER_RE.fullmatch(version)
        reason = ""
        if match is None:
            reason = "must use semantic versioning (MAJOR.MINOR.PATCH)"
        else:
            major, minor, patch = (match.group(index) for index in (1, 2, 3))
            prerelease = match.group(4)
            build = match.group(5)
            prerelease_ids = prerelease.split(".") if prerelease else []
            build_ids = build.split(".") if build else []
            invalid_identifiers = any(not identifier for identifier in prerelease_ids + build_ids)
            invalid_numeric_prerelease = any(
                identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0")
                for identifier in prerelease_ids
            )
            if invalid_identifiers or invalid_numeric_prerelease:
                reason = "must use valid semantic-version identifiers"
            elif major == "0" or (major == "1" and minor == "0" and patch == "0" and prerelease is not None):
                reason = "must be at least 1.0.0"

        detail: YAMLDict = {}
        if reason:
            detail = {
                "version": version,
                "message": f"collection version {version!r} {reason}",
            }
        return GraphRuleResult(
            verdict=bool(reason),
            detail=detail or None,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
