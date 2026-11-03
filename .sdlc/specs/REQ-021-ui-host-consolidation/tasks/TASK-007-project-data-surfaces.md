# TASK-007: Shared Project History, Findings and Dependencies

## Parent Requirement

[REQ-021](../requirement.md), AC2–AC4. Implements the project data family from
[TASK-004](TASK-004-domain-feature-migration.md).

## Status

Complete

## Description

Add reusable BUI project findings, history/details and dependency views using the
existing client, findings renderer and original plugin data contracts.

## Prerequisites

TASK-001 through TASK-003 complete.

## Implementation Notes

1. Reuse the native finding mapping and assessment renderer for history/current
   findings, preserving suppression/review fields and read-only history behavior.
2. Add paged history, project-checked activity details, proposal/diff/log results
   and dependency inventory with license/supplier metadata where available.
3. Let hosts own selected tab/activity and deep links. On tab changes detach UI
   subscriptions without cancelling Gateway operations.
4. Normalize the existing Portal list/history envelopes in an explicit adapter;
   never retry against a direct Gateway. Preserve Portal's existing result bounds.
5. Verify loading/error/empty/denied states, paging, late responses, project
   identity, tab navigation and external package consumption.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `frontend/backstage/plugins/backstage-apme/src/components/` | Extend | Shared project surfaces |
| `frontend/backstage/plugins/backstage-apme/src/api/` | Extend | Explicit Portal response normalization |
| `frontend/packages/ui-workflow/src/shared/` | Extend | One native/shared finding mapping |
| `frontend/packages/apme-client/src/` | Extend | Encoded activity/filter URLs and dependency metadata |
| `frontend/backstage/tests/` | Extend | Interaction and transport regressions |

## Verification

- [x] `tox -e frontend-lint,frontend-unit,frontend-dev-build`
- [x] `tox -e frontend-pack-smoke`
- [x] `tox -e ui-workflow-pack`
- [x] `tox -e lint`

## Acceptance Criteria Reference

AC2 shared Gateway behavior, AC3 explicit host boundaries, AC4 project data.
Actual-host RBAC and missing Portal transport routes remain TASK-005.

## Completion Evidence

2026-10-30: native type checks and all 270 native tests passed after the shared
finding mapper/client changes. Host type checks, project/Portal transport and
interaction tests, development browser build, and clean tarball installation,
public declarations and browser bundling passed. All new project surfaces and
the Portal normalization adapter are included in the external package smoke check.
The workbench resolves name aliases to the Gateway's canonical project ID before
loading scoped data. Actual-host Portal RBAC and complete legacy pagination remain
TASK-005; this task does not claim that those backend routes were changed.
