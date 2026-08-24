# ADR-023: Per-Finding Remediation Classification and Resolution

## Status

Implemented

## Date

2026-03-18

## Context

ADR-009 established a three-tier remediation model (deterministic, AI-proposable, manual review) and described tier assignment as a static, per-rule property:

```yaml
rules:
  L002:
    tier: 1  # Deterministic
  L015:
    tier: 2  # AI-proposable
  R101:
    tier: 3  # Manual review
```

This model assumed a rule's tier never changes. In practice, tier assignment must be **per-finding and mutable** because:

1. **Transform failures reclassify findings.** A rule with a registered transform (Tier 1) may fail to apply on a specific file — malformed YAML, unsupported structure, or edge cases the transform doesn't handle. That specific finding should escalate to Tier 2, not stay labeled "auto-fixable."

2. **Oscillation reclassifies findings.** The convergence loop (scan → fix → rescan) may detect that fixes are creating new violations at the same rate. Remaining Tier 1 findings must escalate rather than loop forever.

3. **AI outcomes vary per finding.** When Abbenay proposes a fix, the result differs per finding: one may succeed, another may fail validation, a third may have low confidence. The rule is the same; the resolution differs.

4. **User rejection is per finding.** A user may accept an AI fix for one finding but reject another from the same rule.

5. **Reporting needs per-finding granularity.** The reporting service (ADR-020) must persist what happened to each finding to show remediation funnel metrics (e.g., "60% auto-fixed, 25% AI-proposed, 10% AI-rejected, 5% manual").

Two forces are in tension:

- **Simplicity**: a static per-rule tier is trivial to implement and reason about.
- **Accuracy**: the pipeline produces per-finding outcomes that a per-rule model cannot represent.

## Decision

**Remediation state is carried as two orthogonal fields on each finding (violation dict and proto `Violation` message), not as a static per-rule property.**

### RemediationClass — "What to do next"

| Value | Meaning |
|-------|---------|
| `auto-fixable` | A deterministic transform exists in the `TransformRegistry` |
| `ai-candidate` | No transform, but an AI agent can propose a fix |
| `manual-review` | Requires human judgment; not AI-proposable |

Initial assignment: the `TransformRegistry` is authoritative. If a rule ID has a registered transform, the finding starts as `auto-fixable`. Otherwise, it falls to `ai-candidate` or `manual-review` based on the `ai_proposable` flag.

Classification can change: `auto-fixable` → `ai-candidate` (on transform failure or oscillation), `ai-candidate` → `manual-review` (on repeated AI failure).

### RemediationResolution — "What happened"

| Value | Meaning |
|-------|---------|
| `unresolved` | Initial state — no remediation attempted yet |
| `transform-failed` | Deterministic transform returned `applied=False` |
| `oscillation` | Convergence loop detected oscillation and bailed |
| `ai-proposed` | AI proposed a fix (pending validation or user review) |
| `ai-failed` | AI call failed or returned no usable result |
| `ai-low-confidence` | AI returned a proposal below the confidence threshold |
| `user-rejected` | User explicitly rejected the proposed fix |

Resolution is write-once per remediation attempt — once a finding is resolved, the value captures the terminal state of that attempt.

### State Flow

```
Finding created (scan)
  │
  ├─ class = auto-fixable, resolution = unresolved
  │   │
  │   ├─ transform succeeds → finding removed from violations
  │   ├─ transform fails → class = ai-candidate, resolution = transform-failed
  │   └─ oscillation detected → class = ai-candidate, resolution = oscillation
  │
  ├─ class = ai-candidate, resolution = unresolved
  │   │
  │   ├─ AI proposes fix → resolution = ai-proposed
  │   │   ├─ validation passes → finding removed (or re-scanned)
  │   │   ├─ validation fails → resolution = ai-failed
  │   │   ├─ below threshold → resolution = ai-low-confidence
  │   │   └─ user rejects → resolution = user-rejected
  │   │
  │   └─ AI call fails → resolution = ai-failed
  │
  └─ class = manual-review, resolution = unresolved
      └─ (remains for human action)
```

### Wire Format

Both fields are carried on the proto `Violation` message:

```protobuf
enum RemediationClass { ... }      // field 8
enum RemediationResolution { ... } // field 9
```

### Single Source of Truth

Classification happens server-side in `engine_server.py` before proto serialization. CLI clients consuming gRPC responses receive pre-classified violations and do not re-classify. The local (in-process) scan path classifies via the same `add_classification_to_violations` function.

## Alternatives Considered

### Alternative 1: Static Per-Rule Config

**Description**: Assign tiers in a YAML config file keyed by rule ID, as originally sketched in ADR-009.

**Pros**:
- Simple to implement and reason about
- Easy to override per-project

**Cons**:
- Cannot represent transform failures, AI outcomes, or user rejection
- A rule with a registered transform is always labeled "auto-fixable" even when the transform fails on specific files
- No data for reporting service funnel metrics

**Why not chosen**: The pipeline produces per-finding outcomes that a per-rule model cannot represent. Real-world usage showed that transform failures and edge cases make static assignment inaccurate.

### Alternative 2: Separate Resolution Tracking

**Description**: Findings carry only `remediation_class`. Resolution state is stored in a separate data structure (e.g., a dict keyed by `(rule_id, file, line)`).

**Pros**:
- Keeps the violation dict lean
- Resolution tracking is opt-in

**Cons**:
- Adds indirection — consumers must join two data structures
- Harder to serialize over gRPC (resolution must be a separate repeated field or a side-channel)
- Easy to lose sync between the finding and its resolution

**Why not chosen**: Carrying both fields on the finding is simpler, serializes naturally in protobuf, and keeps the data co-located.

### Alternative 3: Single Combined Enum

**Description**: Merge class and resolution into one field (e.g., `AUTO_FIXABLE_UNRESOLVED`, `AUTO_FIXABLE_TRANSFORM_FAILED`, `AI_CANDIDATE_PROPOSED`, ...).

**Pros**:
- Single field to read

**Cons**:
- Combinatorial explosion (3 classes x 7 resolutions = 21 values, growing with each new state)
- Loses the semantic distinction between "what to do next" and "what happened"
- Harder to query (e.g., "count all AI_CANDIDATE regardless of resolution")

**Why not chosen**: Two orthogonal dimensions are clearer than one combined enum with 21+ values.

## Consequences

### Positive

- Findings accurately reflect their remediation state at every stage of the pipeline
- The reporting service (ADR-020) can build funnel metrics from per-finding data without inference
- AI integration (Abbenay) has a clear contract: set `remediation_resolution` on each finding it processes
- The proto wire format carries all state needed for dashboard visualization
- Transform failures are visible to users (previously silent)

### Negative

- Every violation dict now carries two extra fields (`remediation_class`, `remediation_resolution`)
- Code that processes violations must handle mutable classification (not a static lookup)
- The `GraphRemediationEngine` convergence loop must update resolution on failures, adding complexity to the loop

### Neutral

- The `TransformRegistry` remains the initial authority for `auto-fixable` — this decision does not change how transforms are registered, only how their outcomes are tracked
- AI resolution states (`ai-proposed`, `ai-failed`, `ai-low-confidence`, `user-rejected`) are populated by the `AIProvider` integration — see ADR-025 and `docs/design/DESIGN_AI_ESCALATION.md`

## Implementation Notes

- `RemediationClass` and `RemediationResolution` are `str, Enum` (not `StrEnum`) for Python 3.10 compatibility
- `add_classification_to_violations()` mutates the violation list in place and returns `None` — callers should not capture the return value
- The `_to_str_value()` helper in `partition.py` handles both enum members and plain strings when extracting values for dict lookups (necessary because `str(enum_member)` returns `"ClassName.MEMBER"` with `str, Enum`)
- Tier 1 transforms are attempted via `ContentGraph.apply_transform()`; when a transform cannot be applied, the result is "not applied" (there is no automatic `remediation_resolution` update to `TRANSFORM_FAILED` or reclassification to `AI_CANDIDATE` — the violation remains in its original state)
- The engine sets `OSCILLATION` on remaining Tier 1 violations when the convergence loop detects non-decreasing fixable counts

## Related Decisions

- ADR-008: Rule ID conventions — classification is orthogonal to rule IDs; the same rule can have different classification per finding
- ADR-009: Remediation engine — this ADR refines the three-tier model from static per-rule to dynamic per-finding
- ADR-020: Reporting service — will consume `remediation_class` and `remediation_resolution` fields for funnel metrics and trend analysis
- ADR-025: AIProvider protocol — defines the abstraction that populates the AI resolution states
- ADR-044: Node identity and progression model — graph-based convergence replaces file-based engine

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-18 | AI agent | Initial proposal and acceptance |
