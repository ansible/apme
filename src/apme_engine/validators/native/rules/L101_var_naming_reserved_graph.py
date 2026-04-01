"""GraphRule L101: variable names must not collide with Ansible reserved names."""

from dataclasses import dataclass
from typing import cast

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

SET_FACT_MODULES = frozenset(
    {
        "ansible.builtin.set_fact",
        "ansible.legacy.set_fact",
        "set_fact",
    }
)

RESERVED_NAMES = frozenset(
    {
        "ansible_facts",
        "ansible_host",
        "ansible_port",
        "ansible_user",
        "ansible_connection",
        "ansible_ssh_private_key_file",
        "ansible_become",
        "ansible_become_user",
        "ansible_become_method",
        "ansible_become_pass",
        "ansible_python_interpreter",
        "ansible_check_mode",
        "ansible_diff_mode",
        "ansible_verbosity",
        "ansible_version",
        "ansible_play_hosts",
        "ansible_play_batch",
        "ansible_play_name",
        "ansible_role_name",
        "ansible_collection_name",
        "ansible_loop",
        "ansible_loop_var",
        "ansible_index_var",
        "ansible_parent_role_names",
        "ansible_parent_role_paths",
        "ansible_dependent_role_names",
        "ansible_run_tags",
        "ansible_skip_tags",
        "ansible_search_path",
        "inventory_hostname",
        "inventory_hostname_short",
        "inventory_file",
        "inventory_dir",
        "groups",
        "group_names",
        "hostvars",
        "playbook_dir",
        "role_path",
        "role_name",
        "ansible_config_file",
        "ansible_playbook_python",
    }
)


@dataclass
class VarNamingReservedGraphRule(GraphRule):
    """Detect variable names that collide with Ansible reserved names.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L101"
    description: str = "Variable names must not collide with Ansible reserved names"
    enabled: bool = True
    name: str = "VarNamingReserved"
    version: str = "v0.0.1"
    severity: str = Severity.HIGH
    tags: tuple[str, ...] = (Tag.VARIABLE,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task or handler nodes for reserved-name variable checks.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task or handler.
        """
        node = graph.get_node(node_id)
        return node is not None and node.node_type in _TASK_TYPES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report reserved-name collisions in set_fact keys or register.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result with ``verdict`` True when collisions exist, or
            None if the node is missing.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        reserved_vars: list[str] = []
        resolved = node.module
        mo = node.module_options
        if not isinstance(mo, dict):
            mo = {}

        if resolved in SET_FACT_MODULES:
            for key in mo:
                if key in ("cacheable",):
                    continue
                if key in RESERVED_NAMES:
                    reserved_vars.append(key)

        reg = node.register
        if reg and str(reg) in RESERVED_NAMES:
            reserved_vars.append(str(reg))

        if not reserved_vars:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        detail: YAMLDict = cast(YAMLDict, {"reserved_vars": reserved_vars})
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
