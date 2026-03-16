# DR-010: Ansible Version Coverage Range

## Status

Open

## Raised By

Team Review — 2026-03-11

## Category

Technical

## Priority

Medium

---

## Question

What range of ansible-core versions do we need to support for version-specific scanning?

Options:
- Full range: 2.9 → 2.20 (complete history)
- AAP 2.x range: 2.14 → 2.20 (AAP-focused)
- Latest only: 2.18 → 2.20 (minimal)

This affects:
- Module metadata database size
- Deprecation tracking complexity
- Test matrix size

## Context

Different ansible-core versions have different:
- Module sets (modules added, removed, deprecated)
- Syntax support (Jinja2 versions, YAML handling)
- FQCN requirements (not required before 2.10)

Building a complete metadata database requires:
- Extracting argspec from each version
- Tracking deprecation timelines
- Mapping short names to FQCNs per version

ADR-006 already provisions venvs for 2.18/2.19/2.20 in the Ansible container.

## Impact of Not Deciding

- Cannot implement version-specific scanning (DR-001)
- Module metadata scope unclear
- Ansible validator venv list undefined

---

## Options Considered

### Option A: Full Range (2.9 → 2.20)

**Description**: Support scanning against any version from Ansible 2.9 (pre-collections) to 2.20 (current).

**Pros**:
- Complete migration path coverage
- Supports very old playbooks
- "Works with everything" marketing

**Cons**:
- Huge metadata database
- Many edge cases (pre-collection era)
- 2.9/2.10 are effectively EOL

**Effort**: Very High

### Option B: AAP 2.x Range (2.14 → 2.20)

**Description**: Support versions shipped with AAP 2.x:
- AAP 2.1: ansible-core 2.14
- AAP 2.4: ansible-core 2.16
- AAP 2.5: ansible-core 2.18

**Pros**:
- Covers real enterprise migration paths
- Reasonable scope
- All versions are collections-era

**Cons**:
- Can't scan truly ancient playbooks
- May miss 2.12/2.13 users

**Effort**: Medium

### Option C: Latest Only (2.18 → 2.20)

**Description**: Only support current ansible-core versions. Users targeting older versions use older tooling.

**Pros**:
- Minimal metadata
- Fast to build
- Matches current Ansible container venvs

**Cons**:
- Can't validate "will this work on 2.16?"
- Limited migration path support
- Less useful for enterprise

**Effort**: Low

### Option D: Start Minimal, Expand Based on Demand

**Description**: Ship with Option C (2.18-2.20). Add older versions if users request.

**Pros**:
- Fast v1
- Demand-driven expansion
- Avoids premature work

**Cons**:
- May frustrate early adopters
- Expansion work later

**Effort**: Low initially

---

## Recommendation

**Option D** (start minimal, expand based on demand).

Start with 2.18, 2.19, 2.20 (already in Ansible container venvs per ADR-006). Add 2.16 and 2.14 in v1.1/v1.2 if enterprise users request.

Rationale:
- Current venv setup already supports this
- Most users targeting AAP 2.5 (2.18)
- Can measure demand from feature requests
- Expanding is easier than cutting scope

---

## Related Artifacts

- DR-001: Version-Specific Analysis (depends on this)
- ADR-006: Ephemeral Venvs (defines current venv set)
- Ansible validator container

---

## Discussion Log

| Date | Participant | Input |
|------|-------------|-------|
| 2026-03-11 | Team | Initial question raised during technical planning |

---

## Decision

**Status**: Decided
**Date**: 2026-03-16
**Decided By**: Team

**Decision**: Option D — Start Minimal, Expand Based on Demand (potentially back to 2.9)

**Rationale**:
- Start with 2.18, 2.19, 2.20 (already in Ansible container venvs per ADR-006)
- Add 2.16 and 2.14 in v1.1/v1.2 if enterprise users request
- Most users targeting AAP 2.5 (ansible-core 2.18)
- Expanding is easier than cutting scope
- Demand-driven approach avoids premature work
- May need to go as far back as 2.9 if technically possible (based on user demand)

**Action Items**:
- [ ] Confirm 2.18/2.19/2.20 venvs work for version-specific scanning
- [ ] Track user requests for older version support
- [ ] Investigate technical feasibility of 2.9-2.14 support (pre-collections era challenges)
- [ ] Prioritize 2.16/2.14 expansion based on enterprise feedback
