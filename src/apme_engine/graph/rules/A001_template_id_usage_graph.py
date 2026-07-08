"""GraphRule A001: detect hardcoded AAP template IDs instead of named_url.

AAP 2.5+ supports named_url for job templates and workflow templates,
providing portability across environments. Hardcoded numeric IDs are
environment-specific and break when content is promoted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from apme_engine.graph.content_graph import ContentGraph, NodeType
from apme_engine.graph.rule_base import (
    GraphRule,
    GraphRuleResult,
    is_templated,
)
from apme_engine.graph.types import RuleTag as Tag
from apme_engine.graph.types import Severity, YAMLDict

if TYPE_CHECKING:
    from apme_engine.graph.content_graph import ContentNode

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

_AAP_API_MODULES = frozenset(
    {
        "uri",
        "ansible.builtin.uri",
    }
)

_AAP_CONTROLLER_MODULES = frozenset(
    {
        "job_launch",
        "workflow_launch",
        "job_template",
        "workflow_job_template",
        "awx.awx.job_launch",
        "awx.awx.workflow_launch",
        "ansible.controller.job_launch",
        "ansible.controller.workflow_launch",
        "awx.awx.job_template",
        "awx.awx.workflow_job_template",
        "ansible.controller.job_template",
        "ansible.controller.workflow_job_template",
    }
)

_TEMPLATE_ID_PATTERN = re.compile(
    r"/api/(v2|controller/v2)/(job_templates|workflow_job_templates)/(\d+)(/|$|[?#])",
    re.IGNORECASE,
)

_TEMPLATE_ID_ONLY_PATTERN = re.compile(r"^\d+$")


def _extract_url_from_options(options: YAMLDict) -> str | None:
    """Extract URL from uri module options.

    Args:
        options: Module options dict from the task.

    Returns:
        URL string if present and is a string, None otherwise.
    """
    url = options.get("url")
    if isinstance(url, str):
        return url
    return None


def _check_controller_module_id(node: ContentNode) -> tuple[bool, str | None, str | None]:
    """Check if controller module uses numeric ID instead of name.

    Args:
        node: ContentNode for the task using a controller module.

    Returns:
        Tuple of (has_violation, template_type, id_value).
    """
    options = node.module_options or {}

    job_template_id = options.get("job_template_id")
    if job_template_id is not None:
        val = str(job_template_id)
        if _TEMPLATE_ID_ONLY_PATTERN.match(val) and not is_templated(val):
            return (True, "job_template", val)

    workflow_template_id = options.get("workflow_template_id")
    if workflow_template_id is not None:
        val = str(workflow_template_id)
        if _TEMPLATE_ID_ONLY_PATTERN.match(val) and not is_templated(val):
            return (True, "workflow_template", val)

    job_template = options.get("job_template")
    if job_template is not None:
        val = str(job_template)
        if _TEMPLATE_ID_ONLY_PATTERN.match(val) and not is_templated(val):
            return (True, "job_template", val)

    workflow = options.get("workflow")
    if workflow is not None:
        val = str(workflow)
        if _TEMPLATE_ID_ONLY_PATTERN.match(val) and not is_templated(val):
            return (True, "workflow_template", val)

    return (False, None, None)


def _check_uri_module(node: ContentNode) -> tuple[bool, str | None, str | None]:
    """Check if uri module URL contains hardcoded template ID.

    Args:
        node: ContentNode for the task using the uri module.

    Returns:
        Tuple of (has_violation, template_type, id_value).
    """
    options = node.module_options or {}
    url = _extract_url_from_options(options)

    if not url:
        return (False, None, None)

    match = _TEMPLATE_ID_PATTERN.search(url)
    if match:
        resource = match.group(2)
        template_type = "workflow_template" if resource == "workflow_job_templates" else "job_template"
        id_value = match.group(3)
        return (True, template_type, id_value)

    return (False, None, None)


@dataclass
class TemplateIDUsageGraphRule(GraphRule):
    """Flag tasks using hardcoded AAP template IDs instead of named_url.

    AAP 2.5+ supports named_url references for job templates and workflow
    templates. Using numeric IDs is fragile because IDs differ across
    environments (dev, staging, production).

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "A001"
    description: str = "Task uses hardcoded AAP template ID instead of named_url"
    enabled: bool = True
    name: str = "TemplateIDUsage"
    version: str = "v0.0.1"
    severity: Severity = Severity.MEDIUM
    tags: tuple[str, ...] = (Tag.AAP, Tag.PORTABILITY)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task/handler nodes using AAP API or controller modules.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task/handler using uri or controller modules.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        if node.node_type not in _TASK_TYPES:
            return False

        mod = node.module or ""
        return mod in _AAP_API_MODULES or mod in _AAP_CONTROLLER_MODULES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Check for hardcoded template IDs in AAP API calls.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with violation details when a hardcoded ID is found.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        mod = node.module or ""

        has_violation = False
        template_type: str | None = None
        id_value: str | None = None

        if mod in _AAP_CONTROLLER_MODULES:
            has_violation, template_type, id_value = _check_controller_module_id(node)
        elif mod in _AAP_API_MODULES:
            has_violation, template_type, id_value = _check_uri_module(node)

        if not has_violation:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        detail: YAMLDict = {
            "module": mod,
            "template_type": template_type,
            "hardcoded_id": id_value,
            "recommendation": "Use named_url (e.g., 'My Template++Default') instead of ID",
        }

        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
