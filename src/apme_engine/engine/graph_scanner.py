"""ContentGraphScanner — drives GraphRule evaluation over a ContentGraph.

Replaces ``risk_detector.detect()`` for the ContentGraph pipeline.
Iterates over all owned nodes in the graph, applying each GraphRule's
``match`` / ``process`` contract.  Results are collected as
``GraphRuleResult`` objects and aggregated into a ``GraphScanReport``.

Used behind the ``APME_USE_CONTENT_GRAPH`` feature flag during Phase 2.
"""

from __future__ import annotations

import logging
import os
import time
import traceback
from dataclasses import dataclass, field

from apme_engine.validators.native.rules.graph_rule_base import (
    GraphRule,
    GraphRuleResult,
)

from .content_graph import ContentGraph, ContentNode, NodeScope, NodeType
from .utils import load_classes_in_dir

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scan report
# ---------------------------------------------------------------------------


@dataclass
class GraphNodeResult:
    """Results of evaluating all rules against a single graph node.

    Attributes:
        node_id: ContentGraph node identifier.
        node: ContentNode snapshot for reference.
        rule_results: Outcomes from every matched rule.
    """

    node_id: str = ""
    node: ContentNode | None = None
    rule_results: list[GraphRuleResult] = field(default_factory=list)


@dataclass
class GraphScanReport:
    """Aggregated results of a full ContentGraph scan.

    Attributes:
        node_results: Per-node rule outcomes.
        rules_evaluated: Number of enabled rules in the scan.
        nodes_scanned: Number of nodes visited.
        elapsed_ms: Total wall-clock time in milliseconds.
    """

    node_results: list[GraphNodeResult] = field(default_factory=list)
    rules_evaluated: int = 0
    nodes_scanned: int = 0
    elapsed_ms: float = 0.0


# ---------------------------------------------------------------------------
# Rule loader
# ---------------------------------------------------------------------------


def load_graph_rules(
    rules_dir: str = "",
    rule_id_list: list[str] | None = None,
    exclude_rule_ids: list[str] | None = None,
) -> list[GraphRule]:
    """Discover and instantiate GraphRule subclasses from directories.

    Uses the same directory-scanning approach as ``risk_detector.load_rules``
    but filters for ``GraphRule`` subclasses instead of ``Rule``.

    Args:
        rules_dir: Colon-separated directories containing rule modules.
        rule_id_list: If provided, only include these rule IDs.
        exclude_rule_ids: Rule IDs to skip.

    Returns:
        Sorted list of enabled GraphRule instances.
    """
    if not rules_dir:
        return []
    if rule_id_list is None:
        rule_id_list = []
    if exclude_rule_ids is None:
        exclude_rule_ids = []

    rules: list[GraphRule] = []
    for directory in rules_dir.split(":"):
        if not os.path.isdir(directory):
            continue
        classes, errors = load_classes_in_dir(directory, GraphRule, fail_on_error=False)
        for err in errors:
            logger.warning("Skipped graph rule: %s", err)
        for cls in classes:
            try:
                rule = cls()
                if not isinstance(rule, GraphRule):
                    continue
                if rule_id_list and rule.rule_id not in rule_id_list:
                    continue
                if rule.rule_id in exclude_rule_ids:
                    continue
                if not rule.enabled:
                    continue
                rules.append(rule)
            except Exception:
                logger.warning("Failed to instantiate graph rule %s: %s", cls, traceback.format_exc())

    rules.sort(key=lambda r: r.precedence)
    return rules


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

_SCANNABLE_TYPES = frozenset(
    {
        NodeType.TASK,
        NodeType.HANDLER,
        NodeType.BLOCK,
        NodeType.PLAY,
        NodeType.ROLE,
        NodeType.TASKFILE,
        NodeType.PLAYBOOK,
    }
)


def scan(
    graph: ContentGraph,
    rules: list[GraphRule],
    *,
    owned_only: bool = True,
) -> GraphScanReport:
    """Evaluate all rules against every eligible node in a ContentGraph.

    Iterates nodes in stable order (sorted by ``node_id``).  For each node,
    each enabled rule's ``match`` is tested; on match, ``process`` runs.
    Results are accumulated into a ``GraphScanReport``.

    Args:
        graph: ContentGraph to scan.
        rules: Pre-loaded GraphRule instances.
        owned_only: If True (default), skip ``REFERENCED`` nodes.

    Returns:
        GraphScanReport with per-node results and timing.
    """
    start = time.monotonic()
    enabled_rules = [r for r in rules if r.enabled]
    report = GraphScanReport(rules_evaluated=len(enabled_rules))

    all_nodes = sorted(graph.nodes(), key=lambda n: n.node_id)

    for node in all_nodes:
        if node.node_type not in _SCANNABLE_TYPES:
            continue
        if owned_only and node.scope != NodeScope.OWNED:
            continue

        report.nodes_scanned += 1
        node_result = GraphNodeResult(node_id=node.node_id, node=node)

        for rule in enabled_rules:
            try:
                matched = rule.match(graph, node.node_id)
                if not matched:
                    continue
                result = rule.process(graph, node.node_id)
                if result is not None:
                    result.rule = rule.get_metadata()
                    node_result.rule_results.append(result)
            except Exception as err:
                logger.warning(
                    "Rule %s failed on %s: %s",
                    rule.rule_id,
                    node.node_id,
                    err,
                    exc_info=True,
                )
                node_result.rule_results.append(
                    GraphRuleResult(
                        rule=rule.get_metadata(),
                        verdict=False,
                        node_id=node.node_id,
                        error=f"Rule execution failed: {type(err).__name__}: {err}",
                    )
                )

        if node_result.rule_results:
            report.node_results.append(node_result)

    report.elapsed_ms = round((time.monotonic() - start) * 1000, 3)
    return report
