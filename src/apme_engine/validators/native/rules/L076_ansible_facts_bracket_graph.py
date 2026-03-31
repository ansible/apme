"""GraphRule L076: detect injected fact variables instead of ansible_facts bracket notation.

Graph-aware port of ``L076_ansible_facts_bracket.py``.
"""

import re
from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

INJECTED_FACTS = frozenset(
    {
        "ansible_distribution",
        "ansible_distribution_major_version",
        "ansible_distribution_version",
        "ansible_distribution_release",
        "ansible_os_family",
        "ansible_architecture",
        "ansible_hostname",
        "ansible_fqdn",
        "ansible_default_ipv4",
        "ansible_default_ipv6",
        "ansible_all_ipv4_addresses",
        "ansible_all_ipv6_addresses",
        "ansible_memtotal_mb",
        "ansible_processor_vcpus",
        "ansible_kernel",
        "ansible_system",
        "ansible_pkg_mgr",
        "ansible_service_mgr",
        "ansible_python_interpreter",
        "ansible_user_id",
        "ansible_env",
        "ansible_interfaces",
        "ansible_mounts",
        "ansible_devices",
        "ansible_virtualization_type",
        "ansible_virtualization_role",
        "ansible_selinux",
        "ansible_apparmor",
        "ansible_date_time",
        "ansible_dns",
        "ansible_domain",
        "ansible_machine",
        "ansible_nodename",
        "ansible_processor",
        "ansible_swaptotal_mb",
        "ansible_uptime_seconds",
    }
)

_FACT_PATTERN = re.compile(r"\b(" + "|".join(re.escape(f) for f in sorted(INJECTED_FACTS)) + r")\b")


@dataclass
class AnsibleFactsBracketGraphRule(GraphRule):
    """Rule for using ansible_facts bracket notation instead of injected fact variables.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L076"
    description: str = "Use ansible_facts['key'] bracket notation instead of injected fact variables"
    enabled: bool = True
    name: str = "AnsibleFactsBracket"
    version: str = "v0.0.1"
    severity: str = Severity.VERY_LOW
    tags: tuple[str, ...] = (Tag.VARIABLE,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task or handler nodes.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task or handler.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        return node.node_type in _TASK_TYPES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Check for injected fact variable usage and return result.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with ``found_facts`` / ``message`` when violated, else pass.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        yaml_lines = getattr(node, "yaml_lines", "") or ""
        found = sorted(set(_FACT_PATTERN.findall(yaml_lines)))
        verdict = len(found) > 0
        detail: YAMLDict | None = None
        if found:
            detail = {
                "found_facts": found,
                "message": "use ansible_facts['key'] bracket notation instead",
            }
        return GraphRuleResult(
            verdict=verdict,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
