# AI as a Graph Transform

**Status**: Planned
**Date**: 2026-04-02
**Related**: [ADR-025](/.sdlc/adrs/ADR-025-ai-provider-protocol.md) | [ADR-027](/.sdlc/adrs/ADR-027-agentic-project-remediation.md) | [ADR-028](/.sdlc/adrs/ADR-028-session-based-fix-workflow.md) | [ADR-044](/.sdlc/adrs/ADR-044-node-identity-progression-model.md) | [NodeState Design](/.sdlc/research/nodestate-progression-design.md) | [Migration Research](/.sdlc/research/ari-to-contentgraph-migration.md)

## Problem

The legacy `RemediationEngine` path runs AI remediation (Tier 2) as a separate
post-convergence phase after Tier 1 graph convergence.  It operates on files,
uses `UnitSegmenter` to chunk tasks by line range, sends snippets to the LLM,
and returns patches as diffs.  That path is disconnected from the
`GraphRemediationEngine` convergence loop:

```
Historical flow (pre-graph AI gate):
  GraphRemediationEngine.remediate()    # Tier 1 only
    └─ converge: scan → transform → rescan → repeat
    └─ splice_modifications()
  ────────────────────────────────────── boundary ──
  RemediationEngine._escalate_tier2()   # separate phase
    └─ partition remaining into Tier 2 / Tier 3
    └─ UnitSegmenter chunks file by line ranges
    └─ AIProvider.propose_unit_fixes() per chunk
    └─ returns AIProposal diffs
```

The active graph path runs Tier 2 AI via
`EngineServicer._session_run_ai_gate()` after Gate 1 approval
(`engine_server.py`, graph remediation flow around `_session_run_graph_ai`),
not through the separate `RemediationEngine` phase above.

The active Engine graph path already invokes Tier 2 through
`EngineServicer._session_run_ai_gate()` after Gate 1 approval (see above),
but CLI remediate paths that still use `RemediationEngine` retain the legacy
split.  This research targets full unification in the graph convergence loop.

### What's wrong with the legacy split

1. **Two convergence models**.  Tier 1 converges in-memory on the graph.
   Tier 2 operates on post-splice files.  If an AI fix introduces a new
   Tier 1 violation, there's no loop to catch it.

2. **No NodeState tracking for AI**.  Tier 1 transforms record progression
   (`NodeState` snapshots at each phase).  AI proposals exist as detached
   `AIProposal` objects with no graph linkage — the UI can't show how a
   node evolved through both Tier 1 and AI.

3. **Line-range segmentation is fragile**.  `UnitSegmenter` chunks files
   by line ranges, which drift after Tier 1 transforms have modified the
   file.  The graph already knows exact node boundaries.

4. **Duplicate context assembly**.  The LLM prompt includes task YAML,
   violations, and surrounding context.  The graph already holds all of
   this — violations on `NodeState.violation_dicts`, YAML on
   `ContentNode.yaml_lines`, hierarchy via parent/child edges.

## Target Architecture

AI is not a separate phase — it is another transform source in the unified
convergence loop.  The only difference between Tier 1 and AI transforms is
metadata (confidence, cost, auto-approvability), not the convergence
machinery.

```
Target flow:
  GraphRemediationEngine.remediate()
    └─ converge:
        ├─ scan
        ├─ Tier 1 transforms (deterministic, auto-approved)
        ├─ Tier 2 transforms (AI, pending approval)
        │    └─ AIProvider receives node context from graph
        │    └─ result goes through apply_transform()
        │    └─ NodeState records source="ai"
        ├─ rescan dirty nodes (catches Tier 1 violations from AI fixes)
        └─ repeat until stable
    └─ splice_modifications()
```

### Key design points

**AI proposals go through `apply_transform()`**.  The graph's existing
transform machinery handles parse → mutate → serialize → re-indent →
`update_from_yaml()` → mark dirty → record `NodeState`.  AI is just
another source of the `CommentedMap` mutation.

**`TransformSession` mediates all transforms** (per ADR-044 invariant 13).
AI transforms use the same `TransformSession` → `submit()` →
`ChangeSet` path.  The session provides read access to the graph (node
content, violations, hierarchy) and write access through `modify_node()`.

**Approval semantics differentiate tiers**.  Tier 1 transforms are
auto-approved (deterministic, safe).  Tier 2 transforms are proposed
with `approved=False` on the `NodeState`.  The client approves/rejects
via the `Approve` message in `FixSession` (ADR-028).  The graph tracks
both states — no separate data structure for proposals.

**Re-scan catches cross-tier interactions**.  If an AI fix introduces a
new L013 (missing `changed_when`), the Tier 1 transform for L013 fires
on the next convergence pass.  Today this interaction is invisible.

**Node context replaces file-chunk context**.  Instead of
`UnitSegmenter` slicing files by line range, the LLM receives:

- `node.yaml_lines` — exact YAML for the task
- `node.state.violation_dicts` — violations on this node
- Parent node context (play vars, become settings) via graph edges
- Sibling context (surrounding tasks) via graph traversal

This is richer and more precise than a line-range file chunk.

## Implementation Sketch

### New: `ai_as_transform()`

```python
async def ai_as_transform(
    graph: ContentGraph,
    node_id: str,
    ai_provider: AIProvider,
    violations: list[ViolationDict],
) -> bool:
    """Apply AI-proposed fix as a graph transform.

    Returns True if the AI produced a valid change.
    """
    node = graph.get_node(node_id)
    if node is None:
        return False

    context = build_ai_context(graph, node_id, violations)
    proposal = await ai_provider.propose_node_fix(context)

    if proposal is None or proposal.fixed_snippet == node.yaml_lines:
        return False

    cm = yaml.load(proposal.fixed_snippet)
    # Validate: must be well-formed YAML, same top-level structure
    graph.apply_transform(node_id, lambda task, _v: _apply_ai_cm(task, cm), violations[0])
    return True
```

### New: `AIProvider.propose_node_fix()`

The existing `AIProvider` protocol gains a node-oriented method alongside
the current file-oriented `propose_unit_fixes()`:

```python
@runtime_checkable
class AIProvider(Protocol):
    async def propose_node_fix(
        self, context: AINodeContext,
    ) -> AIProposal | None: ...
```

`AINodeContext` bundles the graph-derived context (YAML, violations,
parent chain, sibling snippets) into a single prompt-ready object.

### Changes to `GraphRemediationEngine.remediate()`

After Tier 1 transforms exhaust, before declaring convergence:

1. Collect unfixed violations classified as `ai_candidate`
2. Group by node_id
3. For each node: call `ai_as_transform()`
4. If any applied: mark dirty, rescan, continue convergence loop

### Migration path

The legacy `RemediationEngine._escalate_tier2()` continues to work for
the non-graph path (if any clients still use it).  The graph path is
the default for `FixSession`.  Once the graph path supports AI, the
legacy escalation code becomes dead and can be removed.

## Open Questions

1. **Filename in transform context**.  Some transforms may need the
   filename (e.g. L084 subtask prefix — see #222).  The current
   transform signature is `(task: CommentedMap, violation: ViolationDict)`.
   Should we extend to `(task, violation, context)` where context
   includes `file_path`, `node_id`, parent info?

2. **Batch vs per-node LLM calls**.  The current Tier 2 batches all
   violations for a file into one LLM call.  Per-node calls are simpler
   but more expensive.  Could batch by grouping nodes from the same file
   and sending a combined prompt, then splitting the response.

3. **Approval UX in convergence**.  If AI fixes trigger new Tier 1
   fixes in the same convergence loop, should the Tier 1 fixes also
   require approval?  Current design: Tier 1 is always auto-approved
   regardless of what triggered it.  This seems right but worth
   validating.

4. **Tier 3 (agentic)**.  ADR-027 defines Tier 3 as project-level
   agentic remediation.  That operates at a higher level than per-node
   transforms.  It likely stays outside the convergence loop as a
   post-convergence phase.  Not in scope here but worth noting the
   boundary.

## Dependencies

- `ContentGraph.apply_transform()` — exists (PR 7, #223)
- `NodeState.violation_dicts` — exists (PR 7, #223)
- `TransformSession` — exists (ADR-044, described in migration research)
- `AIProvider` protocol — exists (ADR-025, `ai_provider.py`)
- `AbbenayProvider` — exists (`abbenay_provider.py`)
- `propose_node_fix()` on `AIProvider` — **new** (to be added)
- `AINodeContext` — **new** (graph-derived prompt context)
- `GraphRemediationEngine` AI loop — **new** (to be added)

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-04-02 | Bradley A. Thornton | Initial research doc |
