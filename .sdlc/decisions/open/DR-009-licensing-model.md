# DR-009: Licensing Model (Open Source vs Open Core)

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

What is our licensing model?

- **Fully Open Source**: All features available, Apache 2.0 or similar
- **Open Core**: Core scanner open, advanced features (dashboard, enterprise integrations) proprietary

Spotter's free tier is 100 scans/month. Are we competing on openness or following their model?

## Context

Licensing affects:
- Community adoption
- Enterprise sales model
- Feature allocation
- Contribution dynamics

Models to consider:
- **Apache 2.0**: Fully permissive, enterprise-friendly
- **AGPL**: Copyleft, requires derivative works to be open
- **BSL (Business Source License)**: Time-delayed open source
- **Open Core**: Separate proprietary components

## Impact of Not Deciding

- Cannot publish to GitHub (need license)
- Unclear contribution guidelines
- Enterprise sales model undefined

---

## Options Considered

### Option A: Fully Open Source (Apache 2.0)

**Description**: Everything is Apache 2.0. No proprietary components.

**Pros**:
- Maximum community adoption
- Enterprise-friendly license
- Contributions welcome
- Differentiation from Spotter's commercial model

**Cons**:
- No direct monetization
- Competitors can fork
- Must fund via support/consulting

**Effort**: None (just license decision)

### Option B: Open Core

**Description**:
- Scanner CLI: Apache 2.0
- Dashboard: Proprietary
- Enterprise SSO: Proprietary

**Pros**:
- Clear monetization path
- Core community can still contribute
- Enterprise customers pay for enterprise features

**Cons**:
- Community may feel betrayed
- Feature allocation debates
- Dual licensing complexity

**Effort**: Medium (need to separate components)

### Option C: Source Available (BSL)

**Description**: BSL license that converts to Apache 2.0 after 3 years. Usage restrictions for competitors.

**Pros**:
- Prevents direct competition
- Eventually open source
- Allows inspection

**Cons**:
- Not "real" open source
- Community skepticism
- Complex license terms

**Effort**: Low

### Option D: AGPL + Commercial

**Description**: AGPL for open source, commercial license for enterprises that don't want copyleft obligations.

**Pros**:
- Strong copyleft protects from proprietary forks
- Dual licensing proven model
- Enterprise path clear

**Cons**:
- AGPL can scare enterprises
- Contribution assignment complexity
- Some view AGPL as hostile

**Effort**: Low

---

## Recommendation

**Option A** (Apache 2.0) for maximum adoption.

Rationale:
- ansible-lint is MIT, x2a is Apache — ecosystem expectation
- Enterprise adoption prioritizes permissive licenses
- Differentiation from Spotter's commercial model
- Community contributions more likely
- Can monetize via:
  - Enterprise support contracts
  - Managed service (SaaS)
  - Professional services

---

## Related Artifacts

- README.md: License badge
- Contributing guidelines
- Enterprise strategy

---

## Discussion Log

| Date | Participant | Input |
|------|-------------|-------|
| 2026-03-11 | Team | Initial question raised during competitive analysis |

---

## Decision

**Status**: Open
**Date**: —
**Decided By**: —

**Decision**: —

**Rationale**: —

**Action Items**:
- [ ] —
