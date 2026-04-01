"""GraphRule L083: detect hardcoded host group names in roles.

Graph-aware port of ``L083_hardcoded_group.py``.
"""

import re
from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

_GROUP_REF = re.compile(r"groups\[(['\"])(\w+)\1\]")


@dataclass
class HardcodedGroupGraphRule(GraphRule):
    """Rule for detecting hardcoded host group names in role tasks.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L083"
    description: str = "Do not hardcode host group names in roles"
    enabled: bool = True
    name: str = "HardcodedGroup"
    version: str = "v0.0.1"
    severity: Severity = Severity.LOW
    tags: tuple[str, ...] = (Tag.VARIABLE,)

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
        """Check for hardcoded group names in role tasks.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with ``found_groups`` / ``message`` in role paths when violated;
            otherwise pass (including non-role files).
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        filepath = node.file_path or ""
        if "/roles/" not in filepath and not filepath.startswith("roles/"):
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        yaml_lines = getattr(node, "yaml_lines", "") or ""
        found_groups = sorted(set(m.group(2) for m in _GROUP_REF.finditer(yaml_lines)))
        skip_groups = {"all", "ungrouped"}
        found_groups = [g for g in found_groups if g not in skip_groups]
        verdict = len(found_groups) > 0
        detail: YAMLDict | None = None
        if found_groups:
            detail = {
                "found_groups": found_groups,
                "message": "do not hardcode host group names in roles; parameterize them",
            }
        return GraphRuleResult(
            verdict=verdict,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
