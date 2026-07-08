"""Graph-aware remediation engine — unified Tier 1 + Tier 2 convergence.

The ``ContentGraph`` acts as a mutable working copy: transforms modify
``ContentNode.yaml_lines`` in memory, dirty nodes are rescanned with
only graph rules (no full pipeline rebuild), and files on disk are
never touched until final approval via ``splice_modifications()``.

Tier 1 (deterministic) and Tier 2 (AI) transforms participate in the
same convergence loop.  After AI applies, rescanning catches cross-tier
violations and Tier 1 cleanup runs automatically.

See :doc:`/sdlc/research/ai-as-graph-transform` for the design rationale.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from apme_engine.engine.models import ViolationDict
from apme_engine.graph.content_graph import ContentGraph
from apme_engine.graph.rule_base import GraphRule
from apme_engine.graph.scanner import (
    graph_report_to_violations,
    rescan_dirty,
    scan,
)
from apme_engine.remediation.partition import normalize_rule_id, partition_violations
from apme_engine.remediation.registry import TransformRegistry

if TYPE_CHECKING:
    from apme_engine.remediation.ai_provider import AIProvider

logger = logging.getLogger("apme.remediation.graph")

ProgressCallback = Callable[[str, str, float, int], None]
RescanFn = Callable[[ContentGraph, frozenset[str]], Awaitable[list["ViolationDict"]]]


@dataclass
class FilePatch:
    """A single file patch with diff and applied rule IDs.

    Attributes:
        path: File path that was patched.
        original: Original file content before patching.
        patched: Content after applying transforms.
        diff: Unified diff string (original -> patched).
        rule_ids: List of rule IDs applied to this file.
    """

    path: str
    original: str
    patched: str
    diff: str
    rule_ids: list[str] = field(default_factory=list)


@dataclass
class GraphFixReport:
    """Summary of a graph-aware remediation run.

    ``applied_patches`` is **not** populated by ``remediate()`` itself.
    Callers produce patches by passing the post-convergence graph to
    :func:`splice_modifications`, then store the result here.

    All violation counts are sourced from the ``ContentGraph`` violation
    ledger — ``fixed`` and ``fixed_violations`` come from
    ``query_violations(status="fixed")``, ``remaining_violations`` from
    ``query_violations(status="open")`` plus ``ai_abstained``.  No
    snapshot diffs.

    Attributes:
        passes: Number of convergence passes executed.
        fixed: Count of violations resolved during convergence.
        applied_patches: File patches produced by ``splice_modifications``
            (populated by the caller, not by ``remediate``).
        remaining_violations: Violations not resolved (open + ai_abstained).
        fixed_violations: Violations resolved during convergence.
        ai_abstained_violations: Subset of remaining where AI attempted
            but could not produce a fix.
        oscillation_detected: True if the loop bailed due to oscillation.
        nodes_modified: Number of ContentNodes modified.
        step_diffs: Per-progression-step content diffs.
        ai_proposals: AI-proposed node fixes pending human approval.
    """

    passes: int = 0
    fixed: int = 0
    applied_patches: list[FilePatch] = field(default_factory=list)
    remaining_violations: list[ViolationDict] = field(default_factory=list)
    fixed_violations: list[ViolationDict] = field(default_factory=list)
    ai_abstained_violations: list[ViolationDict] = field(default_factory=list)
    oscillation_detected: bool = False
    nodes_modified: int = 0
    step_diffs: list[dict[str, object]] = field(default_factory=list)
    ai_proposals: list[AINodeProposal] = field(default_factory=list)


@dataclass
class AINodeProposal:
    """AI-proposed fix for a single graph node, pending human approval.

    Attributes:
        node_id: Graph node that was modified by AI.
        file_path: Source file path (for display).
        before_yaml: Node's YAML before the AI transform.
        after_yaml: Node's YAML after the AI transform.
        rule_ids: Rule IDs addressed by this fix.
        explanation: Human-readable summary.
        confidence: AI confidence score.
        line_start: Starting line in the original source file (0 if unknown).
        line_end: Ending line in the original source file (0 if unknown).
    """

    node_id: str
    file_path: str
    before_yaml: str
    after_yaml: str
    rule_ids: list[str] = field(default_factory=list)
    explanation: str = ""
    confidence: float = 0.85
    line_start: int = 0
    line_end: int = 0


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
        max_ai_attempts: int = 2,
        max_ai_concurrency: int = 4,
        progress_callback: ProgressCallback | None = None,
        rescan_fn: RescanFn | None = None,
        ai_provider: AIProvider | None = None,
    ) -> None:
        """Initialize the graph remediation engine.

        Args:
            registry: Transform registry mapping rule IDs to fix functions.
            graph: ContentGraph to remediate (mutated in place).
            rules: Pre-loaded GraphRule instances for re-scanning.
            max_passes: Maximum convergence passes (default 5).
            max_ai_attempts: Maximum AI resubmission rounds (default 2).
            max_ai_concurrency: Maximum concurrent AI proposal calls
                (default 4).  Bounds the number of simultaneous LLM
                round-trips per AI pass.
            progress_callback: Optional ``(phase, message, fraction, level)``
                callback for streaming progress.
            rescan_fn: Optional callback that replaces the built-in
                ``rescan_dirty`` call during convergence.  Receives
                ``(graph, dirty_node_ids)`` and returns violations.
                When set, this enables the validator bridge — the
                caller can fan out to real gRPC validators instead of
                only in-memory graph rules.
            ai_provider: Optional AI provider for Tier 2 transforms.
        """
        self._registry = registry
        self._graph = graph
        self._rules = rules
        self._max_passes = max_passes
        self._max_ai_attempts = max_ai_attempts
        self._max_ai_concurrency = max(1, max_ai_concurrency)
        self._progress_cb = progress_callback
        self._rescan_fn = rescan_fn
        self._ai_provider = ai_provider

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
        """Run the unified Tier 1 + Tier 2 convergence loop.

        Tier 1 deterministic transforms run first.  When Tier 1
        exhausts and an ``ai_provider`` is set, Tier 2 AI transforms
        fire on remaining AI-candidate violations.  Post-AI rescanning
        catches cross-tier interactions (e.g., an AI fix introducing a
        new L013), and Tier 1 cleanup runs automatically.  The AI
        resubmission loop is capped by ``max_ai_attempts``.

        After convergence, deterministic transforms are auto-approved.
        AI transforms remain pending (``approved=False``) for human
        review via the ``FixSession`` approval flow.

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
        ai_proposals: list[AINodeProposal] = []
        oscillation = False
        ai_attempts = 0
        ai_feedback_by_node: dict[str, str] = {}

        for pass_num in range(1, self._max_passes + 1):
            passes = pass_num
            tier1_stalled = False
            self._progress("graph-tier1", f"Pass {pass_num}/{self._max_passes}")

            tier1, tier2, tier3 = partition_violations(violations, registry)
            logger.debug(
                "Graph remediation pass %d: %d violations -> tier1=%d tier2=%d tier3=%d (ai_provider=%s)",
                pass_num,
                len(violations),
                len(tier1),
                len(tier2),
                len(tier3),
                self._ai_provider is not None,
            )

            # Phase A: Tier 1 deterministic transforms
            if tier1:
                applied_this_pass = await self._apply_tier1(
                    graph,
                    registry,
                    tier1,
                    pass_num,
                )
                logger.debug(
                    "Graph remediation pass %d: tier1 applied=%d/%d",
                    pass_num,
                    applied_this_pass,
                    len(tier1),
                )

                if applied_this_pass == 0:
                    tier1_stalled = True
                    tier2.extend(tier1)
                    logger.debug(
                        "Graph remediation pass %d: tier1 stalled (0 applied); promoted %d to tier2 (total tier2=%d)",
                        pass_num,
                        len(tier1),
                        len(tier2),
                    )
                else:
                    await self._rescan_and_record(
                        graph,
                        pass_num,
                        resolve_fixed_by="deterministic",
                    )
                    violations = graph.collect_violations()
                    new_tier1, new_tier2, _ = partition_violations(violations, registry)
                    new_fixable = len(new_tier1)

                    if new_fixable >= prev_count:
                        logger.warning(
                            "Graph remediation: oscillation at pass %d (%d >= %d)",
                            pass_num,
                            new_fixable,
                            prev_count,
                        )
                        oscillation = True
                        break

                    prev_count = new_fixable
                    if new_fixable > 0:
                        continue

                    tier1, tier2 = new_tier1, new_tier2

            # Phase B: Tier 2 AI transforms (also when tier1 stalled)
            if (
                (not tier1 or tier1_stalled)
                and tier2
                and self._ai_provider is not None
                and ai_attempts < self._max_ai_attempts
            ):
                ai_attempts += 1
                self._progress(
                    "graph-ai",
                    f"AI attempt {ai_attempts}/{self._max_ai_attempts}: {len(tier2)} candidates",
                )

                new_ai_proposals = await self._apply_ai_transforms(
                    graph,
                    tier2,
                    pass_num,
                    ai_feedback_by_node,
                )
                ai_proposals.extend(new_ai_proposals)

                if graph.dirty_nodes:
                    await self._rescan_and_record(
                        graph,
                        pass_num,
                        resolve_fixed_by="ai",
                        resolve_status="proposed",
                    )
                    violations = graph.collect_violations()

                    # Phase C: Post-AI Tier 1 cleanup
                    new_tier1, new_tier2, _ = partition_violations(violations, registry)
                    if new_tier1:
                        self._progress(
                            "graph-tier1",
                            f"Post-AI cleanup: {len(new_tier1)} Tier 1 violations",
                        )
                        await self._apply_tier1(
                            graph,
                            registry,
                            new_tier1,
                            pass_num,
                        )
                        await self._rescan_and_record(
                            graph,
                            pass_num,
                            resolve_fixed_by="deterministic",
                        )
                        violations = graph.collect_violations()
                        _, new_tier2, _ = partition_violations(violations, registry)

                    if new_tier2:
                        ai_feedback_by_node = _build_ai_feedback(new_tier2)
                        continue

            if not tier1 and not tier1_stalled and not tier2:
                self._progress(
                    "graph-tier1",
                    f"Fully converged at pass {pass_num}",
                )
                logger.info("Graph remediation: fully converged at pass %d", pass_num)
                break

            if (not tier1 or tier1_stalled) and (self._ai_provider is None or ai_attempts >= self._max_ai_attempts):
                logger.info(
                    "Graph remediation: Tier 1 exhausted, %d remaining AI candidates (ai_attempts=%d/%d)",
                    len(tier2),
                    ai_attempts,
                    self._max_ai_attempts,
                )
                break

        graph.approve_pending(source_filter="deterministic")

        remaining = graph.query_violations(status="open")
        fixed_violations = graph.query_violations(status="fixed")
        ai_abstained = graph.query_violations(status="ai_abstained")
        step_diffs = graph.collect_step_diffs()

        return GraphFixReport(
            passes=passes,
            fixed=len(fixed_violations),
            remaining_violations=remaining + ai_abstained,
            fixed_violations=fixed_violations,
            ai_abstained_violations=ai_abstained,
            oscillation_detected=oscillation,
            nodes_modified=_count_modified_nodes(graph),
            step_diffs=step_diffs,
            ai_proposals=ai_proposals,
        )

    async def _apply_tier1(
        self,
        graph: ContentGraph,
        registry: TransformRegistry,
        tier1: list[ViolationDict],
        pass_num: int,
    ) -> int:
        """Apply Tier 1 deterministic transforms for one pass.

        Args:
            graph: ContentGraph to transform.
            registry: Transform registry.
            tier1: Tier 1 fixable violations.
            pass_num: Current convergence pass number.

        Returns:
            Number of transforms applied this pass.
        """
        self._progress(
            "graph-tier1",
            f"Pass {pass_num}: {len(tier1)} fixable violations",
        )

        applied_this_pass = 0
        skipped_no_transform = 0
        skipped_no_apply = 0
        for v in tier1:
            rule_id = normalize_rule_id(str(v.get("rule_id", "")))
            node_id = str(v.get("path", ""))

            transform_fn = registry.get_node_transform(rule_id)
            if transform_fn is None:
                skipped_no_transform += 1
                continue

            applied = await graph.apply_transform(node_id, transform_fn, v)
            if applied:
                applied_this_pass += 1
            else:
                skipped_no_apply += 1
                logger.debug("Tier1 transform %s on %r: not applied", rule_id, node_id)
        logger.debug(
            "Tier1 pass %d summary: applied=%d skipped_no_transform=%d skipped_no_apply=%d",
            pass_num,
            applied_this_pass,
            skipped_no_transform,
            skipped_no_apply,
        )

        for nid in graph.dirty_nodes:
            node = graph.get_node(nid)
            if node is not None:
                node.record_state(pass_num, "transformed", source="deterministic")

        self._progress(
            "graph-tier1",
            f"Pass {pass_num}: {applied_this_pass} transforms applied",
        )

        return applied_this_pass

    async def _apply_ai_transforms(
        self,
        graph: ContentGraph,
        tier2: list[ViolationDict],
        pass_num: int,
        feedback_by_node: dict[str, str],
    ) -> list[AINodeProposal]:
        """Apply AI transforms for Tier 2 violations.

        Proposals are fetched concurrently (bounded by
        ``max_ai_concurrency``), then applied serially because
        ``ContentGraph`` is not thread-safe.

        Args:
            graph: ContentGraph to transform.
            tier2: AI-candidate violations.
            pass_num: Current convergence pass number.
            feedback_by_node: Per-node feedback from prior AI attempts.

        Returns:
            List of AI proposals applied.
        """
        from apme_engine.remediation.ai_context import build_ai_node_context
        from apme_engine.remediation.ai_provider import AINodeFix

        by_node: dict[str, list[ViolationDict]] = defaultdict(list)
        for v in tier2:
            node_id = str(v.get("path", ""))
            if node_id:
                by_node[node_id].append(v)

        logger.debug(
            "AI transforms: %d tier2 violations grouped into %d nodes",
            len(tier2),
            len(by_node),
        )

        assert self._ai_provider is not None  # noqa: S101
        sem = asyncio.Semaphore(self._max_ai_concurrency)

        async def _propose_one(
            node_id: str,
            node_violations: list[ViolationDict],
        ) -> tuple[str, AINodeFix, str] | None:
            """Fetch a single AI proposal under the concurrency semaphore.

            Args:
                node_id: Graph node identifier.
                node_violations: Violations for this node.

            Returns:
                ``(node_id, fix, before_yaml)`` on success, ``None`` if
                the node was skipped or the AI returned no change.
            """
            async with sem:
                node = graph.get_node(node_id)
                if node is None:
                    logger.warning("AI: node %r not found in graph — skipping", node_id)
                    return None
                before_yaml = node.yaml_lines

                feedback = feedback_by_node.get(node_id, "")
                context = build_ai_node_context(
                    graph,
                    node_id,
                    node_violations,
                    feedback=feedback,
                )
                if context is None:
                    logger.warning("AI: context is None for node %s — skipping", node_id)
                    return None

                logger.debug(
                    "AI: calling propose_node_fix for %s (%d violations)",
                    node_id,
                    len(node_violations),
                )
                fix = await self._ai_provider.propose_node_fix(context)  # type: ignore[union-attr]
                logger.debug(
                    "AI: propose_node_fix returned for %s: fix=%s",
                    node_id,
                    fix is not None,
                )

                if fix is None or not fix.fixed_snippet:
                    logger.debug("AI returned no usable fix for node %s", node_id)
                    return None

                if fix.fixed_snippet.strip() == before_yaml.strip():
                    logger.debug(
                        "AI returned identical content for node %s (no change)",
                        node_id,
                    )
                    return None

                return (node_id, fix, before_yaml)

        # Fan out proposals concurrently
        results = await asyncio.gather(
            *[_propose_one(nid, nvs) for nid, nvs in by_node.items()],
            return_exceptions=True,
        )

        from apme_engine.remediation.partition import normalize_rule_id  # noqa: PLC0415

        # Apply accepted fixes serially (graph mutations are not thread-safe)
        proposals: list[AINodeProposal] = []
        proposed_node_ids: set[str] = set()
        failed_node_ids: set[str] = set()
        node_id_list = list(by_node.keys())
        for idx, r in enumerate(results):
            if isinstance(r, BaseException):
                failed_node_ids.add(node_id_list[idx])
                logger.error(
                    "AI proposal failed for %s",
                    node_id_list[idx],
                    exc_info=(type(r), r, r.__traceback__),
                )
                continue
            if r is None:
                continue
            node_id, fix, before_yaml = r

            node = graph.get_node(node_id)
            if node is None:
                continue

            applied = graph.apply_yaml(node_id, fix.fixed_snippet)
            if not applied:
                logger.debug("AI apply_yaml was a no-op for node %s", node_id)
                continue
            proposed_node_ids.add(node_id)
            node.record_state(pass_num, "transformed", source="ai")

            proposals.append(
                AINodeProposal(
                    node_id=node_id,
                    file_path=node.file_path,
                    before_yaml=before_yaml,
                    after_yaml=node.yaml_lines,
                    rule_ids=fix.rule_ids,
                    explanation=fix.explanation,
                    confidence=fix.confidence,
                    line_start=node.line_start,
                    line_end=node.line_end,
                ),
            )

            if fix.skipped:
                skipped_reasons = {normalize_rule_id(s.rule_id): (s.reason, s.suggestion) for s in fix.skipped}
                graph.abstain_violations(
                    node_id,
                    frozenset(skipped_reasons.keys()),
                    reasons=skipped_reasons,
                )

            logger.info(
                "AI transform applied to %s (rules: %s, confidence: %.2f)",
                node_id,
                fix.rule_ids,
                fix.confidence,
            )

        abstained_total = 0
        for nid, nvs in by_node.items():
            if nid in proposed_node_ids or nid in failed_node_ids:
                continue
            rule_ids = frozenset(normalize_rule_id(str(v.get("rule_id", ""))) for v in nvs)
            abstained_total += graph.abstain_violations(nid, rule_ids)

        if abstained_total > 0:
            logger.info("AI abstained: %d violations marked ai_abstained", abstained_total)

        self._progress(
            "graph-ai",
            f"{len(proposals)} AI transforms applied",
        )
        return proposals

    async def _rescan_and_record(
        self,
        graph: ContentGraph,
        pass_num: int,
        resolve_fixed_by: str | None = None,
        resolve_status: str = "fixed",
    ) -> list[ViolationDict]:
        """Rescan dirty nodes, register violations, and optionally resolve.

        Args:
            graph: ContentGraph with dirty nodes to rescan.
            pass_num: Current convergence pass number.
            resolve_fixed_by: When set, transitions violations that
                disappeared on dirty nodes.  Leave ``None`` to skip
                resolution (register only).
            resolve_status: Target status for resolved violations
                (``"fixed"`` for deterministic, ``"proposed"`` for AI).

        Returns:
            Fresh violations from the rescan.
        """
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

        if resolve_fixed_by is not None:
            _resolve_dirty_violations(
                graph,
                new_violations,
                dirty,
                fixed_by=resolve_fixed_by,
                pass_number=pass_num,
                status=resolve_status,
            )

        graph.clear_dirty()
        return new_violations


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

        node_rule_ids = [rec.key[1] for rec in node.violation_ledger.values()]

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
    """Record NodeState snapshots and register violations in the ledger.

    Records a ``NodeState`` (content-only) for each node that has
    violations, then registers all violations into the graph's
    violation ledger.

    When ``dirty_node_ids`` is provided, also records a clean snapshot
    for dirty nodes that have no violations in this batch.

    Args:
        graph: ContentGraph with nodes to update.
        violations: Violation dicts (``path`` should be a graph node ID).
        pass_number: Convergence pass number.
        phase: Pipeline phase (``"scanned"``, ``"transformed"``).
        dirty_node_ids: When set, dirty nodes absent from violations
            get a snapshot entry recorded.
    """
    by_node: dict[str, list[ViolationDict]] = defaultdict(list)
    for v in violations:
        node_id = str(v.get("path", ""))
        if node_id:
            by_node[node_id].append(v)

    for node_id in by_node:
        node = graph.get_node(node_id)
        if node is not None:
            node.record_state(pass_number, phase)

    if dirty_node_ids is not None:
        for nid in dirty_node_ids - set(by_node):
            node = graph.get_node(nid)
            if node is not None:
                node.record_state(pass_number, phase)

    graph.register_violations(violations, pass_number)


def _resolve_dirty_violations(
    graph: ContentGraph,
    rescan_violations: list[ViolationDict],
    dirty_ids: frozenset[str],
    *,
    fixed_by: str,
    pass_number: int,
    status: str = "fixed",
) -> None:
    """Resolve violations on dirty nodes based on rescan results.

    For each dirty node, computes which rule IDs are still present
    and calls ``graph.resolve_violations`` so that absent violations
    transition to *status* with the given attribution.

    Args:
        graph: ContentGraph whose ledger to update.
        rescan_violations: Violations returned by the rescan.
        dirty_ids: Node IDs that were rescanned.
        fixed_by: Attribution (``"deterministic"`` or ``"ai"``).
        pass_number: Current convergence pass number.
        status: Target status (``"fixed"`` or ``"proposed"``).
    """
    remaining_by_node: dict[str, set[str]] = defaultdict(set)
    for v in rescan_violations:
        node_id = str(v.get("path", ""))
        if node_id:
            remaining_by_node[node_id].add(
                normalize_rule_id(str(v.get("rule_id", ""))),
            )

    for nid in dirty_ids:
        remaining = remaining_by_node.get(nid, set())
        graph.resolve_violations(
            nid,
            remaining,
            fixed_by=fixed_by,
            pass_number=pass_number,
            status=status,
        )


def _build_ai_feedback(tier2: list[ViolationDict]) -> dict[str, str]:
    """Build per-node feedback strings from remaining Tier 2 violations.

    When AI resubmission fires, the LLM receives feedback about
    violations that appeared after its previous fix attempt.

    Args:
        tier2: Remaining Tier 2 violations after rescan.

    Returns:
        Dict mapping node_id to a feedback string.
    """
    by_node: dict[str, list[ViolationDict]] = defaultdict(list)
    for v in tier2:
        node_id = str(v.get("path", ""))
        if node_id:
            by_node[node_id].append(v)

    feedback: dict[str, str] = {}
    for node_id, violations in by_node.items():
        lines = ["Your previous fix introduced or did not resolve these violations:"]
        for v in violations:
            rule_id = str(v.get("rule_id", ""))
            message = str(v.get("message", ""))
            lines.append(f"- [{rule_id}]: {message}")
        feedback[node_id] = "\n".join(lines)

    return feedback


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
