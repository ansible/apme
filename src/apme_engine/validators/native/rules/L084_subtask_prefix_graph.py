"""GraphRule L084: task names in included role files should use a prefix.

Graph-aware port of ``L084_subtask_prefix.py``. Applies to tasks and
handlers under ``roles/`` whose YAML file is not ``main.yml``/``main.yaml``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})


@dataclass
class SubtaskPrefixGraphRule(GraphRule):
    """Require a ``|`` prefix pattern in task names for non-main role includes.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
        precedence: Evaluation order (lower = earlier).
    """

    rule_id: str = "L084"
    description: str = "Task names in included sub-task files should use a prefix (e.g. 'sub | Description')"
    enabled: bool = True
    name: str = "SubtaskPrefix"
    version: str = "v0.0.1"
    severity: str = Severity.VERY_LOW
    tags: tuple[str, ...] = (Tag.CODING,)
    precedence: int = 10

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match named tasks/handlers in non-main files under ``roles/``.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True if the node is a named TASK/HANDLER in an included role task file.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return False
        if not node.name:
            return False
        fp = node.file_path or ""
        if "/roles/" not in fp:
            return False
        basename = os.path.basename(fp)
        return basename not in ("main.yml", "main.yaml")

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Flag task names that omit the ``sub |``-style prefix separator.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with ``task_name`` and ``file`` detail when violated.
        """
        node = graph.get_node(node_id)
        if node is None or not node.name:
            return None
        basename = os.path.basename(node.file_path or "")
        verdict = "|" not in node.name
        detail: YAMLDict = {}
        if verdict:
            detail["task_name"] = node.name
            detail["file"] = basename
            detail["message"] = "task names in included files should use prefix (e.g. 'sub | Description')"
        return GraphRuleResult(
            verdict=verdict,
            detail=detail if verdict else None,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
