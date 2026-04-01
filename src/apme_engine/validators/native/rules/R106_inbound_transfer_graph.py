"""GraphRule R106: detect inbound transfer with templated source."""

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
class InboundTransferGraphRule(GraphRule):
    """Flag inbound transfers with a Jinja-templated source URL.

    A templated source means the downloaded content depends on variable
    values, which is a supply-chain risk when variables are externally
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

    rule_id: str = "R106"
    description: str = "An inbound transfer with parameterized source found"
    enabled: bool = True
    name: str = "InboundTransfer"
    version: str = "v0.0.1"
    severity: str = Severity.MEDIUM
    tags: tuple[str, ...] = (Tag.NETWORK,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task/handler nodes that use an inbound-transfer module.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node's module has an ``inbound`` risk profile.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return False
        profile = get_risk_profile(node.module)
        return profile is not None and profile.risk_type == "inbound"

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Flag when the source field contains Jinja2 template syntax.

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

        src = resolve_field(mo, profile, "src")

        if not src or not is_templated(src):
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        detail: YAMLDict = {"from": src}
        dest = resolve_field(mo, profile, "dest")
        if dest:
            detail["to"] = dest
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
