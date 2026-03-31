"""GraphRule R109: detect config key change with templated key."""

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
class ConfigChangeGraphRule(GraphRule):
    """Flag config-change tasks (rpm_key, apt_key) with a templated key.

    A templated key means the cryptographic key or trust anchor being
    modified depends on variable values, which is a risk when variables
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

    rule_id: str = "R109"
    description: str = "A config key change with parameterized key found"
    enabled: bool = True
    name: str = "ConfigChange"
    version: str = "v0.0.1"
    severity: str = Severity.LOW
    tags: tuple[str, ...] = (Tag.SYSTEM,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task/handler nodes that use a config-change module.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node's module has a ``config_change`` risk profile.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return False
        profile = get_risk_profile(node.resolved_module_name, node.module)
        return profile is not None and profile.risk_type == "config_change"

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Flag when the key field contains Jinja2 template syntax.

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

        key = resolve_field(mo, profile, "key")

        if not key or not is_templated(key):
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        detail: YAMLDict = {"key": key}
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
