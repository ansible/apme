"""ContentGraphScanner — drives GraphRule evaluation over a ContentGraph.

Iterates over all owned nodes in the graph, applying each GraphRule's
``match`` / ``process`` contract.  Results are collected as
``GraphRuleResult`` objects and aggregated into a ``GraphScanReport``.

Also provides ``graph_report_to_violations`` for converting results to
the ``ViolationDict`` format expected by the gRPC response path.

Supports inline ``# noqa: <rule_id>`` comments in YAML to suppress
specific rules on a per-task basis.  Native rules skip noqa'd IDs during
``scan()``; ``filter_noqa_violations()`` applies the same comments to
merged validator findings (e.g. OPA) in the engine after fan-out.
"""

from __future__ import annotations

import logging
import os
import re
import time
import traceback
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from apme_engine.graph._loader import load_classes_in_dir
from apme_engine.graph.audit_metadata import AUDIT_JSON_METADATA_KEYS, serialize_audit_metadata_value
from apme_engine.graph.content_graph import ContentGraph, ContentNode, NodeScope, NodeType
from apme_engine.graph.rule_base import GraphRule, GraphRuleResult
from apme_engine.graph.severity import get_severity, severity_to_label
from apme_engine.graph.types import RuleScope, ViolationDict

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
        missing_requested_rule_ids: Rule IDs explicitly requested but not loaded.
        audit_serialization_failures: Count of audit metadata fields dropped on encode failure.
    """

    node_results: list[GraphNodeResult] = field(default_factory=list)
    rules_evaluated: int = 0
    nodes_scanned: int = 0
    elapsed_ms: float = 0.0
    missing_requested_rule_ids: list[str] = field(default_factory=list)
    audit_serialization_failures: int = 0


# ---------------------------------------------------------------------------
# Rule loader
# ---------------------------------------------------------------------------


def native_rules_dir() -> str:
    """Return the absolute path to the built-in graph-rules directory.

    Useful for callers outside the native validator daemon that need
    to load the same rule set (e.g. the Engine remediation bridge).

    Returns:
        Absolute path to ``graph/rules``.
    """
    return os.path.join(os.path.dirname(__file__), "rules")


DISABLED_BY_DEFAULT_GRAPH_RULE_IDS: frozenset[str] = frozenset({"R402", "R404"})


def graph_rule_opt_in_from_rule_configs(rule_configs: Sequence[object] | None) -> list[str]:
    """Return disabled-by-default GraphRule IDs enabled via ``RuleConfig``.

    Args:
        rule_configs: Proto ``RuleConfig`` messages from ``ScanOptions``.

    Returns:
        Sorted rule IDs to opt in when loading native graph rules.
    """
    if not rule_configs:
        return []
    return sorted(
        rc.rule_id  # type: ignore[attr-defined]
        for rc in rule_configs
        if rc.enabled and rc.rule_id in DISABLED_BY_DEFAULT_GRAPH_RULE_IDS  # type: ignore[attr-defined]
    )


def load_graph_rules(
    rules_dir: str = "",
    rule_id_list: list[str] | None = None,
    opt_in_rule_ids: list[str] | None = None,
    exclude_rule_ids: list[str] | None = None,
    *,
    preserve_disabled_defaults: bool = False,
) -> tuple[list[GraphRule], list[str]]:
    """Discover and instantiate GraphRule subclasses from directories.

    Uses the same directory-scanning approach as ``risk_detector.load_rules``
    but filters for ``GraphRule`` subclasses instead of ``Rule``.

    Args:
        rules_dir: Colon-separated directories containing rule modules.
        rule_id_list: If non-empty, only include these rule IDs (restrictive
            whitelist).  IDs listed here are loaded even when the rule class
            sets ``enabled=False``.
        opt_in_rule_ids: In normal (non-whitelist) mode, additionally load
            these disabled-by-default rule IDs.
        exclude_rule_ids: Rule IDs to skip.
        preserve_disabled_defaults: When True, do not flip ``enabled=False`` rules
            to enabled even when explicitly requested via ``rule_id_list`` or
            ``opt_in_rule_ids``.  Used by the ADR-041 catalog collector.

    Returns:
        Tuple of sorted GraphRule instances and any requested rule IDs that
        failed to load.
    """
    if not rules_dir:
        return [], []
    if rule_id_list is None:
        rule_id_list = []
    if opt_in_rule_ids is None:
        opt_in_rule_ids = []
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
                if rule.rule_id in exclude_rule_ids:
                    continue
                if rule_id_list:
                    if rule.rule_id not in rule_id_list:
                        continue
                    explicitly_requested = True
                else:
                    explicitly_requested = rule.rule_id in opt_in_rule_ids
                    if not rule.enabled and not explicitly_requested:
                        continue
                if explicitly_requested and not rule.enabled and not preserve_disabled_defaults:
                    rule = replace(rule, enabled=True)
                rules.append(rule)
            except Exception:
                logger.warning("Failed to instantiate graph rule %s: %s", cls, traceback.format_exc())

    missing_requested: list[str] = []
    requested = (set(rule_id_list) | set(opt_in_rule_ids)) - set(exclude_rule_ids)
    if requested:
        loaded = {r.rule_id for r in rules}
        missing = sorted(requested - loaded)
        if missing:
            logger.warning("Requested graph rules not loaded: %s", missing)
            missing_requested = missing

    rules.sort(key=lambda r: r.precedence)
    return rules, missing_requested


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
        NodeType.RULEBOOK,
        NodeType.RULESET,
    }
)

_NOQA_RE = re.compile(r"(?:^|\s)#\s*noqa:\s*([A-Za-z0-9_,\t ]+)")
_QUOTED_RE = re.compile(r""""[^"\\]*(?:\\.[^"\\]*)*"|'[^']*'""")
_AGGREGATE_SCOPE_NODE_TYPES: dict[str, frozenset[NodeType]] = {
    RuleScope.BLOCK.value: frozenset({NodeType.BLOCK}),
    RuleScope.PLAY.value: frozenset({NodeType.PLAY}),
    RuleScope.PLAYBOOK.value: frozenset({NodeType.PLAYBOOK}),
    RuleScope.ROLE.value: frozenset({NodeType.ROLE}),
    RuleScope.COLLECTION.value: frozenset({NodeType.COLLECTION}),
}


def parse_noqa(yaml_lines: str) -> frozenset[str]:
    """Extract suppressed rule IDs from ``# noqa:`` comments in YAML.

    Supports both single-rule (``# noqa: R108``) and multi-rule
    (``# noqa: R108, L030``) forms.  Rule IDs are normalized to
    uppercase.  Only the first whitespace-delimited token of each
    comma-separated entry is treated as a rule ID, so trailing
    justification text (``# noqa: L068 intentional``, ``# noqa: L068 lola``,
    or ``# noqa: R114 - why trusted``) does not poison the ID.

    Strips simple single- and double-quoted strings before matching
    so that ``# noqa:`` inside typical quoted scalars is ignored.
    YAML's escaped single-quote (``''``) is not handled — this is a
    best-effort heuristic for the common case.

    Args:
        yaml_lines: Raw YAML text for a node.

    Returns:
        Frozen set of suppressed rule IDs (empty if none found).
    """
    suppressed: set[str] = set()
    for line in yaml_lines.splitlines():
        stripped = _QUOTED_RE.sub("", line)
        for match in _NOQA_RE.finditer(stripped):
            for part in match.group(1).split(","):
                token = part.strip().split(None, 1)[0] if part.strip() else ""
                rid = token.upper()
                if rid:
                    suppressed.add(rid)
    return frozenset(suppressed)


def filter_noqa_violations(
    violations: Sequence[ViolationDict],
    graph: ContentGraph | None,
) -> list[ViolationDict]:
    """Drop violations suppressed by ``# noqa:`` on their graph node.

    Used by the engine after validator fan-out so inline noqa comments
    work for findings that carry a ContentGraph ``path`` (node id) with
    non-empty ``yaml_lines`` — notably OPA task-scoped rules such as
    L068.  Native rules already skip noqa'd IDs at match time; filtering
    again here is a no-op for those findings.

    Lookup is by violation ``path`` (ContentGraph ``node_id``).  When
    *graph* is ``None``, or a violation has no matching node / empty
    ``yaml_lines`` / empty ``path``, the violation is kept (fail-open).

    Args:
        violations: Merged violation dicts from one or more validators.
        graph: ContentGraph with ``yaml_lines`` on nodes, or ``None``.

    Returns:
        Violations whose rule ID is not listed in a matching noqa.
    """
    if graph is None or not violations:
        return list(violations)

    kept: list[ViolationDict] = []
    dropped = 0
    noqa_by_path: dict[str, frozenset[str]] = {}
    for violation in violations:
        path = str(violation.get("path") or "").strip()
        rule_id = str(violation.get("rule_id") or "").strip().upper()
        if not path or not rule_id:
            kept.append(violation)
            continue
        node = graph.get_node(path)
        if node is None or not node.yaml_lines:
            kept.append(violation)
            continue
        suppressed = noqa_by_path.get(path)
        if suppressed is None:
            suppressed = parse_noqa(node.yaml_lines)
            noqa_by_path[path] = suppressed
        if rule_id in suppressed:
            dropped += 1
            continue
        kept.append(violation)

    if dropped:
        logger.debug("Filtered %d violation(s) via # noqa on ContentGraph nodes", dropped)
    return kept


def _reset_rule_scan_state(rules: list[GraphRule]) -> None:
    """Reset per-scan mutable state on rules before evaluation.

    Args:
        rules: Enabled rules for the upcoming scan pass.
    """
    for rule in rules:
        rule.reset_scan_state()


def _evaluate_node(
    graph: ContentGraph,
    node: ContentNode,
    enabled_rules: list[GraphRule],
    report: GraphScanReport,
) -> None:
    """Run all rules against a single node and append results to ``report``.

    Rules suppressed via ``# noqa: <rule_id>`` in the node's YAML are
    skipped and are not included in the node's recorded rule results.

    Args:
        graph: ContentGraph being scanned.
        node: Node to evaluate.
        enabled_rules: Pre-filtered list of enabled rules.
        report: Report to accumulate results into (mutated in place).
    """
    report.nodes_scanned += 1
    node_result = GraphNodeResult(node_id=node.node_id, node=node)

    suppressed = parse_noqa(node.yaml_lines) if node.yaml_lines else frozenset()

    for rule in enabled_rules:
        if rule.rule_id.upper() in suppressed:
            continue
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


def scan(
    graph: ContentGraph,
    rules: list[GraphRule],
    *,
    owned_only: bool = True,
    missing_requested_rule_ids: list[str] | None = None,
) -> GraphScanReport:
    """Evaluate all rules against every eligible node in a ContentGraph.

    Iterates nodes in stable order (sorted by ``node_id``).  For each node,
    each enabled rule's ``match`` is tested; on match, ``process`` runs.
    Results are accumulated into a ``GraphScanReport``.

    Args:
        graph: ContentGraph to scan.
        rules: Pre-loaded GraphRule instances.
        owned_only: If True (default), skip ``REFERENCED`` nodes.
        missing_requested_rule_ids: Rule IDs that were requested at load time
            but could not be instantiated (surfaced on the report).

    Returns:
        GraphScanReport with per-node results and timing.
    """
    start = time.monotonic()
    enabled_rules = [r for r in rules if r.enabled]
    report = GraphScanReport(
        rules_evaluated=len(enabled_rules),
        missing_requested_rule_ids=list(missing_requested_rule_ids or ()),
    )
    _reset_rule_scan_state(enabled_rules)

    all_nodes = sorted(graph.nodes(), key=lambda n: n.node_id)

    for node in all_nodes:
        if node.node_type not in _SCANNABLE_TYPES:
            continue
        if owned_only and node.scope != NodeScope.OWNED:
            continue
        _evaluate_node(graph, node, enabled_rules, report)

    report.elapsed_ms = round((time.monotonic() - start) * 1000, 3)
    return report


def rescan_dirty(
    graph: ContentGraph,
    rules: list[GraphRule],
    dirty_node_ids: frozenset[str],
    *,
    owned_only: bool = True,
) -> GraphScanReport:
    """Re-evaluate rules against only the specified (dirty) nodes.

    Called in two contexts: (1) by the native validator servicer when
    ``dirty_node_ids`` are present in a ``ValidateRequest`` (gRPC path),
    and (2) by ``GraphRemediationEngine`` as a fallback when no external
    ``rescan_fn`` bridge is configured (in-process path).

    Args:
        graph: ContentGraph (may have been mutated since last scan).
        rules: Pre-loaded GraphRule instances.
        dirty_node_ids: Node IDs to re-evaluate.
        owned_only: If True (default), skip ``REFERENCED`` nodes
            (consistent with ``scan()``).

    Returns:
        GraphScanReport scoped to the dirty nodes.
    """
    start = time.monotonic()
    enabled_rules = [r for r in rules if r.enabled]
    report = GraphScanReport(rules_evaluated=len(enabled_rules))
    _reset_rule_scan_state(enabled_rules)

    effective_dirty_ids = expand_dirty_node_ids(graph, enabled_rules, dirty_node_ids)
    for node_id in sorted(effective_dirty_ids):
        node = graph.get_node(node_id)
        if node is None:
            continue
        if node.node_type not in _SCANNABLE_TYPES:
            continue
        if owned_only and node.scope != NodeScope.OWNED:
            continue
        _evaluate_node(graph, node, enabled_rules, report)

    report.elapsed_ms = round((time.monotonic() - start) * 1000, 3)
    return report


def expand_dirty_node_ids(
    graph: ContentGraph,
    rules: Sequence[GraphRule],
    dirty_node_ids: frozenset[str],
) -> frozenset[str]:
    """Expand dirty nodes to include ancestors needed by aggregate-scope rules.

    Task-local rescans are sufficient for task-scoped rules, but play-, role-,
    playbook-, block-, and collection-scoped rules may derive findings from
    descendant content. When one of those descendant nodes changes, the enclosing
    aggregate node must also be re-evaluated so remediation convergence and
    audit/reporting rules observe the updated graph state.

    Args:
        graph: ContentGraph containing the dirty nodes.
        rules: Enabled graph rules participating in the rescan.
        dirty_node_ids: Node IDs directly modified since the last pass.

    Returns:
        Dirty node IDs plus any structural ancestors required by enabled
        aggregate-scope rules.
    """
    if not dirty_node_ids:
        return frozenset()

    aggregate_node_types = {
        node_type
        for rule in rules
        for node_type in _AGGREGATE_SCOPE_NODE_TYPES.get(
            rule.scope.value if isinstance(rule.scope, RuleScope) else str(rule.scope),
            frozenset(),
        )
    }
    if not aggregate_node_types:
        return dirty_node_ids

    expanded = set(dirty_node_ids)
    for node_id in dirty_node_ids:
        node = graph.get_node(node_id)
        if node is None:
            continue
        if node.node_type in aggregate_node_types:
            expanded.add(node_id)
        for ancestor_id in graph.positional_ancestor_ids(node_id):
            ancestor = graph.get_node(ancestor_id)
            if ancestor is not None and ancestor.node_type in aggregate_node_types:
                expanded.add(ancestor_id)
    return frozenset(expanded)


# ---------------------------------------------------------------------------
# Result conversion (graph -> violation dicts for gRPC response)
# ---------------------------------------------------------------------------


def graph_report_to_violations(report: GraphScanReport) -> list[ViolationDict]:
    """Convert a GraphScanReport to the flat violation dict list the gRPC response uses.

    Only results with ``verdict=True`` (rule fired, violation detected) are
    included.  Results with ``verdict=False`` are clean passes or errors.

    When ``detail["violations"]`` is a list of dicts, each entry is expanded
    into a separate ViolationDict with its own file/line/message. This
    supports rules like L111 that scan sibling files and emit multiple
    per-file violations from a single graph node.

    Note:
        Increments ``report.audit_serialization_failures`` in place for each
        audit metadata value that fails to serialize.

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
            rid = rule.rule_id if rule else ""

            # Check for nested violations list (e.g., L111 inventory scan)
            nested_violations = detail.get("violations")
            if isinstance(nested_violations, list) and nested_violations:
                expanded: list[ViolationDict] = []
                for nested in nested_violations:
                    if not isinstance(nested, dict):
                        continue
                    nested_line = nested.get("line")
                    line_val: int | list[int] | None = None
                    if isinstance(nested_line, int):
                        line_val = nested_line
                    elif isinstance(nested_line, list):
                        line_val = [int(x) for x in nested_line if isinstance(x, int | float)]
                    expanded.append(
                        {
                            "rule_id": rid,
                            "severity": severity_to_label(get_severity(rid)),
                            "message": str(nested.get("message", rule.description if rule else "")),
                            "file": str(nested.get("file", "")),
                            "line": line_val,
                            "path": rr.node_id,
                            "source": "native",
                            "scope": str(detail.get("scope", "")) or (rule.scope if rule else "task"),
                        }
                    )
                if expanded:
                    violations.extend(expanded)
                    continue

            # Standard single-violation path
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
            v = {
                "rule_id": rid,
                "severity": severity_to_label(get_severity(rid)),
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

            for key in AUDIT_JSON_METADATA_KEYS:
                val = detail.get(key)
                if val is not None:
                    serialized = serialize_audit_metadata_value(val, rule_id=rid, key=key)
                    if serialized is not None:
                        v[key] = serialized
                    else:
                        report.audit_serialization_failures += 1

            affected = detail.get("affected_children")
            if isinstance(affected, int) and affected > 0:
                v["affected_children"] = affected

            violations.append(v)

    return violations
