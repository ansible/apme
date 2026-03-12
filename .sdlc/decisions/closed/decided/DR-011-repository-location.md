# DR-011: Where should the project code be hosted and will it be public?

## Status

Decided

## Raised By

User — 2026-03-11

## Category

Process

## Priority

High

---

## Question

Where are we going to put the project code and will it be public?

## Context

We're about to start implementation and need a repo set up first.

## Impact of Not Deciding

Unable to extend past initial scanner work Brad has done.

---

## Options Considered

### Option A: GitHub (github.com/ansible/apme)

**Description**: Create new public repo under the official Ansible GitHub org.

**Pros**:
- Open visibility and collaboration opportunities

**Cons**:
- Requires Ansible org approval process
- People will see this early and may ask questions; code is not ready yet for consumption

**Effort**: Low

### Option B: Internal GitLab (Private)

**Description**: Setting up repo on internal Red Hat GitLab.

**Pros**:
- Not publicly accessible for now
- Internal CI/CD capabilities can be utilized

**Cons**:
- Not publicly visible
- Need VPN access
- Would need mirroring or moving to public repo later

**Effort**: Low

### Option C: Brad's Existing Repo (github.com/cidrblock/apme)

**Description**: Fork or continue development on Brad's personal GitHub repo. People given read and write access to the repo.

**Pros**:
- Some code already there in place

**Cons**:
- Need to retrofit back into proposed SDLC spec-driven AI framework

**Effort**: Low

---

## Recommendation

*No recommendation — open for discussion.*

---

## Related Artifacts

- DR-009: Licensing Model (OSS vs Open Core) — visibility decision affects licensing strategy
- ADR-004: Podman Pod Deployment — deployment patterns may inform repo structure

---

## Discussion Log

| Date | Participant | Input |
|------|-------------|-------|
| | | |

---

## Decision

**Status**: Decided
**Date**: 2026-03-12
**Decided By**: User

**Decision**: Option A — GitHub public (github.com/ansible/apme)

**Rationale**: Public visibility aligns with open source strategy.

**Action Items**:
- [ ] Brad to request repo creation and transfer code to ansible/apme

---

## Post-Decision Updates

| Date | Update |
|------|--------|
| | |
