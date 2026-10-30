# TASK-009: Shared Model and Galaxy Settings

## Parent Requirement

[REQ-021](../requirement.md), AC2–AC4; settings family from TASK-004.

## Status

In Progress

## Description

Port native AI model preference and Galaxy server management into reusable BUI
components using the current Gateway client and workflow preference contract.
Full AI provider administration remains separate domain migration work.

## Prerequisites

TASK-001 through TASK-003 complete.

## Implementation Notes

1. Reuse the workflow model list and browser preference key, keeping storage
   failures visible and independent from model availability.
2. Use existing Galaxy CRUD; preserve blank edit token semantics and support
   explicit token clearing, auth URL and deployment-default certificate policy.
3. Separate host view/manage permission decisions. Default embedded settings to
   no Galaxy access; Local enables its personal instance settings.
4. Current Portal routes drop certificate policy writes. Expose that capability
   only to hosts that forward it; do not report an ignored change as saved.
5. Mount only the selected page and show request/mutation errors without fallback.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `frontend/backstage/plugins/backstage-apme/src/components/` | Extend | Settings composition and forms |
| `frontend/backstage/plugins/backstage-apme/src/api/apmeApi.ts` | Extend | Personal-host capabilities |
| `frontend/packages/ui-workflow/src/headless.ts` | Extend | Existing preference constant without CSS |
| `frontend/backstage/README.md` | Extend | Host permissions and capability notes |

## Verification

- [x] Type checking, formatting, browser build and packages via tox
- [x] `git diff --check`
- [x] Repository lint via `tox -e lint`

Interaction tests and actual-host permission/CRUD acceptance remain outstanding.

## Acceptance Criteria Reference

AC2 shared behavior, AC3 host boundaries, AC4 native settings coverage.

## Implementation Evidence

2026-10-30: reusable model preference/Galaxy CRUD and lazy workbench Settings
implemented. Forms preserve blank-edit tokens, allow explicit token removal and
confirm deletion. Host view/manage and certificate capabilities are explicit;
no Galaxy request is made without view access. The existing Portal backend
currently drops certificate updates, so embedded hosts default to no certificate
editing. Type checking, formatting, browser build and package generation passed.
Interaction tests were not added or run; actual-host permissions, storage failure,
CRUD and keyboard/theme acceptance remain outstanding.
