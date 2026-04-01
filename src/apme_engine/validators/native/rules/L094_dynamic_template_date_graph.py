"""GraphRule L094: detect dynamic date expressions in templates.

Graph-aware port of ``L094_dynamic_template_date.py``.
"""

import re
from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

_DYNAMIC_DATE = re.compile(
    r"ansible_date_time"
    r"|now\(\)"
    r"|strftime"
    r"|lookup\(['\"]pipe['\"],\s*['\"]date",
    re.IGNORECASE,
)

TEMPLATE_MODULES = frozenset(
    {
        "ansible.builtin.template",
        "ansible.legacy.template",
        "template",
    }
)


@dataclass
class DynamicTemplateDateGraphRule(GraphRule):
    """Rule for detecting dynamic date expressions in template content.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L094"
    description: str = "Do not put dynamic dates in templates; breaks change detection"
    enabled: bool = True
    name: str = "DynamicTemplateDate"
    version: str = "v0.0.1"
    severity: str = Severity.LOW
    tags: tuple[str, ...] = (Tag.CODING,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task or handler nodes using a template module.

        Uses the node's declared ``module`` name.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task or handler whose declared
            module name is a known template module.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        if node.node_type not in _TASK_TYPES:
            return False
        mod = node.module or ""
        return mod in TEMPLATE_MODULES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Check for dynamic date expressions in template tasks.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with ``found_patterns`` / ``message`` when violated; pass when
            not a template module or no dynamic date patterns.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        mod = node.module or ""
        if mod not in TEMPLATE_MODULES:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        yaml_lines = getattr(node, "yaml_lines", "") or ""
        module_options = getattr(node, "module_options", None) or {}
        content_to_check = yaml_lines + str(module_options)
        found = _DYNAMIC_DATE.findall(content_to_check)
        verdict = len(found) > 0
        detail: YAMLDict | None = None
        if found:
            detail = {
                "found_patterns": list(set(found)),
                "message": "do not put dynamic dates in templates; breaks idempotent change detection",
            }
        return GraphRuleResult(
            verdict=verdict,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
