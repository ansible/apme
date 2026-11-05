# APME shared UI packages — delivery stage 4/5

This stack splits PR #832. See
[TASK-022](../../.sdlc/specs/REQ-021-ui-host-consolidation/tasks/TASK-022-staged-delivery.md)
for merge order. The default Local deployment still uses the native UI.

Run `tox -e frontend-ci,frontend-build,frontend-lint,frontend-unit,frontend-pack-smoke,frontend-release-pack-smoke`
for the packages present at this stage, and `tox -e ui-workflow-pack` for native
compatibility. Release builds pin all APME dependencies to artifacts of the same
product release. Portal adoption follows publication and runtime acceptance (#833).

The ESLint dependency override keeps `minimatch` resolved by ESLint,
`@eslint/config-array`, and `@typescript-eslint/typescript-estree` on the patched
11.x line. `check-eslint-minimatch.mjs` checks those lockfile resolutions;
update it together with the override if the ESLint toolchain changes. Other
packages keep their own `minimatch` requirements. The 10.x line has no fix for
[GHSA-g3hv-75wg-m249](https://github.com/isaacs/minimatch/security/advisories/GHSA-g3hv-75wg-m249).

ESLint covers APME-owned TypeScript and TSX. The only source exclusion is the
exact `frontend/vendor/ansible-ui-framework/` snapshot, an upstream Ansible UI
dependency with its own lint conventions; the ignore is not applied to other
vendor directories.
