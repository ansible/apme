"""GraphRule L039: variable references that may be undefined.

Extracts Jinja2 variable references from a task's content fields and
compares them against all variables resolvable in the node's scope via
``VariableProvenanceResolver``.  References that are not defined in any
reachable scope and are not Ansible magic/special variables are reported
as potentially undefined.

Severity is LOW because false positives are expected for extra vars,
dynamic inventory facts, and other runtime-only sources.
"""

from dataclasses import dataclass
from typing import cast

from apme_engine.graph.content_graph import ContentGraph
from apme_engine.graph.rule_base import GraphRule, GraphRuleResult
from apme_engine.graph.types import RuleTag as Tag
from apme_engine.graph.types import Severity, YAMLDict
from apme_engine.graph.variable_helpers import (
    TASK_TYPES,
    collect_timed_strings,
    extract_bare_refs,
    extract_jinja_refs,
)
from apme_engine.graph.variable_provenance import VariableProvenanceResolver

# Ansible magic/special variables that are always available at runtime
# and should never be flagged as undefined.
_MAGIC_VARS: frozenset[str] = frozenset(
    {
        # Host/inventory
        "inventory_hostname",
        "inventory_hostname_short",
        "inventory_dir",
        "inventory_file",
        "groups",
        "group_names",
        "hostvars",
        "ansible_host",
        "ansible_port",
        "ansible_user",
        "ansible_connection",
        "ansible_ssh_host",
        "ansible_ssh_port",
        "ansible_ssh_user",
        "ansible_ssh_pass",
        "ansible_ssh_private_key_file",
        "ansible_become",
        "ansible_become_user",
        "ansible_become_pass",
        "ansible_become_method",
        # Play context
        "play_hosts",
        "ansible_play_hosts",
        "ansible_play_hosts_all",
        "ansible_play_batch",
        "ansible_play_name",
        "ansible_play_role_names",
        "playbook_dir",
        "role_path",
        "role_name",
        "role_names",
        # Facts / setup
        "ansible_facts",
        "ansible_local",
        "ansible_env",
        "ansible_os_family",
        "ansible_distribution",
        "ansible_distribution_version",
        "ansible_distribution_major_version",
        "ansible_architecture",
        "ansible_pkg_mgr",
        "ansible_service_mgr",
        "ansible_hostname",
        "ansible_fqdn",
        "ansible_default_ipv4",
        "ansible_all_ipv4_addresses",
        "ansible_interfaces",
        "ansible_memtotal_mb",
        "ansible_processor_vcpus",
        "ansible_python_interpreter",
        "ansible_python",
        # Runtime / check mode
        "ansible_check_mode",
        "ansible_diff_mode",
        "ansible_verbosity",
        "ansible_version",
        "ansible_run_tags",
        "ansible_skip_tags",
        "ansible_config_file",
        # Loop variables
        "item",
        "ansible_loop",
        "ansible_loop_var",
        "ansible_index_var",
        # Ansible built-in variable namespace
        "vars",
        # Special Jinja / Ansible constants
        "omit",
        "undefined",
        "true",
        "false",
        "none",
        "True",
        "False",
        "None",
        # Common builtins / test functions in Jinja context
        "lookup",
        "query",
        "q",
        "now",
        "range",
        "undef",
    }
)


@dataclass
class UndefinedVariableGraphRule(GraphRule):
    """Detect variable references that may be undefined in the current scope.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L039"
    description: str = "Variable use may be undefined"
    enabled: bool = True
    name: str = "UndefinedVariable"
    version: str = "v0.0.1"
    severity: Severity = Severity.MEDIUM
    tags: tuple[str, ...] = (Tag.VARIABLE,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task and handler nodes where Jinja expressions appear.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True for task and handler nodes.
        """
        node = graph.get_node(node_id)
        return node is not None and node.node_type in TASK_TYPES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report Jinja variable references with no visible definition in scope.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result with ``verdict`` True when undefined refs exist.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        pre_templates, pre_bare, post_bare = collect_timed_strings(
            node,
            include_loop=False,
            include_task_variables=False,
        )
        pre_refs = extract_jinja_refs(pre_templates) | extract_bare_refs(pre_bare)
        post_refs = extract_bare_refs(post_bare)
        if not pre_refs and not post_refs:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        pre_non_magic = {r for r in pre_refs if r not in _MAGIC_VARS and not r.startswith("ansible_")}
        post_non_magic = {r for r in post_refs if r not in _MAGIC_VARS and not r.startswith("ansible_")}
        if not pre_non_magic and not post_non_magic:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        resolver = VariableProvenanceResolver(graph)
        defined = set(resolver.resolve_variables(node_id))

        # Register is available only in changed_when/failed_when (post-task).
        post_defined = defined
        if node.register:
            post_defined = defined | {node.register}

        undefined = sorted((pre_non_magic - defined) | (post_non_magic - post_defined))
        if not undefined:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        detail: YAMLDict = cast(
            YAMLDict,
            {
                "message": (f"Possibly undefined variable(s): {', '.join(undefined)}"),
                "undefined_vars": undefined,
            },
        )
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
