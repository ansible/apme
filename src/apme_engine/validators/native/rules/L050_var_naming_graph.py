"""GraphRule L050: variable names must be lowercase with underscores."""

import re
from dataclasses import dataclass
from typing import cast

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import (
    GraphRule,
    GraphRuleResult,
)

_VALID_VAR_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")

_SCOPED_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER, NodeType.PLAY, NodeType.ROLE})

_SET_FACT_MODULES = frozenset(
    {
        "ansible.builtin.set_fact",
        "ansible.legacy.set_fact",
        "set_fact",
    }
)

_INCLUDE_VARS_MODULES = frozenset(
    {
        "ansible.builtin.include_vars",
        "ansible.legacy.include_vars",
        "include_vars",
    }
)

_SET_FACT_META_KEYS = frozenset({"cacheable"})


def _is_bad_name(name: str) -> bool:
    """Check if a variable name violates the lowercase+underscore convention.

    Args:
        name: Variable name to check.

    Returns:
        True when the name is non-empty and does not match ``[a-z_][a-z0-9_]*``.
    """
    return bool(name) and _VALID_VAR_NAME.match(name) is None


@dataclass
class VarNamingGraphRule(GraphRule):
    """Detect variable names that are not lowercase with underscores.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L050"
    description: str = "Variable names should use lowercase and underscores only"
    enabled: bool = True
    name: str = "VarNaming"
    version: str = "v0.0.1"
    severity: Severity = Severity.LOW
    tags: tuple[str, ...] = (Tag.VARIABLE,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match nodes that can define variables.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True for task, handler, play, and role nodes.
        """
        node = graph.get_node(node_id)
        return node is not None and node.node_type in _SCOPED_TYPES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report variable names violating the lowercase+underscore convention.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result with ``verdict`` True when violations exist.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        bad_names: list[str] = []

        for key in node.variables:
            if _is_bad_name(key):
                bad_names.append(key)

        if node.node_type in (NodeType.TASK, NodeType.HANDLER):
            mo = node.module_options if isinstance(node.module_options, dict) else {}

            if node.module in _SET_FACT_MODULES:
                for key in mo:
                    if key not in _SET_FACT_META_KEYS and _is_bad_name(key):
                        bad_names.append(key)

            if node.module in _INCLUDE_VARS_MODULES:
                var_name = mo.get("name")
                if isinstance(var_name, str) and _is_bad_name(var_name):
                    bad_names.append(var_name)

            if node.register and _is_bad_name(node.register):
                bad_names.append(node.register)

        if node.node_type == NodeType.ROLE:
            for key in node.default_variables:
                if _is_bad_name(key):
                    bad_names.append(key)
            for key in node.role_variables:
                if _is_bad_name(key):
                    bad_names.append(key)

        if not bad_names:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        unique_names = sorted(set(bad_names))
        detail: YAMLDict = cast(
            YAMLDict,
            {
                "message": (f"Variable name(s) should be lowercase with underscores: {', '.join(unique_names)}"),
                "bad_names": unique_names,
            },
        )
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
