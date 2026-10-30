# TASK-003: Immutable Artifacts and CI

## Parent Requirement

[REQ-021](../requirement.md), AC1 and AC5.

## Status

Complete

## Description

Extend the existing product-release packaging to the client and standard plugin,
and prove external installation without source/Portal dependencies.

## Prerequisites

TASK-001 and TASK-002.

## Implementation Notes

1. Build/stage versioned tarballs, licenses, checksums and artifact manifest.
2. Pin same-release dependencies without rewriting source manifests.
3. Verify a clean install, Node client import and browser bundle outside checkout.
4. Add tox environments and CI checks; extend the existing release publisher.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `frontend/backstage/scripts/` | Create | Stage/verify tarballs |
| `tox.ini` | Modify | Sole orchestration entry points |
| `.github/workflows/test.yml` | Modify | Standard package checks |
| `.github/workflows/ui-workflow-release.yml` | Modify | Product-release artifacts |

## Verification

- [x] `tox -e frontend-pack-smoke`
- [x] `tox -e frontend-pack -- --version 0.0.0-test --output dist/ui-release-check`
- [x] Artifact manifests contain release URLs, never workspace/file dependencies.
- [x] `tox -e lint`

## Acceptance Criteria Reference

AC1 independently consumable packages; AC5 release contract. Running this task
does not publish or modify a GitHub release.

## Completion Evidence

2026-10-30: repository lint passed; 270 native tests and 47 host/workflow tests
passed; host type checks and development build passed. Clean tarball installation,
Node import, public declaration type check and browser bundle passed outside the
checkout. Real RHDH/Portal runtime acceptance remains TASK-005.
