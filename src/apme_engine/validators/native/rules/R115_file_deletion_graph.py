"""GraphRule R115: detect file deletion with templated path."""

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
class FileDeletionGraphRule(GraphRule):
    """Flag file-deletion tasks (``state: absent``) with a templated path.

    A templated deletion path means the file being removed depends on
    variable values, which is a destructive-action risk when variables
    are externally controlled.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "R115"
    description: str = "A file deletion with parameterized path found"
    enabled: bool = True
    name: str = "FileDeletionRule"
    version: str = "v0.0.1"
    severity: str = Severity.LOW
    tags: tuple[str, ...] = (Tag.SYSTEM,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match file-change modules with ``state: absent``.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a file-change module with deletion state.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return False
        profile = get_risk_profile(node.resolved_module_name, node.module)
        if profile is None or profile.risk_type != "file_change":
            return False
        mo = node.module_options
        if not isinstance(mo, dict):
            return False
        state = resolve_field(mo, profile, "state")
        return state is not None and state == "absent"

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Flag when the deletion path contains Jinja2 template syntax.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult, or None if node is missing.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        profile = get_risk_profile(node.resolved_module_name, node.module)
        if profile is None:
            return None

        mo = node.module_options
        if not isinstance(mo, dict):
            mo = {}

        path_val = resolve_field(mo, profile, "path")

        if not path_val or not is_templated(path_val):
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        detail: YAMLDict = {"path": path_val}
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
