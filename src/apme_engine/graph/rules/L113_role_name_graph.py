"""GraphRule L113: enforce ansible-lint's role-name identifier convention."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from apme_engine.graph.content_graph import ContentGraph, ContentNode, NodeScope, NodeType
from apme_engine.graph.rule_base import GraphRule, GraphRuleResult
from apme_engine.graph.types import RuleScope, Severity, YAMLDict
from apme_engine.graph.types import RuleTag as Tag

_ROLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _normalize_role_name(role_name: str) -> str:
    """Apply ansible-lint's role-name prefix normalization.

    Args:
        role_name: Role name from metadata or the role path.

    Returns:
        Role name after removing an optional ``ansible-role-`` prefix.
    """
    return role_name.removeprefix("ansible-role-")


def _role_name(node: ContentNode) -> str:
    """Return the role name ansible-lint checks for a local role node.

    Args:
        node: Role node from the content graph.

    Returns:
        Normalized role name derived from metadata, FQCN, or path.
    """
    role_metadata = getattr(node, "role_metadata", {})
    galaxy_info = role_metadata.get("galaxy_info") if isinstance(role_metadata, dict) else None
    metadata_name = galaxy_info.get("role_name") if isinstance(galaxy_info, dict) else None
    if isinstance(metadata_name, str) and metadata_name.strip():
        return _normalize_role_name(metadata_name.strip())

    role_fqcn = node.role_fqcn.strip() or (node.name or "").strip()
    path = node.file_path or node.identity.path
    if not node.file_path and role_fqcn and path == f"roles/{role_fqcn}" and "." in role_fqcn:
        return _normalize_role_name(role_fqcn.rsplit(".", 1)[-1])
    role_path = PurePosixPath(path.replace("\\", "/").rstrip("/"))
    # ARI may identify a role by its meta/main.yml file rather than its root.
    if role_path.name in {"main.yml", "main.yaml"} and role_path.parent.name in {
        "defaults",
        "handlers",
        "meta",
        "tasks",
        "vars",
    }:
        return _normalize_role_name(role_path.parent.parent.name)
    if role_path.name:
        return _normalize_role_name(role_path.name)

    return _normalize_role_name(role_fqcn.rsplit(".", 1)[-1])


@dataclass
class RoleNameGraphRule(GraphRule):
    """Flag role names outside ``^[a-z][a-z0-9_]*$``.

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

    rule_id: str = "L113"
    description: str = "Role names should use lowercase letters, digits, and underscores"
    enabled: bool = True
    name: str = "RoleName"
    version: str = "v0.0.1"
    severity: Severity = Severity.LOW
    tags: tuple[str, ...] = (Tag.QUALITY,)
    scope: str = RuleScope.ROLE

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match owned role nodes.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is an owned role.
        """
        node = graph.get_node(node_id)
        return bool(node and node.node_type == NodeType.ROLE and node.scope == NodeScope.OWNED)

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report role names that do not follow the required convention.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            A violation result for an invalid role name, a clean result for a
            valid role name, or None if the node is not applicable.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type != NodeType.ROLE or node.scope != NodeScope.OWNED:
            return None
        role_name = _role_name(node)
        invalid = not bool(_ROLE_NAME_RE.fullmatch(role_name))
        detail: YAMLDict = {}
        if invalid:
            detail = {
                "role_name": role_name,
                "message": f"role name '{role_name}' must match ^[a-z][a-z0-9_]*$",
            }
        return GraphRuleResult(
            verdict=invalid,
            detail=detail or None,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
