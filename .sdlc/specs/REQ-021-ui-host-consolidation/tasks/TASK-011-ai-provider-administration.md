# TASK-011: Ai Provider Administration

## Parent Requirement

[REQ-021](../requirement.md), AC2–AC5.

## Status

Complete

## Description

Shared AI provider administration using the original plugin contracts.

## Prerequisites

TASK-001 through TASK-003.

## Implementation Notes

1. Reuse provider normalization/merge from the original Portal plugin.
2. Use current Gateway admin routes, write-only keys and partial-failure reporting.
3. Expose provider/model management behind explicit host settings permissions.

## Files to Create/Modify

Shared client/workflow, Backstage components and host integration contracts.

## Verification

Type checks, browser/native/package builds and repository lint via tox.
Host permission, reconnect, keyboard/theme and real-engine acceptance remain
required before native retirement.

## Acceptance Criteria Reference

AC2 existing workflow, AC3 host authorization, AC4 feature coverage, AC5 resources.

## Implementation Evidence

2026-10-30: implementation, focused transport/provider/UI checks, clean tarball
installation, public declarations, Node backend import and browser bundles pass.
The native regression suite passes 270 tests. Host/interaction tests cover denied
access, owner-bound resume, pasted-file protocol and read-only deployment
providers. Actual RHDH/Portal runtime acceptance remains TASK-005, awaiting
running environments and release artifact installation.
