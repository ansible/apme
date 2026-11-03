# TASK-022: Split UI Consolidation into Safe Merge Boundaries

## Parent Requirement

[REQ-021](../requirement.md); ADR-066, ADR-073, ADR-074. Refs #833.

## Status

In Progress

## Description

Deliver the reviewed implementation from PR #832 as five ordered PRs. Preserve
its final implementation and review fixes, with executable intermediate states.

## Prerequisites

The next PR targets its predecessor. Merge in order, retargeting/rebasing onto
main after the predecessor merges. Never merge a later PR ahead of its dependencies.

## Implementation Notes

1. Shared client/workflow extraction, native compatibility exports and immutable
   release delivery for the client and workflow together.
2. Shared Backstage project/data/settings frontend and its development harness.
3. Host-authorized uploads, backend session channel and AI administration UI.
4. Portal-owned adapters/source transfer, SDK checks and six-artifact delivery.
5. Local image/navigation/theme and the default deployment switch.

Existing Local deployment stays on the native UI through the first four PRs.
Portal adoption follows an APME release; its old workspace remains until acceptance.

## Verification

Each boundary must install/build/test independently with its ancestors. CI also
runs on PRs targeting feat/ui-split-* branches. Regenerate locks from the reviewed
resolution for each manifest; enable no job before its sources exist. Audit the
last tree against #832. Live Gateway/Engine acceptance remains in #833.

## Verified merge boundaries

- Stage 1: clean root/native installs, package builds, 50 shared/client tests,
  270 native/workflow tests, isolated packed-consumer declaration/browser checks
  and native Docker build stage passed. Default Podman UI Dockerfile is unchanged.
