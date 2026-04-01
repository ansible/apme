"""GraphRule L086: play-level vars that should be in inventory.

Graph-aware port of ``L086_play_vars_usage.py``.  Matches ``PLAY`` nodes
directly (rather than relying on ``RunTargetType.Play``) and checks the
``node.variables`` dict length.
"""

from __future__ import annotations

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleScope, Severity, YAMLDict
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

MAX_PLAY_VARS = 5


@dataclass
class PlayVarsUsageGraphRule(GraphRule):
    """Detect plays with too many inline vars that belong in inventory.

    Flags plays whose ``variables`` dict exceeds the threshold, suggesting
    the config be moved to ``group_vars`` or ``host_vars``.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
        scope: Structural scope.
    """

    rule_id: str = "L086"
    description: str = "Avoid playbook/play vars for routine config; use inventory vars"
    enabled: bool = True
    name: str = "PlayVarsUsage"
    version: str = "v0.0.2"
    severity: Severity = Severity.LOW
    tags: tuple[str, ...] = (Tag.VARIABLE,)
    scope: str = RuleScope.PLAY

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match play nodes.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True if the node is a play.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        return node.node_type == NodeType.PLAY

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Check play-level vars count against threshold.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with var_count if above threshold.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        play_vars = node.variables
        var_count = len(play_vars) if isinstance(play_vars, dict) else 0
        verdict = var_count > MAX_PLAY_VARS

        detail: YAMLDict = {}
        if verdict:
            detail["var_count"] = var_count
            detail["message"] = "consider moving routine config variables to inventory group_vars"

        return GraphRuleResult(
            verdict=verdict,
            detail=detail if detail else None,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
