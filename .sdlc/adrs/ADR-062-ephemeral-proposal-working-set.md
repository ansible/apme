# ADR-062: Ephemeral Proposal Working Set and Review Analytics

## Status

Accepted

## Date

2026-07-09

## Context

Interactive remediation ([issue #379](https://github.com/ansible/apme/issues/379))
needs a stable **approval unit** for UI checkboxes (per node, not per
violation). Today:

- The engine emits **violations** (per rule) and thin **ProposalOutcome**
  crumbs after a fix session.
- Gateway `proposals` rows are post-hoc AI outcome summaries only.
- Full live proposals live in the in-memory `OperationRegistry` (ADR-052).
- There is no durable human review end-state distinct from engine
  `remediation_resolution`, and no retention policy for proposal blobs.

**Phase 1 scope (this ADR):** post-hoc archival grouping at
`ReportFixCompleted`, durable `violations.review_status`, ephemeral
Gateway `proposals` while a remediate working set is actionable, and
`proposal_rule_analytics`. Live Gate 1/2 checkboxes still bind to engine
`Proposal.id` via `OperationRegistry` / WebSocket approve (ADR-052).
Gateway `proposal_id` (`prop-{gate}-{hash}`) is the archival / activity
identity — Phase 2 defines the id bridge for Option C.

If we keep full proposal diffs forever, SQLite grows without bound. If we
key checkboxes on `violations.id`, we fight node-level `approve_node()`
and coupled multi-rule fixes. If we treat PR/push as “approved,” we lose
rule-efficacy signal for fixes that were accepted but never published.

Constraints and drivers:

- ADR-060: additive `/api/v1` changes only (no field renames/removals).
- Engine stays violation-emitting; Gateway owns product grouping.
- Analytics must support Tier 1 vs AI and pure vs coupled without bias.
- Historical activity must remain reviewable after proposal rows are deleted.

## Decision

**We will treat Gateway `proposals` as an ephemeral actionable working set,
group engine violations into node-grained proposals at ingest, persist human
review on `violations.review_status`, and roll approve/deny into a durable
per-rule analytics table (Tier1/AI × pure/coupled). Historical scans rebuild
proposals in memory from violations and do not re-persist them.**

### Data model

| Store | Grain | Lifetime |
|-------|-------|----------|
| `violations` | per-rule finding + `review_status` | Durable with the scan |
| `proposals` | per-node (group-by-`path`) or singleton | Ephemeral — only while remediate review is actionable |
| `proposal_rule_analytics` | rule × source × coupled (+ optional group key) | Durable aggregates |

### Grouping

- Non-empty `Violation.path` → one proposal per `(scan_id, path, class_lane)`
  where lane is Tier‑1 (auto-fixable/fixed), AI-candidate, or other.
  Mixed rem_class on the same path become **separate** proposals so a
  single decision cannot stamp the wrong class.
- Empty/missing `path` → one proposal per violation (singleton).
- Gateway assigns stable archival `proposal_id` within a scan (activity /
  rebuild). Live interactive approve still uses engine proposal ids
  (ADR-052); Phase 2 bridges the two.

### `review_status` (human/gate decision; not publish)

| Value | Meaning |
|-------|---------|
| `deterministic_approved` | Accepted Tier 1 / deterministic fix (including non-interactive auto-apply) |
| `deterministic_declined` | Rejected Tier 1 fix |
| `ai_approved` | Accepted AI fix |
| `ai_declined` | Rejected AI fix or declined AI-candidate triage |
| `NULL` | Never reviewed |

**Approved ≠ published.** PR/commit/push updates publish metadata (`pr_url`)
only; it does not clear or redefine `review_status`.

Node-level decide → stamp the same `review_status` onto **compatible**
linked violations only. Compatibility is source- and decision-aware:
deterministic → auto-fixable/fixed; AI approve → AI-candidate **or**
post-apply auto-fixable/fixed; AI decline → AI-candidate **or**
post-apply manual-review remaining. Mixed path buckets may leave
unrelated rows as `NULL` so a Tier 1 or AI decision does not corrupt
durable state for the wrong class. Distinct from engine
`remediation_resolution`.

Historical rebuild must **not** invent `approved` from `fixed_yaml`
alone when `review_status` is NULL — that fabricates review. Ingest may
still promote non-interactive Tier 1 (pending + deterministic + fixed)
to approved when persisting the working set, because the engine applied
the fix without a human gate.

### Retention / flush

1. **New remediate scan for a project** → flush analytics from current
   proposals → `DELETE` proposals for that `project_id` → insert proposals
   only for the new actionable remediate. Implemented via
   `link_scan_to_project` when the incoming scan is `remediate` (flush
   *before* attaching so its proposals are not deleted). **Check scans
   do not flush** the project working set; they **discard** any proposals
   and auto-stamps created for that check scan at FixCompleted (engine
   always emits fixed_yaml “would fix” rows).
2. **PR / commit / push completed** → flush + delete for the **published
   scan only** (scan-scoped). Phase 1 wires this into `set_scan_pr_url`;
   push-only publish without a PR URL is Phase 2. Project-wide flush
   remains for remediate→remediate replacement only.
3. **Historical activity GET** → if no working-set rows, **rebuild** proposals
   from violations in memory; do not `INSERT`. Use `original_yaml` /
   `fixed_yaml` for diffs when present.

**Release invariant:** flush and historical rebuild ship together.

### Analytics

- Dimensions include **Tier 1 vs AI** (`source`) and **pure vs coupled**.
- Optional group fingerprint (`L007+L013`) for bundle drill-down.
- Do not use a single flat per-rule declined counter that mixes coupled
  declines (bias). Default efficacy views prefer pure (or split) stats.
- Cross-project queries (e.g. declined AI for rule X) use
  `violations.review_status` after proposals are gone.

### API (ADR-060)

Additive optional fields on activity/proposal/violation responses only.
Do not rename or remove existing `ProposalDetail` fields. Keep status
spellings clients already use (`declined`, `approved`, `pending`). Engine
`rejected` is normalized to `declined` on ingest; `/stats/ai-acceptance`
prefers durable `proposal_rule_analytics` (AI sources, non-group rows)
and falls back to live `proposals` when analytics are empty, counting
both `rejected` and `declined` spellings so historical rows remain
correct. Response shape is unchanged (ADR-060).

## Alternatives Considered

### Alternative 1: Durable slim proposal rows forever

**Description**: Keep one slim proposal row per approval unit indefinitely;
strip diffs after commit.

**Pros**:
- Simple resume of past proposal ids
- No rebuild path

**Cons**:
- Still unbounded row growth per remediate
- Duplicates finding identity already on violations
- Weak answer for “proposals only while actionable”

**Why not chosen**: Violations + analytics already answer history and
efficacy; proposals need not outlive actionable review.

### Alternative 2: Checkbox grain = violation id

**Description**: One approval unit per violation row.

**Pros**:
- Matches current DB finding grain
- Simple FK to `violations.id`

**Cons**:
- Conflicts with `approve_node()` and coupled transforms
- Forces users to approve L007 and L013 separately when they are one node fix

**Why not chosen**: Engine and Option C UI are node-grained; Gateway must
match.

### Alternative 3: Approved means PR/push succeeded

**Description**: Only mark approved when SCM publish completes.

**Pros**:
- Aligns “done” with git

**Cons**:
- Loses accept-without-publish signal for rule efficacy
- Conflates review and publish lifecycles

**Why not chosen**: Review and publish are separate; analytics need review.

## Consequences

### Positive

- Bounded `proposals` table size (remediate working set only; check discards)
- Archival node-grained proposals for activity review (Phase 2 bridges
  live engine checkbox ids to Gateway `proposal_id`)
- Rule-efficacy feedback (Tier1 vs AI, pure vs coupled) for later AI reporting
- Compatible with ADR-060 additive API evolution

### Negative

- Historical AI-only explanation/diff may be unavailable after flush
- Clients must tolerate rebuilt (non-persisted) proposals on old activities
- Flush must be idempotent to avoid double-counting analytics

### Neutral

- Engine violation emission unchanged
- ADR-052 in-memory operation registry remains the live progress fan-out;
  durable truth for review is scan + violations (+ ephemeral proposals while
  actionable)

## Implementation Notes

- Phase 1: schema + group-on-inject + analytics table + flush scaffolding +
  historical rebuild + additive API (this ADR).
- Phase 2: scan-scoped draft/commit; gate commit writes `review_status` and
  analytics; PR/push = publish flush.
- Phase 3: Option C two-gate engine/UI; AI reporting over analytics /
  `review_status`.
- SQLite: extend `_migrate_*` pattern in `apme_gateway.db` (no Alembic).
- Non-interactive Tier 1 auto-apply sets `review_status=deterministic_approved`
  when fixed violations are persisted.

## Related Decisions

- [ADR-020](ADR-020-reporting-service.md): Engine → Gateway reporting
- [ADR-023](ADR-023-per-finding-classification.md): Remediation class/resolution
- [ADR-028](ADR-028-session-based-fix-workflow.md): FixSession / Tier 1 auto-apply
  (amended in spirit by interactive Tier 1 in #379; this ADR covers Gateway
  persistence, not the engine flag)
- [ADR-044](ADR-044-node-identity-progression-model.md): Node identity / `path`
- [ADR-052](ADR-052-project-operation-sse-architecture.md): Operation registry
- [ADR-060](ADR-060-rest-api-versioning-contract.md): Additive REST only

## References

- [Issue #379](https://github.com/ansible/apme/issues/379) — interactive Tier 1 /
  Option C and durability discussion

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-07-09 | Brad Thornton | Initial acceptance from #379 durability design |
