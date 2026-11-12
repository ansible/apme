# TASK-023: BUI Workflow Presentation and Host Actions

## Parent Requirement

[REQ-021](../requirement.md), AC1–AC3 and AC5; ADR-073. Refs #860.

## Status

Complete

## Description

Replace the shared workflow's PatternFly controls and theme bindings with the
pinned BUI host API. Expose host publishing actions through shared panels and
remove the Portal DOM insertion workaround.

## Prerequisites

- Shared workflow/controller and Backstage host packages are available.
- The requester explicitly waived native SPA backward compatibility for #860.

## Implementation Notes

1. Target BUI 0.17.1, React 18 and Router 6; retain one workflow/controller source.
2. Forward optional `hostShipActions` through shared project/operation panels to
   commit and completed result actions. Portal delegates to the shared panel.
3. Migrate actual BUI controls, icons and theme tokens. Preserve diff parsing,
   YAML highlighting, graph behavior, approval decisions and authenticated I/O.
4. Record missing BUI progress/loading primitives as narrow accessible exceptions.
5. Keep Portal's theme bridge while its other PatternFly consumers remain; remove
   the Local bridge when its workflow is fully migrated.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `frontend/packages/ui-workflow/` | Modify | BUI controls, tokens, host action API and package contract |
| `frontend/backstage/` | Modify | Host API forwarding and package validation |
| `frontend/portal/` | Modify | Shared panel composition |
| `containers/rhdh/plugin/` | Modify | Remove obsolete Local PatternFly theme integration |

## Verification

- `tox -e frontend-build,frontend-lint,frontend-unit,frontend-dev-build`
- `tox -e frontend-pack-smoke,ui-workflow-pack`
- `tox -e lint` and `tox -e unit`
- Shared action visibility in commit/completion and failed submission states;
  filtering, proposal decisions, feedback dialog and headless package boundaries.
- Actual Local/Portal runtime acceptance remains in #833.

## Acceptance Criteria Reference

- AC1: CSS-free headless controller and independently delivered packages.
- AC2: Preserve assessment/remediation behavior and shared workflow state.
- AC3: Host-owned actions through explicit props without DOM queries or host guessing.
- AC5: Reproducible BUI dependency and packed-consumer checks.

## Notes

Native SPA retirement remains gated by #833 and TASK-006; this task does not claim
feature acceptance or remove that host. The workflow presentation now targets
Backstage hosts without a native compatibility adapter.

## Results

- `tox -e lint` and frontend lint/types passed.
- Python unit suite: 5,158 passed, 52 skipped (PostgreSQL and local OPA configured).
- BUI workspace: 361 tests passed; Portal frontend: 525 tests passed against the
  pinned SDK. Shared publishing tests cover success, failure, and completion.
- Chromium harness: light/dark controls, HTTP/SSE workflow, host navigation and
  custom YAML rendering passed without PatternFly styles or its root class.
- Workflow tarball, standalone and Portal packed-consumer checks passed, including
  public declarations and browser bundles. Portal build and Local RHDH dynamic
  plugin export passed. These are package/harness checks, not host certification.
- Five independent read-only review passes reported no findings.
