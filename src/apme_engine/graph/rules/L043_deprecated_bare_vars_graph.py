"""GraphRule L043: detect deprecated bare variables in loop directives.

A *bare variable* is a plain identifier used where Ansible historically
allowed it without Jinja2 delimiters::

    with_items: mylist          # bare — should be {{ mylist }}

Using ``{{ var }}`` inside a string value (e.g. ``url: https://{{ host }}/``)
is standard Jinja2 and is **not** a bare-variable violation.

This rule only checks ``with_*`` directive values for bare identifiers
that lack ``{{ }}`` wrapping.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from apme_engine.graph.content_graph import ContentGraph, NodeType
from apme_engine.graph.rule_base import GraphRule, GraphRuleResult
from apme_engine.graph.types import RuleTag as Tag
from apme_engine.graph.types import Severity, YAMLDict

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

_BARE_VAR_RE = re.compile(r"^[a-zA-Z_]\w*(\.\w+)*$")

_LOOP_KEYS = frozenset(
    {
        "with_items",
        "with_list",
        "with_dict",
        "with_fileglob",
        "with_subelements",
        "with_sequence",
        "with_nested",
        "with_first_found",
        "with_indexed_items",
        "with_flattened",
        "with_together",
        "with_cartesian",
        "with_random_choice",
    }
)


def _find_bare_loop_vars(options: Mapping[str, object]) -> list[tuple[str, str]]:
    """Find ``with_*`` values that are bare variable names (no Jinja delimiters).

    Args:
        options: Task options dict (may contain ``with_items``, etc.).

    Returns:
        List of ``(directive_key, bare_value)`` pairs.
    """
    bare: list[tuple[str, str]] = []
    for key, val in options.items():
        if key not in _LOOP_KEYS:
            continue
        if isinstance(val, str) and _BARE_VAR_RE.match(val.strip()):
            bare.append((key, val.strip()))
    return bare


@dataclass
class DeprecatedBareVarsGraphRule(GraphRule):
    """Detect bare variable names in ``with_*`` directives that need ``{{ }}`` wrapping.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L043"
    description: str = "Deprecated bare variable in loop directive; use {{ var }}"
    enabled: bool = True
    name: str = "DeprecatedBareVars"
    version: str = "v0.0.2"
    severity: Severity = Severity.LOW
    tags: tuple[str, ...] = (Tag.VARIABLE,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task or handler nodes that use a ``with_*`` loop directive.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task/handler with a ``with_*`` key in options.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return False
        opts = node.options or {}
        return any(k in _LOOP_KEYS for k in opts)

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Check ``with_*`` values for bare variable names missing ``{{ }}``.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with ``bare_vars`` detail when violated, else pass.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        bare = _find_bare_loop_vars(node.options or {})
        if not bare:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        detail: YAMLDict = cast(
            YAMLDict,
            {"bare_vars": [f"{key}: {val}" for key, val in bare]},
        )
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
