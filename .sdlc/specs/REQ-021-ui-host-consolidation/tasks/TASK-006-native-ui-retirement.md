# TASK-006: Remove Native UI After Replacement Acceptance

## Parent Requirement

[REQ-021](../requirement.md), AC4.

## Status

Pending

## Description

Remove the native SPA and its build/deployment artifacts once the replacement
works in both hosts. No migration or support window is required.

## Prerequisites

TASK-004 feature coverage and TASK-005 actual-host acceptance.

## Implementation Notes

Remove native shell/pages, its transitional lockfile and UI-specific image,
Podman, bootc, release and documentation references. Retain shared packages,
Gateway data/identity/API, engine topology and CLI. Coordinate UI consumer changes
in the external Operator; do not add Helm. Remove transitional React type/source
aliases and obsolete design-system dependencies after their final consumers go.

## Files to Create/Modify

`frontend/`, `containers/ui/`, Podman/bootc definitions, CI/docs and external
Operator deployment references as needed.

## Verification

New-host acceptance passes and no required feature depends on native code. Build
and deployment checks confirm backend and CLI operation after image removal.

## Acceptance Criteria Reference

AC4 replacement and removal without a required migration path.
