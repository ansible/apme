# TASK-001: Shared Client and Workflow Foundation

## Parent Requirement

[REQ-021](../requirement.md), AC1 and AC2.

## Status

Complete

## Description

Extract the existing Gateway client/types and plain adapter helpers. Preserve
native imports and add a CSS-free workflow controller entry point.

## Prerequisites

ADR-073 accepted through the implementation discussion.

## Implementation Notes

1. Move the existing client/types into `frontend/packages/apme-client`.
2. Keep native imports and workflow provider/default behavior compatible.
3. Add `@apme/ui-workflow/headless` and verify its runtime dependency graph.
4. Verify both React 18 host and React 19 native builds/tests.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `frontend/packages/apme-client/` | Create | Pure Gateway client/transport |
| `frontend/packages/ui-workflow/` | Modify | Shared dependency and headless exports |
| `frontend/src/services/api.ts`, `frontend/src/types/api.ts` | Modify | Compatible re-exports |
| `frontend/tsconfig.json`, `frontend/vite.config.ts` | Modify | Explicit native type/test scope |

## Verification

- [x] `tox -e ui-workflow-pack`
- [x] `tox -e frontend-build,frontend-unit`
- [x] `tox -e lint`

## Acceptance Criteria Reference

AC1 independent packages; AC2 reuse authoritative workflow behavior.

## Notes

Workflow API requests also encode project IDs consistently with the existing
Gateway client. No Gateway endpoint, state or SCM contract changed.

## Completion Evidence

2026-10-30: repository lint passed; 270 native tests and 47 host/workflow tests
passed; host type checks and development build passed. Clean tarball installation,
Node import, public declaration type check and browser bundle passed outside the
checkout. Real RHDH/Portal runtime acceptance remains TASK-005.
