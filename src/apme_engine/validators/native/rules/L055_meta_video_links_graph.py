"""GraphRule L055: Role meta video_links should be valid URLs."""

import re
from dataclasses import dataclass
from typing import cast

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleScope, Severity, YAMLDict
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

URL_PATTERN = re.compile(r"^https?://\S+$")


@dataclass
class MetaVideoLinksGraphRule(GraphRule):
    """Validate ``galaxy_info.video_links`` entries are HTTP(S) URLs.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
        scope: Structural scope.
        precedence: Evaluation order (lower = earlier).
    """

    rule_id: str = "L055"
    description: str = "Role meta video_links should be valid URLs"
    enabled: bool = True
    name: str = "MetaVideoLinks"
    version: str = "v0.0.1"
    severity: Severity = Severity.LOW
    tags: tuple[str, ...] = (Tag.DEPENDENCY,)
    scope: str = RuleScope.ROLE
    precedence: int = 10

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match ROLE nodes only.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True if the node is a ROLE.
        """
        node = graph.get_node(node_id)
        return node is not None and node.node_type == NodeType.ROLE

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Flag invalid or malformed ``video_links`` in role metadata.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with ``invalid_links`` or a type message when violated; else pass.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        galaxy_info = node.role_metadata.get("galaxy_info")
        if not isinstance(galaxy_info, dict):
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        video_links = galaxy_info.get("video_links")
        if video_links is None or video_links == []:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        if not isinstance(video_links, list):
            return GraphRuleResult(
                verdict=True,
                detail=cast(
                    YAMLDict,
                    {
                        "message": "galaxy_info.video_links must be a list of URL strings",
                    },
                ),
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        invalid: list[str] = []
        for item in video_links:
            if not isinstance(item, str):
                invalid.append(str(item))
                continue
            if not URL_PATTERN.match(item):
                invalid.append(item)

        verdict = bool(invalid)
        if verdict:
            return GraphRuleResult(
                verdict=True,
                detail=cast(
                    YAMLDict,
                    {"invalid_links": [str(u) for u in invalid[:10]]},
                ),
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        return GraphRuleResult(
            verdict=False,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
