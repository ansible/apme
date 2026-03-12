# DR-007: Target Persona Priority

## Status

Open

## Raised By

Team Review — 2026-03-11

## Category

Strategy

## Priority

High

---

## Question

Which persona are we building for first?

- **DevOps Engineer**: More fixers, diff view, CI integration, fast iteration
- **PM/Architect**: Dashboard, SBOM, metrics, reporting

The PRD serves both but doesn't prioritize. Features are different:
- DevOps: CLI polish, `--fix --dry-run`, SARIF for CI, fast feedback
- PM: Dashboard, historical trending, executive reports, SBOM

## Context

Resource constraints mean we can't do everything at once. Spotter serves both but their CLI (DevOps) came first, dashboard (PM) came later.

The persona choice affects:
- Feature priority
- UX decisions
- Marketing messaging
- Documentation style

## Impact of Not Deciding

- Feature prioritization is ad-hoc
- Mixed messaging confuses users
- Team may work on conflicting priorities

---

## Options Considered

### Option A: DevOps First

**Description**: v1 focuses on CLI experience:
- Fast scanning
- `--fix` with diff preview
- SARIF output for CI
- JUnit for test frameworks
- Excellent error messages

Dashboard and reporting in v2.

**Pros**:
- Follows Spotter's successful model
- Faster feedback loop
- Smaller scope for v1
- DevOps influence buying decisions

**Cons**:
- PM/Architect features delayed
- May lose enterprise deals that need reporting

**Effort**: Reduces v1 scope

### Option B: PM/Architect First

**Description**: v1 focuses on visibility:
- Dashboard with historical trending
- SBOM generation
- Executive reports
- Multi-project aggregation

CLI is basic scan + JSON output.

**Pros**:
- Enterprise-ready from day one
- Differentiation from CLI-only tools

**Cons**:
- Longer v1 timeline
- Requires data persistence (DR-008)
- May not resonate with open-source community

**Effort**: Increases v1 scope

### Option C: Balanced MVP

**Description**: Minimum viable for both:
- DevOps: CLI with `--fix`, `--json`, basic CI integration
- PM: Simple Streamlit dashboard reading JSON files

**Pros**:
- Something for everyone
- Can iterate based on adoption

**Cons**:
- Neither persona fully satisfied
- Risk of "mediocre at everything"

**Effort**: Medium

---

## Recommendation

**Option A** (DevOps first).

Rationale:
- DevOps engineers are the primary users of automation tools
- CLI-first is proven (Spotter, ansible-lint, semgrep)
- Dashboard requires solving data persistence (DR-008) which adds complexity
- DevOps engineers influence tooling decisions; PMs follow
- Faster v1 = faster feedback = better v2 dashboard

## Related Artifacts

- PRD: Persona definitions
- DR-003: Dashboard Architecture
- DR-008: Data Persistence

---

## Discussion Log

| Date | Participant | Input |
|------|-------------|-------|
| 2026-03-11 | Team | Initial question raised during PRD review |

---

## Decision

**Status**: Open
**Date**: —
**Decided By**: —

**Decision**: —

**Rationale**: —

**Action Items**:
- [ ] —
