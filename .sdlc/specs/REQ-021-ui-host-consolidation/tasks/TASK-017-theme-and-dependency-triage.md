# TASK-017: Theme Controls, Settings Layout and Dependency Triage

## Parent Requirement

[REQ-021](../requirement.md), AC2–AC3; ADR-043, ADR-051, ADR-055 and ADR-073.

## Status

Complete

## Description

Use the original upstream APME table surfaces and blue links, expose the Local
host's persisted Light/Dark/System preference in its sidebar, and replace the
clipped Galaxy table with wrapping server cards. Render compact history links
with full run identities available accessibly and on hover.

Load dependency findings through the existing paginated Gateway endpoint, show
complete per-dependency open/acknowledged counts, and offer project-scoped
acknowledgment behind the host's explicit `canAcknowledgeFindings` capability.
Confirm the broader rule scope when findings have no task YAML, matching ADR-055.
Keep inventory visible when findings fail, and abort pagination on unmount or
adapter/project changes. No new backend routes or fingerprint modes.

## Verification

- Frontend format/types, existing regression suite, dependency triage and Local
  theme-control regressions.
- Packed standalone/Portal consumer type, CSS and browser checks.
- Local image build and browser checks using fixture data with long server URLs,
  long run IDs, dependency issues and acknowledgments, in light/dark modes and at
  narrow widths.
- Repository lint and Python unit suite through tox.

## Results

- Frontend format/types and all 100 regression tests passed, including complete
  finding pagination, denied writes, acknowledgment scope and adapter/project
  changes. Standalone and Portal packed-consumer checks passed.
- Local image built successfully. Browser checks with fixture data confirmed
  persisted Light/Dark/System selection, wrapping Galaxy URLs at 1440/800 px,
  compact history links and confirmed acknowledgment with refreshed counts.
- Repository lint passed; Python unit suite: 4,976 passed, 50 skipped,
  85.58% coverage. Live two-host Gateway/Engine acceptance remains in #833.
