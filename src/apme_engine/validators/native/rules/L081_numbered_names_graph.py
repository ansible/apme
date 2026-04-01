"""GraphRule L081: do not number roles or playbooks.

Graph-aware port of ``L081_numbered_names.py``. Uses ``file_path`` on
TASK, HANDLER, and ROLE nodes to detect numbered basenames.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleScope, Severity, YAMLDict
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_NUMBERED_PREFIX = re.compile(r"^\d+[_\-.]")
_TASK_HANDLER_ROLE = frozenset({NodeType.TASK, NodeType.HANDLER, NodeType.ROLE})


@dataclass
class NumberedNamesGraphRule(GraphRule):
    """Detect numbered file names such as ``01_setup.yml``.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
        scope: Structural scope.
        precedence: Evaluation order (lower = earlier).
    """

    rule_id: str = "L081"
    description: str = "Do not number roles or playbooks"
    enabled: bool = True
    name: str = "NumberedNames"
    version: str = "v0.0.1"
    severity: Severity = Severity.LOW
    tags: tuple[str, ...] = (Tag.CODING,)
    scope: str = RuleScope.PLAYBOOK
    precedence: int = 10

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match TASK, HANDLER, or ROLE nodes with a file path.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True if the node type is eligible and ``file_path`` is non-empty.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_HANDLER_ROLE:
            return False
        return bool((node.file_path or "").strip())

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Flag basenames that start with digits and a separator.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with ``filename`` detail when violated, else pass.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        filepath = (node.file_path or "").strip()
        if not filepath:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        basename = os.path.basename(filepath)
        verdict = bool(_NUMBERED_PREFIX.match(basename))
        detail: YAMLDict = {}
        if verdict:
            detail["filename"] = basename
            detail["message"] = "do not number roles or playbooks; use descriptive names"
        return GraphRuleResult(
            verdict=verdict,
            detail=detail if verdict else None,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
