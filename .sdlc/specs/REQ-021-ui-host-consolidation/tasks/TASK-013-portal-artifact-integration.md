# TASK-013: APME-owned Portal Integration Artifacts

## Parent Requirement

[REQ-021](../requirement.md), AC1–AC5; [ADR-073](../../../adrs/ADR-073-ui-host-composition.md).

## Status

Complete

## Description

Transfer the original Portal guest integration into APME-owned release artifacts
and make the standalone frontend consumable through NFS. Preserve catalog sync,
ownership, entitlements, settings permissions and repository lifecycle behavior.

## Prerequisites

- TASK-001 through TASK-004; explicit implementation approval in TASK-005.

## Implementation Notes

1. Add the standard `./alpha` API/page declaration and config schema; use it in Local.
2. Port original common/frontend/catalog/scaffolder production sources into an
   isolated Portal integration tree, preserving provenance and existing tests.
3. Ship two Portal-only adapters with SDK/AAP peers and same-release workflow
   dependencies, in addition to the four standalone artifacts. Validate against
   the host's real SDK, not fabricated interfaces or stubs.
4. Add settings get/save operations with JSON schemas, audit metadata and RBAC,
   preserving existing HTTP paths and validation. Complete descriptor entitlement,
   content-type, config and applicability gates and preserve scaffolder workflow.
5. Prepare a deterministic Portal adoption patch using actual packed tarballs
   for local verification and immutable release URLs for the eventual switch.
   Do not publish a release or delete the downstream workspace before acceptance.

## Files to Create/Modify

- `frontend/backstage/`: standalone alpha, backend package name, packaging.
- `frontend/portal/`: APME-owned Portal adapters, build/pack/adoption tooling.
- `tox.ini`, CI and documentation: reproducible separate host integration build.

## Verification

- Standalone build/type/package checks and dependency isolation through tox.
- Portal adapter type/declaration/browser/backend builds against the actual SDK.
- Clean packed-artifact import/bundle checks; no workspace paths in manifests.
- Portal composition type check after applying the prepared adoption patch.
- Repository lint. Actual multiuser and Gateway/Engine acceptance stays TASK-005.

## Acceptance Criteria Reference

AC1 portable artifacts; AC2 shared workflow; AC3 host authorization;
AC4 original Portal feature preservation; AC5 immutable release packaging.

## Completion Evidence

2026-11-02:

- Standard type/format checks, repository lint and 75 host/workflow tests pass.
- The transferred Portal backend/common suite passes 459 tests; the frontend
  suite passes 517 tests, including native discovery and catalog entitlement gates.
- `tox -e portal-pack-smoke` installs all six packed artifacts outside both
  checkouts and passes browser bundling, backend feature loading and declaration
  consumption. Standalone artifacts contain no Portal/AAP dependencies.
- A generated adoption patch applied to an isolated Portal worktree. After removing
  `workspaces/apme` and its root workspace/TypeScript globs, Yarn installed the
  actual tarballs and both the host TypeScript check and app production build pass.
  Portal's unrelated existing peer warnings remain; the new SDK peer requirements
  are declared explicitly by the adapters and the generated host dependency patch.
- The clean Local RHDH image builds with the public alpha API and renamed backend
  package. Chromium inspection confirms the APME brand, Projects/Settings sidebar
  and Projects page. No Gateway/Engine was connected to this preview.

The generated patch is preparation, not a published release or production switch.
Keep TASK-005 open for actual multiuser authorization, Gateway/Engine/SCM workflows,
uploads, settings administration and resource acceptance. Portal's runtime mounting,
settings/nav blueprints and installed-plugin discovery are host contract gaps;
this integration uses supported static composition and existing host surfaces.
