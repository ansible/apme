# TASK-001: Add LICENSE File

## Parent Requirement

REQ-001: Core Scanning Engine

## Status

Pending

## Description

Add Apache 2.0 license to the repository per DR-009 decision. This includes the LICENSE file, README badge, and CONTRIBUTING.md updates.

## Prerequisites

- None

## Implementation Steps

1. Create LICENSE file with Apache 2.0 text
2. Add license badge to README.md (after project title)
3. Update CONTRIBUTING.md with contribution license terms (Apache 2.0 CLA-free)

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| LICENSE | Create | Apache 2.0 license text |
| README.md | Modify | Add license badge |
| CONTRIBUTING.md | Modify | Add license terms for contributors |

## Verification

Before marking complete:

- [ ] LICENSE file exists at repository root
- [ ] LICENSE contains full Apache 2.0 text
- [ ] README.md has license badge (e.g., `![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)`)
- [ ] CONTRIBUTING.md mentions Apache 2.0 license terms

## Acceptance Criteria Reference

From DR-009 (Licensing Model):
- [x] Decision: Apache 2.0 (Fully Open Source)
- [ ] LICENSE file added
- [ ] License badge in README
- [ ] CONTRIBUTING.md updated

## Related Artifacts

- DR-009: Licensing Model (Decided: Apache 2.0)

---

## Completion Checklist

- [ ] Implementation complete
- [ ] Verification steps pass
- [ ] Status updated to Complete
- [ ] Committed with message: `Implements TASK-001: Add Apache 2.0 LICENSE file`
