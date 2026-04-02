"""GraphRule L051: detect Jinja formatting issues (brace spacing and filter pipe spacing).

Graph-aware port of ``L051_jinja.py``.
"""

import re
from dataclasses import dataclass
from typing import cast

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

JINJA_NO_SPACE = re.compile(r"\{\{[^\s\}].*?\}\}|\{\{.*?[^\s\{]\}\}")
JINJA_EXPR_RE = re.compile(r"\{\{\s*(.*?)\s*\}\}")
JINJA_PIPE_BAD = re.compile(r"(?<!\|)\|(?!\|)(?:\S)|\S(?<!\|)\|(?!\|)")


def _task_text_for_jinja(node: object) -> str:
    """Build concatenated text from yaml_lines and string option values.

    Args:
        node: Content graph node (task or handler).

    Returns:
        Single string for Jinja spacing checks.
    """
    yaml_lines = getattr(node, "yaml_lines", "") or ""
    text = yaml_lines
    options = getattr(node, "options", None) or {}
    module_options = getattr(node, "module_options", None) or {}
    for v in (options, module_options):
        if isinstance(v, dict):
            for val in v.values():
                if isinstance(val, str):
                    text += " " + val
    return text


@dataclass
class JinjaGraphRule(GraphRule):
    """Rule for Jinja formatting: brace spacing and filter pipe spacing.

    Detects ``{{foo}}`` (missing brace spaces) and ``foo|bar`` (missing pipe
    spaces) inside Jinja expressions.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L051"
    description: str = "Jinja spacing could be improved"
    enabled: bool = True
    name: str = "Jinja"
    version: str = "v0.0.1"
    severity: Severity = Severity.LOW
    tags: tuple[str, ...] = (Tag.QUALITY,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task or handler nodes.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task or handler.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        return node.node_type in _TASK_TYPES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Check Jinja spacing (braces and pipes) and return result.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with ``bad_expressions`` / ``message`` when violated, else pass.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        text = _task_text_for_jinja(node)
        bad: list[str] = []
        bad.extend(JINJA_NO_SPACE.findall(text))
        for m in JINJA_EXPR_RE.finditer(text):
            inner = m.group(1)
            if JINJA_PIPE_BAD.search(inner):
                bad.append(m.group(0))
        verdict = len(bad) > 0
        detail: YAMLDict | None = None
        if bad:
            detail = cast(
                YAMLDict,
                {
                    "bad_expressions": list(dict.fromkeys(bad))[:10],
                    "message": "Jinja2 spacing could be improved",
                },
            )
        return GraphRuleResult(
            verdict=verdict,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
