# TASK-008: Shared Rules and Health

## Parent Requirement

[REQ-021](../requirement.md), AC2–AC4; domain family from TASK-004.

## Status

In Progress

## Description

Expose the existing rule catalog, overrides and system health in reusable BUI
views and the local workbench. Preserve the original Portal transport and RBAC.

## Prerequisites

TASK-001 through TASK-003 complete.

## Implementation Notes

1. Use the existing typed client for health and rule writes; retain numeric
   severity values and partial override updates.
2. Normalize Portal's explicitly different rule envelope/field names in its
   adapter. Do not request missing detail/stats endpoints or fall back to Gateway.
3. Search, filter and page the existing full catalog locally; mount views only
   when selected. Detail selection uses that catalog without a duplicate fetch.
4. Hosts opt into rule editing after evaluating their permissions. Backend
   authorization remains authoritative; show denied/failed writes visibly.
5. Add host-owned navigation and public exports; document unavailable Portal
   metadata and actual-host acceptance limits.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `frontend/backstage/plugins/backstage-apme/src/components/` | Extend | Rules, health and lazy workbench navigation |
| `frontend/backstage/plugins/backstage-apme/src/api/` | Extend | Portal catalog normalization |
| `frontend/backstage/plugins/backstage-apme/src/index.ts` | Extend | Public component contracts |
| `frontend/backstage/README.md` | Extend | Host permission integration |

## Verification

- [x] `tox -e frontend-format,frontend-lint,frontend-dev-build,frontend-pack`
- [x] `git diff --check`
- [x] `tox -e lint`

Interaction tests and real-host RBAC acceptance remain outstanding; this task's
verification records only the checks actually run.

## Acceptance Criteria Reference

AC2 shared behavior, AC3 host boundaries, AC4 rules/health coverage.

## Implementation Evidence

2026-10-30: reusable rules/health exports and lazy workbench navigation implemented.
Rules use the existing client and partial writes; embedded views default to
read-only, Local opts into management and Portal owns its permission decision.
Portal catalog normalization preserves its returned labels and leaves lossy
metadata unspecified. Type checking, formatting, browser build and three-package
generation passed. Interaction tests were not added or run for this family;
permission-denial, keyboard/theme and real-host acceptance remain outstanding.
