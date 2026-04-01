"""ContentGraphScanner — drives GraphRule evaluation over a ContentGraph.

Replaces ``risk_detector.detect()`` for the ContentGraph pipeline.
Iterates over all owned nodes in the graph, applying each GraphRule's
``match`` / ``process`` contract.  Results are collected as
``GraphRuleResult`` objects and aggregated into a ``GraphScanReport``.

Also provides ``graph_report_to_violations`` for converting results to
the ``ViolationDict`` format expected by the gRPC response path.
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
from .models import ViolationDict
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
        NodeType.COLLECTION,
        NodeType.MODULE,
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


# ---------------------------------------------------------------------------
# Result conversion (graph -> violation dicts for gRPC response)
# ---------------------------------------------------------------------------


def graph_report_to_violations(report: GraphScanReport) -> list[ViolationDict]:
    """Convert a GraphScanReport to the flat violation dict list the gRPC response uses.

    Only results with ``verdict=True`` (rule fired, violation detected) are
    included.  Results with ``verdict=False`` are clean passes or errors.

    Args:
        report: Completed scan report from ``scan()``.

    Returns:
        List of ``ViolationDict`` dicts ready for ``violation_dict_to_proto``.
    """
    violations: list[ViolationDict] = []
    for node_result in report.node_results:
        node = node_result.node
        for rr in node_result.rule_results:
            if not rr.verdict:
                continue
            rule = rr.rule
            detail = rr.detail or {}

            file_path = ""
            line: int | list[int] | None = None
            if rr.file:
                if len(rr.file) >= 1:
                    file_path = str(rr.file[0])
                if len(rr.file) >= 2:
                    line = int(rr.file[1])
            elif node:
                file_path = node.file_path
                line = node.line_start if node.line_start else None

            msg = str(detail.get("message", "")) or (rule.description if rule else "")
            scope = str(detail.get("scope", "")) or (rule.scope if rule else "task")
            v: ViolationDict = {
                "rule_id": rule.rule_id if rule else "",
                "level": rule.severity if rule else "",
                "message": msg,
                "file": file_path,
                "line": line,
                "path": rr.node_id,
                "source": "native",
                "scope": scope,
            }

            for key in ("resolved_fqcn", "original_module", "fqcn", "with_key"):
                val = detail.get(key)
                if val is not None:
                    v[key] = str(val)

            affected = detail.get("affected_children")
            if isinstance(affected, int) and affected > 0:
                v["affected_children"] = affected

            violations.append(v)

    return violations
