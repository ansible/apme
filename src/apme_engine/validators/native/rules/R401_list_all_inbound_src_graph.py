"""GraphRule R401: list all inbound sources across the playbook."""

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict, YAMLValue
from apme_engine.validators.native.rules._module_risk_mapping import (
    get_risk_profile,
    resolve_field,
)
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})


@dataclass
class ListAllInboundSrcGraphRule(GraphRule):
    """Aggregate all inbound-transfer source URLs in the playbook.

    Fires once on the PLAYBOOK node, walking all task descendants to
    collect ``src`` values from inbound-transfer modules.  This is an
    audit/reporting rule (severity VERY_LOW).

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "R401"
    description: str = "List all inbound sources"
    enabled: bool = True
    name: str = "ListAllInboundSrcRule"
    version: str = "v0.0.1"
    severity: str = Severity.VERY_LOW
    tags: tuple[str, ...] = (Tag.DEBUG,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match only PLAYBOOK nodes so the rule fires once per playbook.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True for playbook-level nodes.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        return node.node_type == NodeType.PLAYBOOK

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Walk all task descendants and collect inbound source URLs.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the playbook node.

        Returns:
            GraphRuleResult with ``inbound_src`` list, or None if node missing.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        src_list: list[YAMLValue] = []
        for desc_id in sorted(
            graph.descendants(node_id),
            key=lambda d: (n.file_path, n.line_start, d) if (n := graph.get_node(d)) else ("", 0, d),
        ):
            desc = graph.get_node(desc_id)
            if desc is None or desc.node_type not in _TASK_TYPES:
                continue
            profile = get_risk_profile(desc.module)
            if profile is None or profile.risk_type != "inbound":
                continue
            mo = desc.module_options
            if not isinstance(mo, dict):
                continue
            src = resolve_field(mo, profile, "src")
            if src:
                src_list.append(src)

        if not src_list:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        detail: YAMLDict = {"inbound_src": src_list}
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
