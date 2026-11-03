# APME shared UI packages — delivery stage 1/5

This stack splits PR #832. See
[TASK-022](../../.sdlc/specs/REQ-021-ui-host-consolidation/tasks/TASK-022-staged-delivery.md)
for merge order. The default Local deployment still uses the native UI.

Run `tox -e frontend-ci,frontend-build,frontend-lint,frontend-unit,frontend-pack-smoke`
for the packages present at this stage, and `tox -e ui-workflow-pack` for native
compatibility. Release builds pin all APME dependencies to artifacts of the same
product release. Portal adoption follows publication and runtime acceptance (#833).
