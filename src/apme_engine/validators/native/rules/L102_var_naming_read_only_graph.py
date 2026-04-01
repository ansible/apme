"""GraphRule L102: do not set read-only Ansible variables."""

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

READ_ONLY_VARS = frozenset(
    {
        "ansible_version",
        "ansible_play_hosts",
        "ansible_play_hosts_all",
        "ansible_play_batch",
        "ansible_play_name",
        "ansible_role_name",
        "ansible_collection_name",
        "ansible_run_tags",
        "ansible_skip_tags",
        "ansible_check_mode",
        "ansible_diff_mode",
        "ansible_verbosity",
        "ansible_loop",
        "ansible_loop_var",
        "ansible_index_var",
        "ansible_parent_role_names",
        "ansible_parent_role_paths",
        "ansible_dependent_role_names",
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
    }
)


@dataclass
class VarNamingReadOnlyGraphRule(GraphRule):
    """Detect attempts to set read-only Ansible variables.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L102"
    description: str = "Do not set read-only Ansible variables"
    enabled: bool = True
    name: str = "VarNamingReadOnly"
    version: str = "v0.0.1"
    severity: str = Severity.HIGH
    tags: tuple[str, ...] = (Tag.VARIABLE,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task or handler nodes for read-only variable checks.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task or handler.
        """
        node = graph.get_node(node_id)
        return node is not None and node.node_type in _TASK_TYPES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report set_fact or register names that are read-only variables.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result with ``verdict`` True when violations exist, or
            None if the node is missing.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        violations: list[str] = []
        resolved = node.module
        mo = node.module_options
        if not isinstance(mo, dict):
            mo = {}

        if resolved in SET_FACT_MODULES:
            for key in mo:
                if key in ("cacheable",):
                    continue
                if key in READ_ONLY_VARS:
                    violations.append(key)

        reg = node.register
        if reg and str(reg) in READ_ONLY_VARS:
            violations.append(str(reg))

        if not violations:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        detail: YAMLDict = cast(YAMLDict, {"read_only_vars": violations})
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
