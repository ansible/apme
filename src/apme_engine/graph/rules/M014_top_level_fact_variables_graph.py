r"""GraphRule M014: top-level fact variables — use ansible_facts[\"name\"] (removed in 2.24).

Only flags the specific ``ansible_*`` variables that are injected as
top-level facts by the ``setup`` module (and related fact-gathering).
User-defined variables that happen to start with ``ansible_`` (e.g.
``ansible_controller_collection_installed``) are **not** deprecated
facts and are not flagged.
"""

import re
from dataclasses import dataclass
from typing import cast

from apme_engine.graph.content_graph import ContentGraph, NodeType
from apme_engine.graph.rule_base import GraphRule, GraphRuleResult
from apme_engine.graph.types import RuleTag as Tag
from apme_engine.graph.types import Severity, YAMLDict

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

# Known deprecated top-level fact variables injected by the setup module.
# These are the variables that Ansible injects as ``ansible_<name>`` when
# ``INJECT_FACTS_AS_VARS`` is true (deprecated since 2.5, removal in 2.24).
# User-defined ``ansible_*`` variables are NOT in this set.
DEPRECATED_FACTS: frozenset[str] = frozenset(
    {
        # System / OS identity
        "ansible_architecture",
        "ansible_bios_date",
        "ansible_bios_version",
        "ansible_distribution",
        "ansible_distribution_file_variety",
        "ansible_distribution_major_version",
        "ansible_distribution_release",
        "ansible_distribution_version",
        "ansible_hostname",
        "ansible_fqdn",
        "ansible_domain",
        "ansible_machine",
        "ansible_nodename",
        "ansible_os_family",
        "ansible_system",
        "ansible_system_vendor",
        "ansible_kernel",
        "ansible_kernel_version",
        "ansible_product_name",
        "ansible_product_serial",
        "ansible_product_uuid",
        "ansible_product_version",
        # Package / service managers
        "ansible_pkg_mgr",
        "ansible_service_mgr",
        # Python
        "ansible_python",
        "ansible_python_interpreter",
        "ansible_python_version",
        # Network
        "ansible_default_ipv4",
        "ansible_default_ipv6",
        "ansible_all_ipv4_addresses",
        "ansible_all_ipv6_addresses",
        "ansible_interfaces",
        "ansible_dns",
        # Hardware / memory / CPU
        "ansible_memfree_mb",
        "ansible_memtotal_mb",
        "ansible_memory_mb",
        "ansible_swapfree_mb",
        "ansible_swaptotal_mb",
        "ansible_processor",
        "ansible_processor_cores",
        "ansible_processor_count",
        "ansible_processor_nproc",
        "ansible_processor_threads_per_core",
        "ansible_processor_vcpus",
        # Mounts / devices
        "ansible_devices",
        "ansible_mounts",
        # User / env
        "ansible_env",
        "ansible_user_dir",
        "ansible_user_gecos",
        "ansible_user_gid",
        "ansible_user_id",
        "ansible_user_shell",
        "ansible_user_uid",
        "ansible_effective_group_id",
        "ansible_effective_user_id",
        "ansible_real_group_id",
        "ansible_real_user_id",
        # Date / time
        "ansible_date_time",
        # Virtualization / container
        "ansible_virtualization_role",
        "ansible_virtualization_type",
        # SELinux / AppArmor
        "ansible_selinux",
        "ansible_apparmor",
        # SSH / connection
        "ansible_ssh_host_key_dsa_public",
        "ansible_ssh_host_key_ecdsa_public",
        "ansible_ssh_host_key_ed25519_public",
        "ansible_ssh_host_key_rsa_public",
        # Misc gathered facts
        "ansible_cmdline",
        "ansible_fibre_channel_wwn",
        "ansible_lvm",
        "ansible_uptime_seconds",
    }
)

_ANSIBLE_VAR = re.compile(r"\b(ansible_\w+)\b")


@dataclass
class TopLevelFactVariablesGraphRule(GraphRule):
    """Detect deprecated injected ``ansible_*`` setup-module facts.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "M014"
    description: str = 'Use ansible_facts["name"] instead of injected ansible_* fact variables (removed in 2.24)'
    enabled: bool = True
    name: str = "TopLevelFactVariables"
    version: str = "v0.0.2"
    severity: Severity = Severity.HIGH
    tags: tuple[str, ...] = (Tag.CODING,)

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
        """Scan Jinja2 expressions for deprecated top-level fact variables.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with ``found_facts``, ``suggestions``, and ``message`` when violated;
            else pass.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        yaml_lines = getattr(node, "yaml_lines", "") or ""
        options = getattr(node, "options", None) or {}
        module_options = getattr(node, "module_options", None) or {}
        all_text_parts = [yaml_lines]
        for v in list(options.values()) + list(module_options.values()):
            if isinstance(v, str):
                all_text_parts.append(v)
        text = " ".join(all_text_parts)
        found: set[str] = set()
        for m in _ANSIBLE_VAR.finditer(text):
            varname = m.group(1)
            if varname in DEPRECATED_FACTS:
                found.add(varname)
        verdict = len(found) > 0
        detail: YAMLDict | None = None
        if found:
            suggestions = {v: f'ansible_facts["{v.removeprefix("ansible_")}"]' for v in sorted(found)}
            detail = cast(
                YAMLDict,
                {
                    "message": f"Top-level fact variable(s) {', '.join(sorted(found))} removed in 2.24",
                    "found_facts": sorted(found),
                    "suggestions": suggestions,
                },
            )
        return GraphRuleResult(
            verdict=verdict,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
