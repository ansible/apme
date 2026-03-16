# DR-006: Success Metrics Baselines

## Status

Open

## Raised By

Team Review — 2026-03-11

## Category

Product

## Priority

Low

---

## Question

How do we measure success? PRD claims:
- ">90% remediation rate for standard modules"
- "8x faster than manual"

But what are the baselines? Without baselines, these are aspirational and not measurable.

## Context

The PRD contains specific numeric targets without:
- Current baseline measurements
- Methodology for measurement
- Definition of "standard modules"
- Definition of "manual" remediation process

Without baselines, we can't:
- Prove we hit targets
- Track improvement over time
- Make data-driven prioritization decisions

## Impact of Not Deciding

- Success criteria are unmeasurable
- No way to demonstrate value to stakeholders
- Marketing claims are unsupported

---

## Options Considered

### Option A: Establish Baselines Now

**Description**: Before v1 launch, measure:
- Manual remediation time on sample playbooks (5-10 real projects)
- FQCN coverage of current transform registry
- False positive rate on sample projects

**Pros**:
- Enables honest marketing
- Tracks improvement over time
- Sets realistic expectations

**Cons**:
- Requires time investment
- Need access to representative playbooks
- May reveal uncomfortable truths

**Effort**: Medium

### Option B: Relative Metrics Only

**Description**: Track improvement from v1.0 to v1.1, etc. No absolute claims.

**Pros**:
- Avoids baseline problem
- Still shows progress
- More defensible

**Cons**:
- Can't claim "8x faster" without reference
- Less compelling marketing

**Effort**: Low

### Option C: Use Industry Benchmarks

**Description**: Reference published data on manual remediation time (if any exists).

**Pros**:
- External validation
- No internal measurement needed

**Cons**:
- May not exist for Ansible modernization
- May not be comparable to our use case

**Effort**: Low (if data exists)

### Option D: Defer Metrics to Post-Launch

**Description**: Launch first, measure after with real user data.

**Pros**:
- Real-world data more meaningful
- Faster to market

**Cons**:
- Initial marketing unsubstantiated
- May lose early adopter trust

**Effort**: None (deferral)

---

## Recommendation

**Option A** (establish baselines now) for key metrics:
1. Manual remediation time: Time 2-3 team members fixing FQCN issues manually on a sample role
2. Automation coverage: % of issues auto-fixable by Tier 1 transforms
3. Accuracy: False positive rate on 3 real-world projects

Even rough baselines are better than none.

---

## Related Artifacts

- PRD: Success metrics section
- EXECUTIVE-SUMMARY.md: Claims about efficiency

---

## Discussion Log

| Date | Participant | Input |
|------|-------------|-------|
| 2026-03-11 | Team | Initial question raised during PRD review |

---

## Decision

**Status**: Decided
**Date**: 2026-03-16
**Decided By**: Team

**Decision**: Option A — Establish Baselines Now

**Rationale**:
- Even rough baselines are better than none
- Enables honest, defensible marketing claims
- Tracks improvement over time
- Sets realistic expectations with stakeholders
- Key metrics: manual remediation time, automation coverage, false positive rate

**Action Items**:
- [ ] Measure manual remediation time: Time 2-3 team members fixing FQCN issues manually on sample role
- [ ] Measure automation coverage: % of issues auto-fixable by Tier 1 transforms
- [ ] Measure accuracy: False positive rate on 3 real-world projects
- [ ] Document methodology for reproducible measurement
- [ ] Update PRD/marketing with measured baselines
