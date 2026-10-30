# TASK-002: Backstage Project Workflow

## Parent Requirement

[REQ-021](../requirement.md), AC2 and AC3.

## Status

Complete

## Description

Build the standard plugin and development harness around the original Portal
transport adapter and shared scan/remediation controllers.

## Prerequisites

TASK-001 shared package foundation.

## Implementation Notes

1. Preserve the existing catalog/discovery/fetch adapter with source provenance.
2. Add Local proxy discovery, BUI repository list/form/dialog and project page.
3. Use existing workflow controllers and presentation for scans/remediation.
4. Test registration, project selection, HTTP/SSE, denial and unmount behavior.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `frontend/backstage/plugins/backstage-apme/` | Create | Standard package and embeddable workflow |
| `frontend/backstage/dev/` | Create | React 18/Router 6 development context |
| `frontend/backstage/tests/` | Create | Host transport/UI/closure regressions |
| `package.json`, `package-lock.json` | Create | Independent standard workspace |

## Verification

- [x] `tox -e frontend-lint,frontend-unit,frontend-dev-build`
- [x] `tox -e frontend-pack-smoke`

## Acceptance Criteria Reference

AC2 project workflow; AC3 explicit host transport. Actual Portal RBAC and RHDH
runtime acceptance belong to TASK-005 and are not established by this task.

## Completion Evidence

2026-10-30: repository lint passed; 270 native tests and 47 host/workflow tests
passed; host type checks and development build passed. Clean tarball installation,
Node import, public declaration type check and browser bundle passed outside the
checkout. Real RHDH/Portal runtime acceptance remains TASK-005.
