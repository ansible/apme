"""GraphRule R114: detect file change with templated path or source."""

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules._module_risk_mapping import (
    get_risk_profile,
    resolve_field,
)
from apme_engine.validators.native.rules.graph_rule_base import (
    GraphRule,
    GraphRuleResult,
    is_templated,
)

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})


@dataclass
class FileChangeGraphRule(GraphRule):
    """Flag file-change tasks with a Jinja-templated path or source.

    A templated file path means the target of the file operation depends
    on variable values, which is a risk when variables are externally
    controlled.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "R114"
    description: str = "A file change with parameterized path found"
    enabled: bool = True
    name: str = "ConfigChange"
    version: str = "v0.0.1"
    severity: str = Severity.LOW
    tags: tuple[str, ...] = (Tag.SYSTEM,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task/handler nodes that use a file-change module.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node's module has a ``file_change`` risk profile.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return False
        profile = get_risk_profile(node.module)
        return profile is not None and profile.risk_type == "file_change"

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Flag when the path or source field contains Jinja2 syntax.

        Both ``path`` and ``src`` are checked independently; either
        (or both) being templated triggers the rule.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult, or None if node is missing.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        profile = get_risk_profile(node.module)
        if profile is None:
            return None

        mo = node.module_options
        if not isinstance(mo, dict):
            mo = {}

        path_val = resolve_field(mo, profile, "path")
        src_val = resolve_field(mo, profile, "src")

        detail: YAMLDict = {}
        if path_val and is_templated(path_val):
            detail["path"] = path_val
        if src_val and is_templated(src_val):
            detail["src"] = src_val

        if not detail:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
