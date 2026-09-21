# ADR-065: SPA vs Gateway Live-Operation State Ownership

## Status

Accepted

## Date

2026-07-20

## Context

ADR-052 moved project operations from a browser WebSocket lifeline to a
Gateway-authoritative `OperationRegistry` with REST actions and SSE fan-out.
ADR-062 and ADR-064 added assess-pause, interactive gates, and ephemeral
proposal working sets. The SPA now implements a multi-step Scan → Assess →
Quick-fix → AI → Commit tracker and an explicit Resume / Start over attach
policy.

Two gaps appeared in the written record:

1. **ADR-052 overstated SPA emptiness.** It said the frontend becomes a “pure
   state renderer” with “no local state machine.” In practice the SPA derives a
   fine-grained workflow latch and holds attach/form UI state that is not (and
   should not be) in the registry.
2. **No ownership table** for what lives in Gateway memory vs PostgreSQL vs the
   browser — so portals and future clients cannot tell what they must
   reimplement vs what they can ignore.

Forces in tension:

- Live ops must survive tab close / multi-viewer reconnect (ADR-052).
- Portal and CLI consumers share the Gateway API, not React modules.
- Fine-grained UX steps (Quick-fix applied vs AI assessment) are denser than
  the coarse `OperationStatus` enum.
- Registry restart loses in-flight ops by design; Activity history is not a
  resume protocol.
- Auto-attaching every project open to a live op fights deliberate Resume /
  Start over UX.

Constraints:

- ADR-060: additive `/api/v1` only for contract changes.
- ADR-029 / invariant 5: durable persistence at the Gateway edge (PostgreSQL), not
  in the engine.
- ADR-052: in-memory registry remains the live progress fan-out.
- Do not invent a second authoritative phase machine in the SPA that can
  disagree with `OperationState.status`.

## Decision

**We will keep the Gateway `OperationRegistry` as the sole authoritative store
for in-flight operation lifecycle and payloads, treat PostgreSQL/Activity as
durable completed-run truth, and allow the SPA only non-authoritative
presentation state (derived workflow tracking, explicit attach, local form
options). This ADR amends ADR-052’s “no local state machine” wording: no
*authoritative* local state machine — derived UI tracking is permitted.**

### Ownership table

| Store | Owns | Lifetime | Resume / multi-client |
|-------|------|----------|------------------------|
| Gateway `OperationRegistry` | Coarse `status`, `findings`, live `proposals`, `result`, progress log, SSE | Minutes; TTL after terminal | Any client via `GET …/operation` + SSE |
| PostgreSQL (scans, violations, analytics) | Completed activity, `review_status`, proposal analytics; historical rebuild | Durable | Read-only history; not live attach |
| SPA (browser) | Attach flag, Scan options, `WorkflowLatch` / stepper derivation, panel filters | Tab / component lifetime | Rebuilt from registry snapshot; latch resets on new `operation_id` |

### Gateway owns (authoritative)

- Operation lifecycle enum (`queued` … `assessed` … `awaiting_approval` …
  `applying` … terminal).
- Live payloads needed to render gates: findings, proposals, result, `pr_url`,
  error.
- REST mutations that advance the op (`begin-remediate`, approve, PATCH
  proposals, cancel, create-PR).
- One non-terminal op per project (409 on conflict).

### SPA owns (non-authoritative)

- **Attach policy:** do not auto-bind `OperationPanel` merely because a live op
  exists. Attach when the user **Resumes** (e.g. `?resume=1`) or **starts** a
  Scan/Remediate in this session; dismiss clears attach. Activity /
  project History show Available vs Read-only for the latest row when
  `scan_id` matches a live op.
- **Derived workflow tracker:** `WorkflowLatch` + `resolveCurrentWorkflowStep`
  map `OperationState` onto Scan → findings → Quick-fix → AI → Commit →
  Complete. Latch is display smoothing only; it must not invent phases the
  API cannot express.
- **Local form chrome:** ansible version, collections, enable AI, auto-apply
  Quick-fix, filter toggles — until submitted as operate options.

### SPA must not own

- Authoritative phase that can disagree with Gateway `status`.
- `sessionStorage` / localStorage as the source of truth for live ops
  (rejected in ADR-052).
- Durable review end-state (that is `violations.review_status` / analytics per
  ADR-062).

### Why this split is necessary

1. **Reconnect and portals.** Only Gateway-held state is visible to a second
   tab, Backstage, or API client. React latch/attach cannot be shared.
2. **Coarse vs dense UX.** Status enum is the product contract; the stepper is
   a presentation projection that may add labels without expanding the API.
3. **Ephemerality.** Registry loss on Gateway restart matches Engine stream
   death; durable Activity is the audit trail, not the resume protocol
   (rebuild-from-drafts remains out of scope unless a future ADR says
   otherwise).
4. **Deliberate attach.** Auto-resume on every project open conflates
   “history exists” with “user is in this run,” especially after assess-pause
   and multi-hour review windows.

## Alternatives Considered

### Alternative 1: Persist fine-grained workflow phase in the Gateway

**Description**: Add `workflow_step` (or similar) to `OperationState` and
persist latch milestones server-side.

**Pros**:
- Identical stepper for every client without re-derivation.
- Survives full page reload of latch-only gaps.

**Cons**:
- Couples UX copy/step density to API versioning (ADR-060 pressure).
- Portals that do not show the SPA stepper still pay schema cost.
- Duplicates information already implied by `status` + proposals/findings.

**Why not chosen**: Presentation projection belongs in clients; coarse status
stays the shared contract.

### Alternative 2: SPA authoritative state machine (sessionStorage)

**Description**: Revert toward browser-owned phase + resume blob (pre-ADR-052).

**Pros**:
- Rich offline UX; no registry memory.

**Cons**:
- Breaks multi-viewer and portal; lost on navigation (ADR-052 context).
- Approval path tied to one browser again.

**Why not chosen**: Explicitly rejected by ADR-052; still wrong for portals.

### Alternative 3: Only PostgreSQL as live-op store

**Description**: Write every phase transition and proposal blob to the DB.

**Pros**:
- Survive Gateway restart with richer recovery.

**Cons**:
- Ops are minutes-scale; schema and flush complexity for transient state.
- Engine stream still dies on restart — false sense of recoverability
  (ADR-052 “why in-memory”).

**Why not chosen**: Keep registry ephemeral; durable store for completed
runs and review analytics (ADR-062).

## Consequences

### Positive

- Clear rule for agents and portal authors: implement against Gateway status +
  payloads; treat SPA stepper/attach as reference UX, not shared library.
- Amends ADR-052 without abandoning Gateway authority.
- Documents Available / Read-only + Resume / Start over as attach policy, not
  a second lifecycle.

### Negative

- Fine-grained stepper can briefly disagree across clients if derivation
  rules diverge (acceptable; latch is non-authoritative).
- Attach-off means a live op is invisible on Overview until Resume — by
  design; users must use Activity/History.

### Neutral

- Pure workflow helpers under `frontend/src/remediation/` remain SPA-local;
  extraction to a shared UI package is delivered as `@apme/ui-workflow`
  (ADR-066 GitHub Release tarballs); this ADR still owns Gateway vs SPA
  state, not the npm publish path.
- Does not change engine FixSession semantics (ADR-039 / ADR-064).

## Implementation Notes

- Keep deriving steps from `OperationState` in
  `frontend/src/remediation/workflowSteps.ts`; reset latch when
  `operation_id` changes.
- `useProjectOperationState(..., { enabled })` — poll/SSE only when attached
  or explicitly resumed.
- Latest Activity / project History row: Available only when live registry
  `scan_id` matches and status is non-terminal live.
- Do not add `workflow_step` to REST without a new ADR and ADR-060 review.
- Portal/CLI: drive `status` + REST actions; ignore SPA latch.

**Invariant consistency:** Does not modify AGENTS.md invariants. Aligns with
invariant 5 (persistence at Gateway edge for durable data) and ADR-052
(in-memory live ops). Amends ADR-052 wording only.

## Related Decisions

- [ADR-029](ADR-029-web-gateway-architecture.md): Persistence at the edge
- [ADR-030](ADR-030-frontend-deployment-model.md): SPA as reference presentation
- [ADR-037](ADR-037-project-centric-ui-model.md): Project-centric UI
- [ADR-052](ADR-052-project-operation-sse-architecture.md): Operation SSE /
  registry (amended by this ADR)
- [ADR-060](ADR-060-rest-api-versioning-contract.md): Additive REST
- [ADR-062](ADR-062-ephemeral-proposal-working-set.md): Ephemeral proposals vs
  durable review
- [ADR-064](ADR-064-assess-pause-session-continue.md): Assess-pause Scan →
  Remediate

## References

- Conversation on Activity Resume / no auto-resume and workflow latch storage
  (2026-07)
- [.sdlc/research/ui-capabilities-assessment.md](../research/ui-capabilities-assessment.md):
  UI as reference client of Gateway REST

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-07-20 | Brad Thornton | Initial proposal — SPA vs Gateway live-state ownership |
| 2026-07-20 | Brad Thornton | Accepted |
