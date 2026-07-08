"""GraphRule L110: debug tasks should not log sensitive variables without no_log.

Detects debug module usage where msg or var contains references to sensitive
variables (password, secret, token, api_key, etc.) without no_log: true set
on the task or an ancestor scope.

Complements L047 (which checks password-like parameter *names*) by catching
sensitive variable *values* being logged via debug.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import cast

from apme_engine.graph.content_graph import ContentGraph, ContentNode, NodeType
from apme_engine.graph.rule_base import GraphRule, GraphRuleResult
from apme_engine.graph.types import RuleTag as Tag
from apme_engine.graph.types import Severity, YAMLDict, YAMLValue

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

_DEBUG_MODULES = frozenset(
    {
        "debug",
        "ansible.builtin.debug",
        "ansible.legacy.debug",
    }
)

_SENSITIVE_WORDS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "secrets",
        "token",
        "api_key",
        "apikey",
        "credential",
        "credentials",
        "cred",
        "private_key",
        "ssh_key",
        "access_key",
        "client_key",
    }
)

_JINJA_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)")
_JINJA_BLOCK_RE = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
_JINJA_ATTR_RE = re.compile(r"\[['\"]([a-zA-Z_][a-zA-Z0-9_]*)['\"]")
_WORD_BOUNDARY_RE = re.compile(r"(?:^|[_.'\"\[])({})(?:[_.'\"\[\]]|$)".format("|".join(_SENSITIVE_WORDS)))


def _extract_jinja_vars(text: str) -> set[str]:
    """Extract variable names from Jinja template references.

    Captures both simple vars ({{ password }}) and nested attribute/key access
    ({{ vault.db_password }}, {{ credentials['token'] }}). Only scans within
    Jinja blocks to avoid false positives from plain text containing brackets.

    Args:
        text: String potentially containing Jinja syntax like {{ var_name }}.

    Returns:
        Set of variable names/paths found in the text.
    """
    if not isinstance(text, str):
        return set()
    result: set[str] = set()
    for m in _JINJA_VAR_RE.finditer(text):
        result.add(m.group(1))
    for block_match in _JINJA_BLOCK_RE.finditer(text):
        block_content = block_match.group(1)
        for attr_match in _JINJA_ATTR_RE.finditer(block_content):
            result.add(attr_match.group(1))
    return result


def _var_looks_sensitive(var_name: str) -> bool:
    """Check if a variable name matches sensitive patterns.

    Uses word-boundary matching to avoid false positives like 'secretary_name'
    (contains 'secret') or 'tokenized_value' (contains 'token'). Matches when
    a sensitive word appears as a complete segment bounded by underscores,
    dots, brackets, or string start/end.

    Handles both simple names (db_password) and nested paths (vault.db_password,
    credentials['token']).

    Args:
        var_name: Variable name or dotted path to check.

    Returns:
        True if the variable name contains a sensitive word as a segment.
    """
    lower = var_name.lower()
    return bool(_WORD_BOUNDARY_RE.search(lower))


def _find_sensitive_vars_in_debug(node: ContentNode) -> list[str]:
    """Find sensitive variable references in debug task msg/var/_raw/_raw_params.

    Checks msg, var, _raw, and _raw_params (free-form module args) for sensitive
    variable references. Uses a set internally for deduplication.

    Args:
        node: Task node to inspect.

    Returns:
        List of unique sensitive variable names found.
    """
    sensitive_found: set[str] = set()
    mo = node.module_options if isinstance(node.module_options, dict) else {}

    msg = mo.get("msg", "")
    if msg:
        for var_name in _extract_jinja_vars(str(msg)):
            if _var_looks_sensitive(var_name):
                sensitive_found.add(var_name)

    var_param = mo.get("var", "")
    if var_param and isinstance(var_param, str) and _var_looks_sensitive(var_param):
        sensitive_found.add(var_param)

    # Check both _raw and _raw_params for free-form module args
    for raw_key in ("_raw", "_raw_params"):
        raw = mo.get(raw_key, "")
        if raw:
            for var_name in _extract_jinja_vars(str(raw)):
                if _var_looks_sensitive(var_name):
                    sensitive_found.add(var_name)

    return sorted(sensitive_found)


def _no_log_true_in_scope(graph: ContentGraph, node_id: str) -> bool:
    """Return True if no_log is effectively True at this node.

    Ansible allows more-specific scopes to override inherited keywords. A task
    with no_log: false can opt out of a block/play with no_log: true. We walk
    the chain from the task outward (closest to farthest) and return on the
    first explicit no_log setting. This correctly handles cases like:
    - Task: unset, Block: false, Play: true → effective false (block wins)
    - Task: unset, Block: true, Play: false → effective true (block wins)

    Args:
        graph: Content graph for the scan.
        node_id: Task or handler node id.

    Returns:
        True when no_log is effectively true at this scope.
    """
    node = graph.get_node(node_id)
    if node is None:
        return False
    if node.no_log is False:
        return False
    if node.no_log is True:
        return True
    for ancestor in graph.ancestors(node_id):
        if ancestor.no_log is False:
            return False
        if ancestor.no_log is True:
            return True
    return False


@dataclass
class DebugSensitiveVarsGraphRule(GraphRule):
    """Flag debug tasks logging sensitive variables without no_log protection.

    Detects patterns like:
        - debug: msg="{{ db_password }}"
        - debug: var=api_token

    Without no_log: true on the task or a containing block/play.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L110"
    description: str = "Debug tasks should not log sensitive variables without no_log: true"
    enabled: bool = True
    name: str = "DebugSensitiveVars"
    version: str = "v0.0.1"
    severity: Severity = Severity.HIGH
    tags: tuple[str, ...] = (Tag.SYSTEM, Tag.SECURITY)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match debug tasks with msg, var, or free-form parameters.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a debug task with msg, var, _raw, or _raw_params set.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        if node.node_type not in _TASK_TYPES:
            return False
        if node.module not in _DEBUG_MODULES:
            return False

        mo = node.module_options if isinstance(node.module_options, dict) else {}
        return bool(mo.get("msg") or mo.get("var") or mo.get("_raw") or mo.get("_raw_params"))

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Check if debug task logs sensitive variables without no_log.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult; verdict True when violation is found.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        sensitive_vars = _find_sensitive_vars_in_debug(node)
        if not sensitive_vars:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        if _no_log_true_in_scope(graph, node_id):
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        vars_jinja = ", ".join(f"{{{{ {v} }}}}" for v in sorted(sensitive_vars))
        detail: YAMLDict = {
            "message": f"Debug task logs sensitive variable(s): {vars_jinja}; set no_log: true",
            "sensitive_vars": cast("list[YAMLValue]", sorted(sensitive_vars)),
        }
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
