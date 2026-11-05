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

## Squash/rebase merges

Record each old parent tip before updating it. After its merge, use
`git rebase --onto origin/main <old-parent-tip> <child-branch>` to move only the
child's commits. Rebase later descendants onto their updated parent, using the
respective old parent tips. Push rebased branches with `--force-with-lease`,
retarget the newly unblocked PR to main and rerun its checks. A plain rebase can
replay an old parent's commits after squash; do not use it for that transition.

## Verified merge boundaries

- Stage 1: clean root/native installs, package builds, 50 shared/client tests,
  270 native/workflow tests, isolated packed-consumer declaration/browser checks
  and native Docker build stage passed. The default Podman UI Dockerfile
  (`containers/ui/Dockerfile`) copies the `@apme/client` manifest before `npm ci`.
- Stage 2: fresh install, builds/types, 116 frontend tests, 270 native/workflow
  tests, packed-consumer checks, development harness build, repository lint and
  4,976 Python tests passed (50 skipped). No default Local deployment change.
