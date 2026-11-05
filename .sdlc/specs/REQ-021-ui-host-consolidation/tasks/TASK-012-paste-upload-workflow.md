# TASK-012: Paste Upload Workflow

## Parent Requirement

[REQ-021](../requirement.md), AC2–AC5.

## Status

Complete

## Description

Shared paste/upload workflow using the current FixSession hook.

## Prerequisites

TASK-001 through TASK-003.

## Implementation Notes

1. Reuse the existing session hook and proposal/results renderers.
2. Inject an explicit host socket factory; deny upload when unavailable.
3. Provide paste/files/directory input, reconnect, approvals and remediated-file downloads.

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
