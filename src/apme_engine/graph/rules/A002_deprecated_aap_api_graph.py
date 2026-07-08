"""GraphRule A002: detect deprecated AAP API endpoints.

AAP 2.5 introduced the platform gateway with new API paths:
- `/api/controller/v2/` for automation controller
- `/api/hub/v2/` for automation hub (replaces deprecated /api/v2/ on hub)
- `/api/eda/v2/` for Event-Driven Ansible

The legacy `/api/v2/` endpoints are deprecated and will be removed in 2.7.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from apme_engine.graph.content_graph import ContentGraph, NodeType
from apme_engine.graph.rule_base import (
    GraphRule,
    GraphRuleResult,
)
from apme_engine.graph.types import RuleTag as Tag
from apme_engine.graph.types import Severity, YAMLDict

if TYPE_CHECKING:
    from apme_engine.graph.content_graph import ContentNode

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

_URI_MODULES = frozenset(
    {
        "uri",
        "ansible.builtin.uri",
    }
)

_DEPRECATED_HUB_MODULES = frozenset(
    {
        "ah_token",
        "ah_user",
        "ansible.hub.ah_token",
        "ansible.hub.ah_user",
    }
)

_DEPRECATED_API_PATTERN = re.compile(
    r"(?:https?://[^/]+)?/api/v2/(?!controller/|hub/|eda/)",
    re.IGNORECASE,
)

_RESOURCE_TERMINATOR_PATTERN = re.compile(r"[/?#]")

_CONTROLLER_API_RESOURCES = frozenset(
    {
        "job_templates",
        "workflow_job_templates",
        "jobs",
        "workflow_jobs",
        "inventories",
        "inventory_sources",
        "hosts",
        "groups",
        "credentials",
        "credential_types",
        "projects",
        "organizations",
        "teams",
        "users",
        "schedules",
        "notification_templates",
        "labels",
        "execution_environments",
        "instance_groups",
        "instances",
    }
)

_HUB_API_RESOURCES = frozenset(
    {
        "collections",
        "namespaces",
        "repositories",
        "container-images",
        "execution-environments",
        "remotes",
        "distributions",
    }
)


def _extract_urls_from_options(options: YAMLDict) -> list[str]:
    """Extract URL values from module options.

    Args:
        options: Module options dict from the task.

    Returns:
        List of URL strings found in the options.
    """
    urls: list[str] = []
    url = options.get("url")
    if isinstance(url, str):
        urls.append(url)
    return urls


def _classify_deprecated_api(url: str) -> tuple[str | None, str | None]:
    """Classify a deprecated API URL by service type.

    Args:
        url: URL string to analyze.

    Returns:
        Tuple of (service_type, recommended_path) or (None, None) if not deprecated.
    """
    match = _DEPRECATED_API_PATTERN.search(url)
    if not match:
        return (None, None)

    resource = _RESOURCE_TERMINATOR_PATTERN.split(url[match.end() :], maxsplit=1)[0].lower()

    if resource in _CONTROLLER_API_RESOURCES:
        return ("controller", f"/api/controller/v2/{resource}")

    if resource in _HUB_API_RESOURCES:
        return ("hub", f"/api/hub/v2/{resource}")

    # Only flag URLs that explicitly reference known AAP resources.
    # Unknown /api/v2/ paths may belong to non-AAP services.
    return (None, None)


def _check_uri_module(node: ContentNode) -> tuple[bool, str | None, str | None, str | None]:
    """Check if uri module URL uses deprecated API endpoints.

    Args:
        node: ContentNode for the task using the uri module.

    Returns:
        Tuple of (has_violation, service_type, deprecated_path, recommended_path).
    """
    options = node.module_options or {}
    urls = _extract_urls_from_options(options)

    for url in urls:
        service_type, recommended = _classify_deprecated_api(url)
        if service_type:
            deprecated_path = "/api/v2/"
            return (True, service_type, deprecated_path, recommended)

    return (False, None, None, None)


def _check_deprecated_module(node: ContentNode) -> tuple[bool, str | None]:
    """Check if task uses deprecated hub modules.

    Args:
        node: ContentNode for the task.

    Returns:
        Tuple of (has_violation, replacement_module).
    """
    mod = node.module or ""

    if mod in {"ansible.hub.ah_token", "ah_token"}:
        return (True, "ansible.platform.token")

    if mod in {"ansible.hub.ah_user", "ah_user"}:
        return (True, "ansible.platform.user")

    return (False, None)


@dataclass
class DeprecatedAAPAPIGraphRule(GraphRule):
    """Flag tasks using deprecated AAP API endpoints or modules.

    AAP 2.5 introduced the platform gateway with new API paths. The legacy
    /api/v2/ endpoints are deprecated and will be removed in AAP 2.7.

    Additionally, certain ansible.hub modules are deprecated in favor of
    the ansible.platform collection.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "A002"
    description: str = "Task uses deprecated AAP API endpoint or module"
    enabled: bool = True
    name: str = "DeprecatedAAPAPI"
    version: str = "v0.0.1"
    severity: Severity = Severity.HIGH
    tags: tuple[str, ...] = (Tag.AAP, Tag.PORTABILITY)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task/handler nodes using uri or deprecated hub modules.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node might use deprecated AAP APIs.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        if node.node_type not in _TASK_TYPES:
            return False

        mod = node.module or ""
        return mod in _URI_MODULES or mod in _DEPRECATED_HUB_MODULES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Check for deprecated AAP API usage.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with violation details when deprecated API is found.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        mod = node.module or ""

        if mod in _DEPRECATED_HUB_MODULES:
            has_violation, replacement = _check_deprecated_module(node)
            if has_violation:
                detail: YAMLDict = {
                    "module": mod,
                    "deprecated_in": "AAP 2.5",
                    "removed_in": "AAP 2.7",
                    "replacement": replacement,
                }
                return GraphRuleResult(
                    verdict=True,
                    detail=detail,
                    node_id=node_id,
                    file=(node.file_path, node.line_start),
                )

        if mod in _URI_MODULES:
            has_violation, service_type, deprecated, recommended = _check_uri_module(node)
            if has_violation:
                detail = {
                    "module": mod,
                    "service": service_type,
                    "deprecated_path": deprecated,
                    "deprecated_in": "AAP 2.5",
                    "removed_in": "AAP 2.7",
                    "recommended_path": recommended,
                }
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
