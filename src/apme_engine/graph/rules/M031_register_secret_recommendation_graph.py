"""GraphRule M031: register sensitive-looking values for output masking (2.22+).

Detects sensitive-looking variable names assigned with ``set_fact`` or
captured with ``register`` when their string values are not explicitly
registered with ``ansible.builtin.register_secret``.

Ansible Core 2.22 masks registered values at Ansible-controlled output
boundaries while keeping the original values usable in tasks. Variable names
are only a heuristic; this rule does not inspect values to determine whether
they are secrets.

References:
- https://docs.ansible.com/projects/ansible-core/2.22/collections/ansible/builtin/register_secret_filter.html
- https://docs.ansible.com/projects/ansible-core/2.22/playbook_guide/playbooks_secret_masking.html
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import cast

from apme_engine.graph.content_graph import ContentGraph, ContentNode, NodeType
from apme_engine.graph.rule_base import GraphRule, GraphRuleResult
from apme_engine.graph.sensitivity import var_looks_sensitive
from apme_engine.graph.types import RuleTag as Tag
from apme_engine.graph.types import Severity, YAMLDict, YAMLValue

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

_SET_FACT_MODULES = frozenset(
    {
        "set_fact",
        "ansible.builtin.set_fact",
        "ansible.legacy.set_fact",
    }
)

_ADDITIONAL_SENSITIVE_WORDS = frozenset({"auth_key", "bearer", "jwt", "oauth"})
_ADDITIONAL_SENSITIVE_WORD_RE = re.compile(
    r"(?:^|[_.'\"\[])({})(?:[_.'\"\[\]]|$)".format("|".join(_ADDITIONAL_SENSITIVE_WORDS))
)
_REGISTER_SECRET_FILTER_RE = re.compile(r"\|\s*(?:ansible\.builtin\.)?register_secret\b")


def _var_name_is_sensitive(var_name: str) -> bool:
    """Check if a variable name matches the rule's sensitive-name heuristic.

    Args:
        var_name: Variable name to check.

    Returns:
        True if the variable name contains a sensitive word as a segment.
    """
    return var_looks_sensitive(var_name) or bool(_ADDITIONAL_SENSITIVE_WORD_RE.search(var_name.lower()))


def _value_has_register_secret_filter(value: object) -> bool:
    """Check whether a string expression applies the core secret filter.

    Args:
        value: A YAML value, typically a Jinja expression.

    Returns:
        True if the value contains ``register_secret`` or its FQCN.
    """
    return isinstance(value, str) and bool(_REGISTER_SECRET_FILTER_RE.search(value))


def _find_sensitive_set_facts(node: ContentNode) -> list[str]:
    """Find sensitive-looking set_fact keys without register_secret.

    Args:
        node: Task node using set_fact.

    Returns:
        Sorted variable names whose values lack the secret filter.
    """
    module_options = node.module_options
    if not isinstance(module_options, dict):
        return []

    return sorted(
        key
        for key, value in module_options.items()
        if isinstance(key, str) and _var_name_is_sensitive(key) and not _value_has_register_secret_filter(value)
    )


def _register_projection(node: ContentNode) -> YAMLDict:
    """Return dictionary-form register projections from preserved task options.

    Args:
        node: Task or handler node to inspect.

    Returns:
        The register projection mapping, or an empty mapping.
    """
    raw_register = node.options.get("register")
    return raw_register if isinstance(raw_register, dict) else {}


def _find_sensitive_registered_vars(node: ContentNode) -> list[str]:
    """Find sensitive-looking register variables lacking secret registration.

    String-form ``register`` captures a complete task result and cannot itself
    be passed to the string-only filter. Dictionary-form ``register`` can
    project a secret string from ``_task.result`` and register that value.

    Args:
        node: Task or handler node to inspect.

    Returns:
        Sorted variable names that look sensitive and lack the filter.
    """
    sensitive_names: set[str] = set()

    if node.register and _var_name_is_sensitive(node.register):
        sensitive_names.add(node.register)

    for key, value in _register_projection(node).items():
        if isinstance(key, str) and _var_name_is_sensitive(key) and not _value_has_register_secret_filter(value):
            sensitive_names.add(key)

    return sorted(sensitive_names)


@dataclass
class RegisterSecretRecommendationGraphRule(GraphRule):
    """Recommend registering sensitive-looking strings for output masking.

    The rule uses variable names as a heuristic and covers ``set_fact`` and
    task ``register`` variables. It recommends ``register_secret`` for
    ansible-core 2.22+, which masks registered values in Ansible-controlled
    output without changing the values used by the playbook.

    Task-level ``no_log`` is not an exemption: it censors the producer task's
    result but does not register a value for masking in later tasks. Retain
    ``no_log`` where appropriate as defense in depth.

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
    description: str = "Register sensitive-looking string values for output masking (ansible-core 2.22+)"
    enabled: bool = True
    name: str = "RegisterSecretRecommendation"
    version: str = "v0.0.2"
    severity: Severity = Severity.MEDIUM
    tags: tuple[str, ...] = (Tag.SECURITY, Tag.CODING)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match tasks that set facts or capture task results.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task using set_fact or register.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return False
        return node.module in _SET_FACT_MODULES or bool(node.register or _register_projection(node))

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report sensitive-looking variables without register_secret.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            A GraphRuleResult; its verdict is true when unregistered values
            with sensitive-looking names are found.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        if node.module in _SET_FACT_MODULES:
            sensitive_vars = _find_sensitive_set_facts(node)
            context = "set_fact"
        else:
            sensitive_vars = _find_sensitive_registered_vars(node)
            context = "register"

        if not sensitive_vars:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        vars_formatted = ", ".join(sensitive_vars)
        if context == "set_fact":
            message = (
                f"Variable(s) '{vars_formatted}' have sensitive-looking names; "
                "wrap each sensitive string assigned by set_fact with "
                "`| ansible.builtin.register_secret` (ansible-core 2.22+)."
            )
            recommendation = (
                "Wrap each sensitive string assigned by set_fact with "
                "`| ansible.builtin.register_secret`. Task-level `no_log` may "
                "also hide the producer task's result, but it does not register "
                "the value for masking in later tasks."
            )
        else:
            message = (
                f"Variable(s) '{vars_formatted}' have sensitive-looking names; "
                "project each secret string from `_task.result` through "
                "`| ansible.builtin.register_secret` in dictionary-form "
                "`register` (ansible-core 2.22+)."
            )
            recommendation = (
                "Use dictionary-form `register` to extract each sensitive string "
                "from `_task.result` and apply `| ansible.builtin.register_secret`; "
                "do not pass the full task-result mapping to the string filter. "
                "Keep `no_log: true` on the producer task as defense in depth; "
                "it alone does not mask later uses."
            )

        detail: YAMLDict = {
            "message": message,
            "sensitive_vars": cast("list[YAMLValue]", sensitive_vars),
            "context": context,
            "recommendation": recommendation,
        }

        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
