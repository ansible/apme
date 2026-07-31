# ADR-064: Assess-Pause and Session-Continue Scan → Remediate

## Status

Implemented

## Date

2026-07-18

## Context

The SPA historically exposed parallel **Check** and **Remediate** starts. Users
want a unified workflow: **Scan** (assess) → review violations (flat or by
node) → optional **Remediate** without a full re-clone/re-scan when the
session is still live.

ADR-062 Option C already pauses for Gate 1/2 approvals inside one
`FixSession` while retaining `content_graph`. What was missing is a pause
*before* Gate 1 apply review, after violations (and would-fix Tier 1) are
known.

Constraints:

- ADR-060: no breaking REST changes; additive only.
- Portal / CLI must keep today’s check → COMPLETE and remediate defaults.
- Must not reintroduce `violation_ids` Tier 1 pre-filter (selective apply
  remains approve-time on engine proposal ids).

## Decision

**We will add an opt-in assess-pause on `FixSession` that emits findings and
holds the session until `BeginRemediate`, then continues into existing
interactive Gate 1/2 — without a full rescan while the session is alive.**

### Engine (proto additive)

- `FixOptions.assess_pause` (bool, default **false**). When true, Primary:
  - Runs the graph remediate path with Tier 1 **computed** but not spliced
    (same as `interactive` for apply deferral).
  - Emits `SessionEvent.findings` (`FindingsReady`) with all content
    violations (including would-fix metadata where present).
  - Does **not** emit `ProposalsReady` yet; session stays non-terminal.
  - On `SessionCommand.begin_remediate`, emits Gate 1 `t1-*` (if any) or
    proceeds to AI / COMPLETE as today.
- Without `assess_pause`, behavior is unchanged.

### Gateway (REST additive)

- `options.assess_pause` on `POST .../operation` (works with `action: check`
  or `remediate`). When set on check, Gateway attaches `FixOptions` with
  `assess_pause=true` (caller still controls `interactive`) so the engine
  can pause.
- New `POST .../operation/begin-remediate` → gRPC `BeginRemediate`.
- New SSE status `assessed` + `findings` event (additive).
- `interactive` remains default **false** server-side.
- Session expired → `409` with `code: session_expired`; client may start a
  fresh remediate.

### SPA

- **Scan** label wires check + `assess_pause: true`.
- Assess panel: violations flat | group-by-node; path-less singletons;
  deps remain on Dependencies.
- **Remediate** calls begin-remediate on the same operation when assessed;
  falls back to full remediate start if expired.
- Workflow stepper steps (Scan → findings → Quick-fix → AI → Commit) are
  **derived** from Gateway `OperationState` in the SPA; they are not a second
  authoritative phase store. Attach / Resume policy is SPA-local — see
  [ADR-065](ADR-065-spa-gateway-live-state-ownership.md).

## Alternatives Considered

### Alternative 1: Two operations (check COMPLETE, then remediate)

**Description**: Keep separate FixSessions; remediate always rescans.

**Pros**: No proto change.

**Cons**: Doubles clone/scan cost; graph state discarded.

**Why not chosen**: User explicitly wants continue-without-rescan.

### Alternative 2: Assess-pause default on for all checks

**Description**: Every check pauses for begin-remediate.

**Pros**: Uniform UX.

**Cons**: Breaks CLI/CI and portal expecting COMPLETE after check.

**Why not chosen**: Violates non-breaking / ADR-060 spirit.

## Consequences

### Positive

- Unified Scan → Remediate UX without double pipeline cost.
- Portal/CLI unchanged until they opt in.
- Reuses Gate 1/2 and session graph retention.

### Negative

- Longer-lived sessions while users read findings (TTL / Extend).
- Gateway operation state machine gains `assessed`.

### Neutral

- AI escalation triage (Include/Skip before Gate 2) is interactive in the
  SPA when `enable_ai`; CLI/`run_project_operation` without
  `escalate_ai_queue` auto-includes all candidate paths — see ADR-062.

## Implementation Notes

- `assess_pause` implies Tier 1 compute without splice (apply deferral) until
  begin-remediate; Gate 1 vs auto-apply still follows the independent
  `interactive` flag (do not force `interactive=true`).
- Findings payload uses existing `Violation` messages plus optional
  would-fix fields already on violations where available.
- Replay on `resume` should re-emit `FindingsReady` when still assessed.

## Related Decisions

- [ADR-028](ADR-028-session-based-fix-workflow.md): FixSession
- [ADR-039](ADR-039-unified-operation-stream.md): Check / remediate via FixSession
- [ADR-052](ADR-052-project-operation-sse-architecture.md): Operation SSE
- [ADR-060](ADR-060-rest-api-versioning-contract.md): Additive REST
- [ADR-062](ADR-062-ephemeral-proposal-working-set.md): Interactive gates / path
- [ADR-065](ADR-065-spa-gateway-live-state-ownership.md): SPA vs Gateway
  live-state ownership

## References

- Unified Scan Remediate UX plan (2026-07)

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-07-18 | Brad Thornton | Initial acceptance — assess-pause + begin-remediate |
| 2026-07-20 | Brad Thornton | SPA stepper/attach ownership clarified; link ADR-065 |
| 2026-07-20 | Brad Thornton | Clarify assess_pause does not force interactive=true |
| 2026-07-21 | Brad Thornton | Note AI escalation triage is no longer deferred |
