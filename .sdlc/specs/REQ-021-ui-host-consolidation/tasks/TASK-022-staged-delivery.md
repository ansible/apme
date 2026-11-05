# TASK-022: Split UI Consolidation into Safe Merge Boundaries

## Parent Requirement

[REQ-021](../requirement.md); ADR-066, ADR-073, ADR-074. Refs #833.

## Status

Complete

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
- Stage 3: fresh install, builds/types, 128 frontend tests (including upload
  authorization/lifecycle), 270 native/workflow tests, four-artifact consumer
  checks, harness build, repository lint and 4,976 Python tests passed
  (50 skipped). Upload UI and backend authorization ship together.
- Stage 4: 486 Portal backend/common tests and 519 Portal frontend tests passed;
  both packed consumers, 128 shared frontend tests, 270 native/workflow tests,
  fresh root install, repository lint and 4,976 Python tests passed (50 skipped).
  Portal source provenance and static NFS integration are preserved. Review
  hardening adds host authorization to inherited project and repository routes,
  constrains SCM branch lookup to configured HTTPS origins without redirects,
  safely encodes Gateway path identifiers, streams operation responses with
  cancellation, scopes catalog sync by SCM provider and host, and propagates
  catalog read failures. Regression coverage includes router authorization,
  traversal and streaming, branch origin handling, sync failure recovery,
  activity query dismissal, and packed-build path resolution.
- Stage 5: fresh install, 132 frontend tests, 270 native/workflow tests, both
  packed consumers, repository lint and 4,976 Python tests passed (50 skipped).
  The Local image built; installed-image browser checks with fixture data passed
  workflow Next/Dismiss/re-entry, explicit new checks, both graphs, dependency
  placement, version dropdown, severity order, cached Settings visits and rule
  enable/severity/reset writes, including an 800px layout.
- Git-object audit verified all five workspace locks, unchanged backend/proto
  contracts, CI on stack bases, and default Local activation only in stage 5.
  The reviewed UI and dependency resolution match #832. Review fixes harden
  upload quota concurrency and dedicated Portal deletion authorization.

## Merge maintenance

Keep successor PRs as drafts until their predecessor is merged. Each PR targets
its predecessor to show only its own changes. After a squash/rebase merge,
rebase the successor onto updated main while dropping the old predecessor
commits, then rebase the remaining descendants in order. Retarget the newly
unblocked PR to main and rerun its CI before merging. Do not merge successors
into their feature-branch bases. No production Portal dependency is switched
until an APME release is available. The original #832 review history and branch
are preserved; the split does not resolve disputed review threads.

## Review convergence and activation gate

Five independent review passes verified the split and re-reviewed both inherited
security/resource fixes. No split-specific blockers remain. Portal direct project
deletion now denies missing/denied host policy; concurrent upload creation checks
capacity after host settings resolve. Dedicated regressions cover both changes.

The final Local activation PR remains draft and blocked on predecessor merges
and #833 Local replacement acceptance, including durable non-project CLI/upload
history. This split does not waive those gaps or retire the native UI. Release
retry under a changed Node build version may intentionally fail checksum checks
because manifest provenance changes; refusing changed immutable bytes is retained.
