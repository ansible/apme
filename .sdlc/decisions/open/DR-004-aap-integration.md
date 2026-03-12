# DR-004: AAP Pre-Flight Integration

## Status

Open

## Raised By

Team Review — 2026-03-11

## Category

Architecture

## Priority

Blocking

---

## Question

How does APME integrate with AAP (Ansible Automation Platform) as a pre-flight check? The PRD says "Pre-Flight check integrated into AAP Job Templates" in one sentence, but this is a significant architectural decision:

- Is it a callback plugin?
- An Ansible collection with custom modules?
- A webhook that AAP calls?
- An Execution Environment (EE) with APME baked in?

## Context

AAP Job Templates can be extended in several ways:
1. **Callback plugins**: Run before/after playbook execution
2. **Custom collections**: Provide modules that can be called in playbooks
3. **Webhooks**: External service called via `uri` module or job workflow
4. **Execution Environments**: Container images with custom tooling

Each has different integration depth, maintenance burden, and user experience.

This could be a multi-phase effort spanning weeks.

## Impact of Not Deciding

- REQ-004 (Integration) cannot be implemented for AAP
- No clarity on deployment model for enterprise AAP users
- Cannot scope the AAP integration work

---

## Options Considered

### Option A: Ansible Collection with apme_scan Module

**Description**: Create `company.apme` collection with:
- `apme_scan` module: scans playbooks/roles before execution
- `apme_report` module: uploads results to dashboard (if exists)

User adds to playbook: `- company.apme.apme_scan: path={{ playbook_dir }}`

**Pros**:
- Familiar Ansible pattern
- Works with any Ansible (not just AAP)
- User controls when/where scan runs
- Collection published to Galaxy

**Cons**:
- Requires APME to be accessible (pod or in-process)
- Scan happens at runtime, not truly "pre-flight"
- User must modify playbooks

**Effort**: Medium

### Option B: AAP Callback Plugin

**Description**: Callback plugin that runs before playbook execution. Fails the job if critical violations found.

**Pros**:
- True pre-flight (runs before any tasks)
- No playbook modification needed
- Automatic for all jobs using the EE

**Cons**:
- Must be baked into Execution Environment
- AAP-specific (doesn't work with vanilla Ansible)
- Callback plugins have limited context

**Effort**: Medium

### Option C: Job Template Workflow with Webhook

**Description**: AAP workflow that:
1. Calls APME API (webhook) with project info
2. APME scans the project (fetched from SCM)
3. Returns pass/fail
4. Workflow continues or fails based on result

**Pros**:
- Decoupled architecture
- APME doesn't need to be in EE
- Can scan before project is checked out to controller

**Cons**:
- Requires APME to have SCM access
- More moving parts
- User must set up workflow

**Effort**: High

### Option D: Execution Environment with APME CLI

**Description**: Publish an EE image with `apme-scan` CLI baked in. Users run pre-scan as first playbook task.

**Pros**:
- Simple conceptually
- EE is the standard AAP customization point
- Works with existing job templates

**Cons**:
- Large EE image size
- User must add scan task to playbooks
- EE maintenance burden

**Effort**: Medium

---

## Recommendation

**Option A** (Ansible Collection) for v1:
- Most portable (works with AAP and vanilla Ansible)
- Familiar pattern for Ansible users
- Can be combined with Option D (EE) for enterprise deployment
- Collection publishing is a known process

Phase 2 could add callback plugin for "zero-config" AAP experience.

---

## Related Artifacts

- REQ-004: Integration
- PRD: AAP pre-flight requirement
- ADR-004: Podman deployment (current architecture)

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
