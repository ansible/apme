# TASK-018: Non-workflow Controls and Feature Audit

## Parent Requirement

[REQ-021](../requirement.md), AC2–AC4; ADR-041, ADR-043 and ADR-073.

## Status

Complete

## Description

Review every Local control outside the remediation workflow against the original
UI. Give ordinary actions, links, destructive actions and pagination consistent
compact styling, including dialogs and disabled/keyboard states. Restore visible
rule enable/severity controls, project deletion and failed-load retry paths.

## Prerequisites

- TASK-017 complete.

## Implementation Notes

1. Inventory page actions and original non-fleet functionality; record results.
2. Scope button presentation to explicit ordinary controls, excluding workflow.
3. Make rule configuration discoverable and retain host authorization/errors.
4. Restore missing project actions with explicit permissions and confirmation.
5. Exercise dialogs, empty/error/paginated states in both themes and narrow widths.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `frontend/backstage/plugins/backstage-apme/src/components/` | Modify | Controls, rule configuration and project actions |
| `frontend/backstage/tests/` | Modify | Meaningful mutation/permission regressions |
| `.sdlc/research/` | Create | Review inventory and remaining acceptance evidence |

## Verification

- Frontend format/types/unit and both packed consumers through tox.
- `tox -e lint`, `tox -e unit`; Local image and browser controls review.
- CI passes on the pushed commit; real two-host acceptance remains in #833.

## Acceptance Criteria Reference

- AC2: repository and rule functionality remains available.
- AC3: explicit host permissions; no host detection or transport fallback.
- AC4: identify remaining coverage gaps before native UI retirement.

## Results

- Frontend format/types and 115 tests passed; native/shared workflow types and
  270 tests passed. Both packed-consumer checks passed.
- `tox -e unit`: 4,976 passed, 50 skipped, 85.58% coverage, using native OPA
  and local PostgreSQL. Repository lint verified through tox.
- Local image built and browser-reviewed in light/dark at 1440/800 px, including
  ordinary controls, dialogs, disabled/paginated/error states, rule writes,
  confirmed project deletion, SBOM and graph/tree alternatives with fixture data.
- [Audit inventory](../../../research/apme-local-controls-and-parity-audit.md)
  records functionality gaps explicitly added to #833. Native retirement and live
  two-host acceptance remain open.
