"""GraphRule L053: role meta should have valid structure."""

from dataclasses import dataclass
from typing import cast

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_GALAXY_STRING_FIELDS = frozenset(
    {
        "author",
        "company",
        "description",
        "license",
        "min_ansible_version",
        "min_ansible_container_version",
        "namespace",
        "role_name",
        "issue_tracker_url",
        "repository",
    }
)


def _check_galaxy_info_types(galaxy_info: dict[str, object]) -> list[str]:
    """Validate known ``galaxy_info`` scalar and sequence field types.

    Args:
        galaxy_info: The ``galaxy_info`` mapping from role metadata.

    Returns:
        Human-readable error strings for each type mismatch; empty if valid.
    """
    errors: list[str] = []
    for field in _GALAXY_STRING_FIELDS:
        if field not in galaxy_info:
            continue
        val = galaxy_info[field]
        if not isinstance(val, str):
            errors.append(
                f"galaxy_info.{field} must be a string, got {type(val).__name__}",
            )
    if "platforms" in galaxy_info and not isinstance(galaxy_info["platforms"], list):
        errors.append("galaxy_info.platforms must be a list")
    if "galaxy_tags" in galaxy_info and not isinstance(galaxy_info["galaxy_tags"], list):
        errors.append("galaxy_info.galaxy_tags must be a list")
    return errors


@dataclass
class MetaIncorrectGraphRule(GraphRule):
    """Flag ``role_metadata`` with invalid ``galaxy_info`` or ``dependencies`` shape.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
        precedence: Evaluation order (lower = earlier).
    """

    rule_id: str = "L053"
    description: str = "Role meta should have valid structure (galaxy_info, dependencies)"
    enabled: bool = True
    name: str = "MetaIncorrect"
    version: str = "v0.0.1"
    severity: str = Severity.LOW
    tags: tuple[str, ...] = (Tag.DEPENDENCY,)
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
        """Validate top-level meta keys and ``galaxy_info`` field types.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with structural or type errors in ``detail``, else pass.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        meta = node.role_metadata
        if not isinstance(meta, dict):
            return GraphRuleResult(
                verdict=True,
                detail={"message": "metadata must be a dict"},
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        galaxy_info = meta.get("galaxy_info")
        if galaxy_info is not None and not isinstance(galaxy_info, dict):
            return GraphRuleResult(
                verdict=True,
                detail={"message": "galaxy_info must be a dict when present"},
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        dependencies = meta.get("dependencies")
        if dependencies is not None and not isinstance(dependencies, list):
            return GraphRuleResult(
                verdict=True,
                detail={"message": "dependencies must be a list when present"},
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        if isinstance(galaxy_info, dict):
            type_errors = _check_galaxy_info_types(cast("dict[str, object]", galaxy_info))
            if type_errors:
                detail: YAMLDict = {"message": "; ".join(type_errors)}
                return GraphRuleResult(
                    verdict=True,
                    detail=detail,
                    node_id=node_id,
                    file=(node.file_path, node.line_start),
                )
        return GraphRuleResult(
            verdict=False,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
