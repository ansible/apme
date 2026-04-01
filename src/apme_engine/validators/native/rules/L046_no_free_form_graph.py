"""GraphRule L046: avoid free-form key=value syntax on module actions."""

import re
from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

_KV_PATTERN = re.compile(r"\b\w+=\S")

_COMMAND_MODULES = frozenset(
    {
        "ansible.builtin.command",
        "ansible.builtin.shell",
        "ansible.builtin.raw",
        "ansible.builtin.script",
        "ansible.legacy.command",
        "ansible.legacy.shell",
        "ansible.legacy.raw",
        "ansible.legacy.script",
        "command",
        "shell",
        "raw",
        "script",
    }
)


@dataclass
class NoFreeFormGraphRule(GraphRule):
    """Detect modules invoked with free-form key=value string arguments.

    The preferred style is a YAML mapping with explicit keys rather than
    a single string containing key=value pairs (e.g. ``stat: path=/tmp``).
    Command-family modules (command, shell, raw, script) are flagged
    when ``_raw_params`` contains a non-empty string.  Other modules
    are flagged when ``_raw_params`` contains ``key=value`` patterns.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L046"
    description: str = "Avoid free-form when calling module actions"
    enabled: bool = True
    name: str = "NoFreeForm"
    version: str = "v0.0.1"
    severity: str = Severity.LOW
    tags: tuple[str, ...] = (Tag.COMMAND,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task/handler nodes with a resolved module name.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task or handler invoking a module.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return False
        return bool(node.module)

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report when _raw_params indicates free-form module invocation.

        For command-family modules, a non-empty ``_raw_params`` string is a
        violation.  For other modules, ``_raw_params`` containing ``key=value``
        patterns is the indicator.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result, or None if node missing.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        mo = node.module_options
        if not isinstance(mo, dict):
            mo = {}

        raw = mo.get("_raw_params", "") or mo.get("_raw", "")
        if isinstance(raw, dict) and "_raw" in raw:
            raw = raw.get("_raw", "")

        resolved = node.module
        is_free_form = False
        if isinstance(raw, str) and raw.strip():
            is_free_form = resolved in _COMMAND_MODULES or bool(_KV_PATTERN.search(raw))

        if not is_free_form:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        detail: YAMLDict = {
            "module": resolved,
            "message": "avoid using free-form when calling module actions",
        }
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
