"""GraphRule R105: detect outbound transfer with templated destination."""

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
class OutboundTransferGraphRule(GraphRule):
    """Flag outbound transfers (uri PUT/POST/PATCH) with a templated dest URL.

    A templated destination means the target of outgoing data depends on
    variable values, which is a data-exfiltration risk when variables
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

    rule_id: str = "R105"
    description: str = "An outbound transfer with parameterized destination found"
    enabled: bool = True
    name: str = "OutboundTransfer"
    version: str = "v0.0.1"
    severity: str = Severity.MEDIUM
    tags: tuple[str, ...] = (Tag.NETWORK,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task/handler nodes using ``uri`` with a mutating HTTP method.

        The method must be PUT, POST, or PATCH for the outbound risk
        profile to apply.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a gated outbound-transfer module.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return False
        profile = get_risk_profile(node.module)
        if profile is None or profile.risk_type != "outbound":
            return False
        if profile.method_gate:
            mo = node.module_options
            if not isinstance(mo, dict):
                return False
            method = str(mo.get("method", "GET")).upper()
            return method in profile.method_gate
        return True

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Flag when the destination URL contains Jinja2 template syntax.

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

        dest = resolve_field(mo, profile, "dest")

        if not dest or not is_templated(dest):
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        detail: YAMLDict = {}
        src = resolve_field(mo, profile, "src")
        if src:
            detail["from"] = src
        detail["to"] = dest
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
