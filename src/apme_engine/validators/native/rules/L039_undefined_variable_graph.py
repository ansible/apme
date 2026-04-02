"""GraphRule L039: variable references that may be undefined.

Extracts Jinja2 variable references from a task's content fields and
compares them against all variables resolvable in the node's scope via
``VariableProvenanceResolver``.  References that are not defined in any
reachable scope and are not Ansible magic/special variables are reported
as potentially undefined.

Severity is LOW because false positives are expected for extra vars,
dynamic inventory facts, and other runtime-only sources.
"""

import re
from dataclasses import dataclass
from typing import cast

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.engine.variable_provenance import VariableProvenanceResolver
from apme_engine.validators.native.rules.graph_rule_base import (
    GraphRule,
    GraphRuleResult,
)

_JINJA_VAR_RE = re.compile(r"\{\{(.*?)\}\}")
_BARE_IDENT_RE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b")

# Jinja operators/tests/functions that look like identifiers but are not
# variable references.
_JINJA_BUILTINS: frozenset[str] = frozenset(
    {
        "and",
        "or",
        "not",
        "in",
        "is",
        "if",
        "else",
        "elif",
        "for",
        "import",
        "as",
        "with",
        "bool",
        "int",
        "float",
        "string",
        "list",
        "dict",
        "length",
        "lower",
        "upper",
        "default",
        "defined",
        "undefined",
        "sameas",
        "mapping",
        "iterable",
        "sequence",
        "number",
        "match",
        "search",
        "regex",
        "select",
        "reject",
        "map",
        "sort",
        "join",
        "first",
        "last",
        "min",
        "max",
        "abs",
        "round",
        "trim",
        "replace",
        "split",
        "unique",
        "flatten",
        "combine",
        "mandatory",
        "ternary",
        "from_json",
        "from_yaml",
        "to_json",
        "to_yaml",
        "to_nice_json",
        "to_nice_yaml",
        "b64encode",
        "b64decode",
        "hash",
        "type_debug",
        "ipaddr",
        "basename",
        "dirname",
        "realpath",
        "relpath",
        "regex_replace",
        "regex_search",
        "regex_findall",
        "password_hash",
        "comment",
        "subelements",
        "product",
        "zip",
        "zip_longest",
        "json_query",
        "community",
        "succeeded",
        "failed",
        "changed",
        "skipped",
        "success",
        "failure",
    }
)

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

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


def _extract_jinja_refs(texts: list[str]) -> set[str]:
    """Extract simple variable identifiers from Jinja expressions.

    Only extracts the root identifier (before ``.`` or ``|``).  Dotted
    access and filters are stripped.

    Args:
        texts: Strings that may contain ``{{ ... }}`` expressions.

    Returns:
        Set of root variable names referenced in the Jinja expressions.
    """
    refs: set[str] = set()
    for text in texts:
        for match in _JINJA_VAR_RE.findall(text):
            cleaned = match.strip().split("|")[0].split(".")[0].split("[")[0].strip()
            if cleaned and cleaned.isidentifier():
                refs.add(cleaned)
    return refs


_QUOTED_STRING_RE = re.compile(r"""(?:'[^']*'|"[^"]*")""")
_DOTTED_ATTR_RE = re.compile(r"\.([a-zA-Z_][a-zA-Z0-9_]*)")


def _extract_bare_refs(texts: list[str]) -> set[str]:
    """Extract identifiers from bare Jinja expressions (no ``{{ }}``).

    Used for ``when``, ``changed_when``, ``failed_when`` which are
    implicitly Jinja — Ansible evaluates them as expressions without
    requiring ``{{ }}`` wrappers.

    Strips quoted strings and dotted attribute names before extraction
    so that ``'RedHat'`` and ``.rc`` are not treated as variables.

    Args:
        texts: Bare expression strings.

    Returns:
        Set of identifier names minus Jinja builtins/operators.
    """
    refs: set[str] = set()
    for text in texts:
        stripped = _QUOTED_STRING_RE.sub("", text)
        dotted_attrs = {m.group(1) for m in _DOTTED_ATTR_RE.finditer(stripped)}
        for ident in _BARE_IDENT_RE.findall(stripped):
            if ident not in _JINJA_BUILTINS and ident not in dotted_attrs and not ident[0].isdigit():
                refs.add(ident)
    return refs


def _collect_strings(node: object) -> tuple[list[str], list[str]]:
    """Gather string fields from a node, split by expression type.

    Args:
        node: A ContentNode (duck-typed to avoid circular import in tests).

    Returns:
        Tuple of (template_strings, bare_expression_strings).
        Template strings may contain ``{{ }}``; bare expression strings
        are implicitly Jinja (``when``, ``changed_when``, ``failed_when``).
    """
    templates: list[str] = []
    bare: list[str] = []

    when_expr = getattr(node, "when_expr", None)
    if when_expr:
        if isinstance(when_expr, list):
            bare.extend(str(w) for w in when_expr)
        else:
            bare.append(str(when_expr))

    name = getattr(node, "name", None)
    if isinstance(name, str):
        templates.append(name)

    mo = getattr(node, "module_options", None)
    if isinstance(mo, dict):
        _collect_dict_strings(mo, templates)

    for attr in ("changed_when", "failed_when"):
        val = getattr(node, attr, None)
        if isinstance(val, str):
            bare.append(val)
        elif isinstance(val, list):
            bare.extend(str(v) for v in val)

    env = getattr(node, "environment", None)
    if isinstance(env, dict):
        _collect_dict_strings(env, templates)

    return templates, bare


def _collect_dict_strings(d: dict[str, object], out: list[str]) -> None:
    """Recursively collect string values from a nested dict.

    Args:
        d: Dictionary to traverse.
        out: Accumulator list for discovered strings.
    """
    for v in d.values():
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            _collect_dict_strings(v, out)
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, str):
                    out.append(item)
                elif isinstance(item, dict):
                    _collect_dict_strings(item, out)


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
    severity: Severity = Severity.LOW
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
        return node is not None and node.node_type in _TASK_TYPES

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

        templates, bare = _collect_strings(node)
        refs = _extract_jinja_refs(templates) | _extract_bare_refs(bare)
        if not refs:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        # Any ansible_* prefix is treated as a potential fact / connection var
        non_magic = {r for r in refs if r not in _MAGIC_VARS and not r.startswith("ansible_")}
        if not non_magic:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        resolver = VariableProvenanceResolver(graph)
        defined = resolver.resolve_variables(node_id)

        undefined = sorted(non_magic - set(defined))
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
