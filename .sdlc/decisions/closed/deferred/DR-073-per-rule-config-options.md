# DR-073: Per-Rule Configuration Options

## Status

Deferred

## Raised By

Bradley Thornton (@cidrblock) — 2026-06-10

## Category

Architecture

## Priority

Low

---

## Question

Should APME support user-configurable options per rule (e.g., a custom sensitive-name word list for M031)?

## Context

PR #338 adds M031 (register_secret recommendation) with a hardcoded list of sensitive-looking variable-name patterns (password, token, secret, api_key, etc.). @cidrblock requested the ability for users to customize this list.

Current `RuleConfig` proto only supports: `rule_id`, `severity`, `enabled`, `enforced`. No per-rule custom parameters.

## Impact of Not Deciding

Low. M031 ships with sensible defaults. Users cannot customize the name heuristic. Revisit after customer feedback on detections and secret masking with ansible-core 2.22+.

---

## Options Considered

### Option A: Extend RuleConfig Proto

**Description**: Add `map<string, string> options` or typed fields to `RuleConfig`. Extend `.apme/rules.yml` parser. Pass config through Primary to validators.

**Pros**:
- Clean, type-safe configuration
- Works for all rules, not just M031
- UI/Gateway can expose config

**Cons**:
- Proto change affects all services
- Needs gRPC regen, validator interface changes
- More complex than needed for single rule

**Effort**: Medium-High

### Option B: Environment Variable Workaround

**Description**: M031 reads `APME_M031_SENSITIVE_WORDS` env var as comma-separated override.

**Pros**:
- Quick to implement
- No proto changes

**Cons**:
- Doesn't scale to other rules
- Not discoverable
- Container config complexity

**Effort**: Low

### Option C: Defer Until Customer Feedback

**Description**: Ship M031 with defaults. Gather usage data. Design config system when we understand real needs.

**Pros**:
- Avoids premature optimization
- Focus on the ansible-core 2.22+ `register_secret` recommendation
- Real requirements inform design

**Cons**:
- Users can't customize immediately

**Effort**: None

---

## Recommendation

**Option C: Defer**. Ship M031 with hardcoded defaults. Once customers test M031's sensitive-name detection and `register_secret` guidance, their feedback will inform whether per-rule configuration is needed and what shape it should take.

---

## Related Artifacts

- PR #338: M031 `register_secret` recommendation rule
- ADR-041: Per-project rule overrides (severity/enabled/enforced)
- ANSTRAT-1720: Selective Redaction of Sensitive Variables

---

## Discussion Log

| Date | Participant | Input |
|------|-------------|-------|
| 2026-06-10 | @cidrblock | Requested user-configurable word list in PR #338 review |
| 2026-06-11 | @pgriffit | Created DR, recommending deferral until customer feedback |

---

## Decision

**Status**: Deferred
**Date**: 2026-06-11
**Decided By**: Phil Griffiths

**Decision**: Defer per-rule config options until customer feedback on M031 detections and the `register_secret` recommendation.

**Rationale**: Core detection capability is the priority. Custom word lists are nice-to-have. Real usage will inform whether config is needed and what shape it should take.

**Action Items**:
- [x] Ship M031 with sensible defaults (PR #338)
- [ ] Gather customer feedback on M031 detections — Owner: PM
- [ ] Revisit this DR if config requests emerge — Owner: Engineering

---

## Post-Decision Updates

| Date | Update |
|------|--------|
| 2026-06-11 | DR created, status Deferred |
