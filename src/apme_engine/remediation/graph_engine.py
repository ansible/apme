"""Graph-aware remediation engine — in-memory convergence on ContentGraph.

Replaces the disk-based convergence loop for Tier 1 transforms.  The
``ContentGraph`` acts as a mutable working copy: transforms modify
``ContentNode.yaml_lines`` in memory, dirty nodes are rescanned with
only graph rules (no full pipeline rebuild), and files on disk are
never touched until final approval via ``splice_modifications()``.

See :doc:`/sdlc/research/nodestate-progression-design` for the full
design rationale.
"""

from __future__ import annotations

import difflib
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from apme_engine.engine.content_graph import ContentGraph
from apme_engine.engine.graph_scanner import (
    graph_report_to_violations,
    rescan_dirty,
    scan,
)
from apme_engine.engine.models import ViolationDict
from apme_engine.remediation.engine import FilePatch
from apme_engine.remediation.partition import normalize_rule_id, partition_violations
from apme_engine.remediation.registry import TransformRegistry
from apme_engine.validators.native.rules.graph_rule_base import GraphRule

logger = logging.getLogger("apme.remediation.graph")

ProgressCallback = Callable[[str, str, float, int], None]
RescanFn = Callable[[ContentGraph, frozenset[str]], Awaitable[list["ViolationDict"]]]


@dataclass
class GraphFixReport:
    """Summary of a graph-aware remediation run.

    ``applied_patches`` is **not** populated by ``remediate()`` itself.
    Callers produce patches by passing the post-convergence graph to
    :func:`splice_modifications`, then store the result here.

    Attributes:
        passes: Number of convergence passes executed.
        fixed: Count of violations fixed by Tier 1 transforms.
        applied_patches: File patches produced by ``splice_modifications``
            (populated by the caller, not by ``remediate``).
        remaining_violations: Violations still present after convergence.
        fixed_violations: Violations resolved by transforms.
        oscillation_detected: True if the loop bailed due to oscillation.
        nodes_modified: Number of ContentNodes modified.
    """

    passes: int = 0
    fixed: int = 0
    applied_patches: list[FilePatch] = field(default_factory=list)
    remaining_violations: list[ViolationDict] = field(default_factory=list)
    fixed_violations: list[ViolationDict] = field(default_factory=list)
    oscillation_detected: bool = False
    nodes_modified: int = 0


class GraphRemediationEngine:
    """In-memory convergence loop on a ContentGraph.

    Operates entirely in memory — no files are written to disk during
    convergence.  Line numbers always reference the original file.
    After convergence, call :func:`splice_modifications` to produce
    file patches.

    The engine does NOT own scanning — it receives pre-loaded
    ``GraphRule`` instances and a ``TransformRegistry``.
    """

    def __init__(
        self,
        registry: TransformRegistry,
        graph: ContentGraph,
        rules: list[GraphRule],
        *,
        max_passes: int = 5,
        progress_callback: ProgressCallback | None = None,
        rescan_fn: RescanFn | None = None,
    ) -> None:
        """Initialize the graph remediation engine.

        Args:
            registry: Transform registry mapping rule IDs to fix functions.
            graph: ContentGraph to remediate (mutated in place).
            rules: Pre-loaded GraphRule instances for re-scanning.
            max_passes: Maximum convergence passes (default 5).
            progress_callback: Optional ``(phase, message, fraction, level)``
                callback for streaming progress.
            rescan_fn: Optional callback that replaces the built-in
                ``rescan_dirty`` call during convergence.  Receives
                ``(graph, dirty_node_ids)`` and returns violations.
                When set, this enables the validator bridge — the
                caller can fan out to real gRPC validators instead of
                only in-memory graph rules.
        """
        self._registry = registry
        self._graph = graph
        self._rules = rules
        self._max_passes = max_passes
        self._progress_cb = progress_callback
        self._rescan_fn = rescan_fn

    def _progress(
        self,
        phase: str,
        message: str,
        fraction: float = 0.0,
        level: int = 2,
    ) -> None:
        if self._progress_cb is not None:
            try:
                self._progress_cb(phase, message, fraction, level)
            except Exception:
                logger.warning("Progress callback raised; ignoring", exc_info=True)

    async def remediate(
        self,
        initial_violations: list[ViolationDict] | None = None,
    ) -> GraphFixReport:
        """Run the in-memory convergence loop.

        All transforms start as pending entries.  After the loop,
        ``graph.approve_pending()`` auto-approves deterministic
        transforms.  AI transforms (applied in a separate phase
        by the caller) remain pending for human review.

        Args:
            initial_violations: Pre-computed violations from a prior scan.
                When ``None``, an initial full graph scan is performed.

        Returns:
            GraphFixReport with patches, counts, and remaining violations.
        """
        graph = self._graph
        registry = self._registry

        if initial_violations is None:
            initial_report = scan(graph, self._rules)
            violations = graph_report_to_violations(initial_report)
        else:
            violations = list(initial_violations)

        _record_violations(graph, violations, pass_number=0, phase="scanned")

        prev_count: float = float("inf")
        passes = 0
        all_fixed: list[ViolationDict] = []
        oscillation = False

        for pass_num in range(1, self._max_passes + 1):
            passes = pass_num
            self._progress("graph-tier1", f"Pass {pass_num}/{self._max_passes}")

            tier1, _, _ = partition_violations(violations, registry)

            if not tier1:
                self._progress("graph-tier1", f"Converged at pass {pass_num} (0 fixable)")
                logger.info("Graph remediation: converged at pass %d", pass_num)
                break

            self._progress(
                "graph-tier1",
                f"Pass {pass_num}: {len(tier1)} fixable violations",
            )

            applied_this_pass = 0
            for v in tier1:
                rule_id = normalize_rule_id(str(v.get("rule_id", "")))
                node_id = str(v.get("path", ""))

                transform_fn = registry.get_node_transform(rule_id)
                if transform_fn is None:
                    continue

                applied = await graph.apply_transform(node_id, transform_fn, v)
                if applied:
                    applied_this_pass += 1
                    all_fixed.append(dict(v))

            for nid in graph.dirty_nodes:
                node = graph.get_node(nid)
                if node is not None:
                    node.record_state(pass_num, "transformed", source="deterministic")

            self._progress(
                "graph-tier1",
                f"Pass {pass_num}: {applied_this_pass} transforms applied",
            )

            if applied_this_pass == 0:
                logger.debug(
                    "Graph remediation: pass %d no transforms applied",
                    pass_num,
                )
                break

            dirty = graph.dirty_nodes
            if self._rescan_fn is not None:
                new_violations = await self._rescan_fn(graph, dirty)
            else:
                rescan_report = rescan_dirty(graph, self._rules, dirty)
                new_violations = graph_report_to_violations(rescan_report)

            _record_violations(
                graph,
                new_violations,
                pass_number=pass_num,
                phase="scanned",
                dirty_node_ids=dirty,
            )

            graph.clear_dirty()

            new_tier1, _, _ = partition_violations(new_violations, registry)
            new_fixable = len(new_tier1)

            if new_fixable >= prev_count:
                logger.warning(
                    "Graph remediation: oscillation at pass %d (%d >= %d)",
                    pass_num,
                    new_fixable,
                    prev_count,
                )
                self._progress(
                    "graph-tier1",
                    f"Oscillation at pass {pass_num}",
                    level=3,
                )
                oscillation = True
                break

            prev_count = new_fixable
            violations = new_violations

            if new_fixable == 0:
                self._progress(
                    "graph-tier1",
                    f"Fully converged at pass {pass_num}",
                )
                logger.info("Graph remediation: fully converged at pass %d", pass_num)
                break

        # Auto-approve all deterministic transforms
        graph.approve_pending()

        final_report = scan(graph, self._rules)
        remaining = graph_report_to_violations(final_report)

        return GraphFixReport(
            passes=passes,
            fixed=len(all_fixed),
            remaining_violations=remaining,
            fixed_violations=all_fixed,
            oscillation_detected=oscillation,
            nodes_modified=_count_modified_nodes(graph),
        )


def splice_modifications(
    graph: ContentGraph,
    originals: dict[str, str],
    *,
    include_pending: bool = False,
) -> list[FilePatch]:
    """Splice modified ``yaml_lines`` back into original files.

    Groups modified nodes by ``file_path``, sorts by ``line_start``
    descending (bottom-up) so that splicing one node does not shift
    line numbers for nodes above it, and produces a unified diff per
    file.

    Args:
        graph: ContentGraph after convergence (nodes may have updated
            ``yaml_lines``).
        originals: Map of ``file_path`` to original file content
            (before any transforms).
        include_pending: When ``True``, use the latest progression
            entry (approved or not) instead of the last approved one.
            Set to ``True`` during convergence re-scans so external
            validators see the current in-memory state.

    Returns:
        List of ``FilePatch`` objects for files that changed.
    """
    _Edit = tuple[int, int, str, list[str]]
    modified_by_file: dict[str, list[_Edit]] = defaultdict(list)

    for node in graph.nodes():
        if not node.progression or len(node.progression) < 2:
            continue
        if not node.file_path or not node.yaml_lines:
            continue
        if node.line_start <= 0 or node.line_end <= 0:
            continue
        if node.line_end < node.line_start:
            continue

        original_hash = node.progression[0].content_hash
        if include_pending:
            effective = node.progression[-1]
        else:
            effective = next(
                (s for s in reversed(node.progression) if s.approved),
                node.progression[0],
            )
        if original_hash == effective.content_hash:
            continue

        node_rule_ids = list(node.progression[0].violations) if node.progression[0].violations else []

        modified_by_file[node.file_path].append(
            (node.line_start, node.line_end, effective.yaml_lines, node_rule_ids),
        )

    patches: list[FilePatch] = []
    for file_path, edits in modified_by_file.items():
        original = originals.get(file_path)
        if original is None:
            continue

        lines = original.splitlines(keepends=True)
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"

        # Bottom-up to preserve line offsets
        edits.sort(key=lambda e: e[0], reverse=True)
        rule_ids: list[str] = []

        for line_start, line_end, yaml_text, edit_rules in edits:
            new_lines = yaml_text.splitlines(keepends=True)
            if new_lines and not new_lines[-1].endswith("\n"):
                new_lines[-1] += "\n"
            # line_start/line_end are 1-based inclusive; Python slice
            # [start-1:end] is equivalent because slice end is exclusive.
            lines[line_start - 1 : line_end] = new_lines
            rule_ids.extend(edit_rules)

        patched = "".join(lines)

        if patched != original:
            diff = "".join(
                difflib.unified_diff(
                    original.splitlines(keepends=True),
                    patched.splitlines(keepends=True),
                    fromfile=f"a/{file_path}",
                    tofile=f"b/{file_path}",
                )
            )
            patches.append(
                FilePatch(
                    path=file_path,
                    original=original,
                    patched=patched,
                    diff=diff,
                    rule_ids=rule_ids,
                )
            )

    return patches


def _record_violations(
    graph: ContentGraph,
    violations: list[ViolationDict],
    *,
    pass_number: int,
    phase: str,
    dirty_node_ids: frozenset[str] | None = None,
) -> None:
    """Record a NodeState snapshot for nodes with violations.

    When ``dirty_node_ids`` is provided, also records a clean snapshot
    (empty violations) for dirty nodes that are *absent* from
    ``violations``.  This distinguishes "transformed but not yet
    verified" from "rescanned and confirmed clean."

    Args:
        graph: ContentGraph with nodes to update.
        violations: Violation dicts (each must have ``path`` set to a node ID).
        pass_number: Convergence pass number.
        phase: Pipeline phase (``"scanned"``, ``"transformed"``).
        dirty_node_ids: When set, dirty nodes absent from violations
            get a clean ``(phase, violations=())`` entry.
    """
    by_node: dict[str, list[str]] = defaultdict(list)
    for v in violations:
        node_id = str(v.get("path", ""))
        rule_id = str(v.get("rule_id", ""))
        if node_id and rule_id:
            by_node[node_id].append(rule_id)

    for node_id, rule_ids in by_node.items():
        node = graph.get_node(node_id)
        if node is not None:
            node.record_state(pass_number, phase, violations=tuple(sorted(set(rule_ids))))

    if dirty_node_ids is not None:
        for nid in dirty_node_ids - set(by_node):
            node = graph.get_node(nid)
            if node is not None:
                node.record_state(pass_number, phase, violations=())


def _count_modified_nodes(graph: ContentGraph) -> int:
    """Count nodes with at least two progression entries and a content change.

    Args:
        graph: ContentGraph after convergence.

    Returns:
        Number of modified nodes.
    """
    count = 0
    for node in graph.nodes():
        if len(node.progression) >= 2 and node.progression[0].content_hash != node.progression[-1].content_hash:
            count += 1
    return count
