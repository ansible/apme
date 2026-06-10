"""GraphRule M031: variables with sensitive names should use Sensitive tag (2.19+).

Detects variables that contain or reference sensitive data (passwords, tokens,
secrets, API keys) and recommends tagging them with ansible-core 2.19+'s
Sensitive data tag for automatic redaction in job output.

This rule supports ANSTRAT-1720: Selective Redaction of Sensitive Variables in
Job Output. The Sensitive tag is the recommended V2 approach per the design
document, enabling value-based redaction that propagates through Jinja
templating and string operations.

References:
- ansible-core 2.19+ data tagging: https://docs.ansible.com/ansible/devel/
- ANSTRAT-1720: Approach C (Data Tagging with Sensitive Tag)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import cast

from apme_engine.engine.content_graph import ContentGraph, ContentNode, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict, YAMLValue
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

_SET_FACT_MODULES = frozenset(
    {
        "set_fact",
        "ansible.builtin.set_fact",
        "ansible.legacy.set_fact",
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
        "client_secret",
        "auth_key",
        "bearer",
        "jwt",
        "oauth",
    }
)

_WORD_BOUNDARY_RE = re.compile(r"(?:^|[_.'\"\[])({})(?:[_.'\"\[\]]|$)".format("|".join(_SENSITIVE_WORDS)))


def _var_name_is_sensitive(var_name: str) -> bool:
    """Check if variable name matches sensitive patterns.

    Uses word-boundary matching to avoid false positives like 'secretary_name'
    (contains 'secret') or 'tokenized_value' (contains 'token'). Matches when
    a sensitive word appears as a complete segment bounded by underscores,
    dots, brackets, or string start/end.

    Args:
        var_name: Variable name to check.

    Returns:
        True if the variable name contains a sensitive word as a segment.
    """
    lower = var_name.lower()
    return bool(_WORD_BOUNDARY_RE.search(lower))


def _find_sensitive_set_facts(node: ContentNode) -> list[str]:
    """Find sensitive variable names being set via set_fact.

    Examines the module_options for set_fact tasks and identifies any keys
    (variable names being defined) that match sensitive patterns.

    Args:
        node: Task node using set_fact module.

    Returns:
        Sorted list of sensitive variable names being set.
    """
    sensitive_found: set[str] = set()
    mo = node.module_options if isinstance(node.module_options, dict) else {}

    for key in mo:
        if isinstance(key, str) and _var_name_is_sensitive(key):
            sensitive_found.add(key)

    return sorted(sensitive_found)


def _find_sensitive_registered_vars(node: ContentNode) -> str | None:
    """Check if a task registers a sensitive variable.

    Examines the register keyword on a task to determine if the variable
    name being registered matches sensitive patterns.

    Args:
        node: Task node to inspect.

    Returns:
        The registered variable name if sensitive, None otherwise.
    """
    register = node.register
    if register and isinstance(register, str) and _var_name_is_sensitive(register):
        return register
    return None


def _check_no_log_effective(graph: ContentGraph, node_id: str) -> bool:
    """Check if no_log is effectively True at this node scope.

    Walks from task outward (closest to farthest) and returns on the first
    explicit no_log setting. This correctly handles scope overrides.

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
class SensitiveTagRecommendationGraphRule(GraphRule):
    """Recommend Sensitive tag for variables handling sensitive data.

    Detects tasks that define or register variables with sensitive-looking
    names (password, token, secret, api_key, etc.) and recommends using
    ansible-core 2.19+'s Sensitive data tag for automatic output redaction.

    This rule complements L110 (debug tasks with sensitive vars) and L047
    (password-like parameters) by addressing the broader variable lifecycle.
    While no_log: true suppresses entire task output, the Sensitive tag
    enables selective redaction of specific values while preserving audit
    visibility of non-sensitive output.

    Targets:
    - set_fact tasks defining sensitive variables
    - Tasks registering results to sensitive variable names

    Does not fire if no_log: true is set (user has already addressed the
    concern, though Sensitive tag would be a better long-term solution).

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "M031"
    description: str = "Variables with sensitive names should use Sensitive tag (ansible-core 2.19+)"
    enabled: bool = True
    name: str = "SensitiveTagRecommendation"
    version: str = "v0.0.1"
    severity: Severity = Severity.MEDIUM
    tags: tuple[str, ...] = (Tag.SECURITY, Tag.CODING)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match tasks that set or register variables.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task using set_fact or has a register.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        if node.node_type not in _TASK_TYPES:
            return False

        if node.module in _SET_FACT_MODULES:
            return True

        return bool(node.register)

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Check if task sets/registers sensitive variables without Sensitive tag.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult; verdict True when sensitive vars found without protection.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        sensitive_vars: list[str] = []
        context: str = ""

        if node.module in _SET_FACT_MODULES:
            sensitive_vars = _find_sensitive_set_facts(node)
            context = "set_fact"
        elif node.register:
            registered = _find_sensitive_registered_vars(node)
            if registered:
                sensitive_vars = [registered]
                context = "register"

        if not sensitive_vars:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        if _check_no_log_effective(graph, node_id):
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        vars_formatted = ", ".join(sensitive_vars)
        detail: YAMLDict = {
            "message": (
                f"Variable(s) '{vars_formatted}' contain sensitive data; "
                "recommend using Sensitive tag (ansible-core 2.19+) for automatic "
                "redaction in job output. See ANSTRAT-1720 for migration guidance."
            ),
            "sensitive_vars": cast("list[YAMLValue]", sensitive_vars),
            "context": context,
            "recommendation": (
                "Apply Sensitive tag to values at definition time for automatic "
                "redaction that propagates through Jinja templating and string operations."
            ),
        }

        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
