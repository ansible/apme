# TASK-021: Project Page Polish

## Parent Requirement

[REQ-021](../requirement.md), AC2–AC5; ADR-043 and ADR-073.

## Status

Complete

## Description

Place the open dependency diagram after dependency inventory and requirements.
Replace the advanced scan target text field with a dropdown using Portal's
existing Ansible Core minor choices. Preserve the Engine default and any
preselected version. Sort project severity counts in ADR-043 order, omitting
zero counts.

## Verification

Verify selected/default scan request options and unordered severity summaries
through frontend regression tests. Run frontend/native types and tests, packed
consumer checks, repository checks and the Local image/browser checks via tox.
Keep live Gateway/Engine and two-host acceptance tracked in #833.

## Results

- Frontend format/types and 131 tests passed, including selected/default scan
  request options and mixed/zero severity summaries. Native/shared types and
  270 tests passed. Both packed consumers passed.
- Local image built successfully. Browser checks using fixture data verified
  the dropdown, the requested severity sequence, the expanded 9-node diagram
  below both inventory tables and requirements, and the 800px layout.
  An existing browser cache initially served previous assets; clearing it
  loaded the new image's UI. Hard refresh after a rebuild if needed.
- Repository lint passed. Python suite: 4,976 passed, 50 skipped, 85.58% coverage. Live two-host
  acceptance remains tracked in #833.
