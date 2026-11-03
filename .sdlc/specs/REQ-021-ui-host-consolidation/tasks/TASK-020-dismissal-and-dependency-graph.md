# TASK-020: Workflow Dismissal and Dependency Diagram

## Parent Requirement

[REQ-021](../requirement.md), AC2–AC5; ADR-052, ADR-065 and ADR-073.

## Status

Complete

## Description

Respect deliberate workflow dismissal when leaving/reopening a project. Discover
resumable operations through the Gateway and offer explicit Resume instead of
reattaching every project visit to a retained completed snapshot. Starting or
resuming explicitly attaches; dismissal detaches without changing Gateway state.

Replace the default dependency text disclosure with an always-open interactive
D3/Dagre diagram using the content graph renderer. Parse the existing uv tree,
preserve shared/cyclic references, and retain raw text as an alternative. Do not
invent relationships from a flat inventory. Restore Diagram as the content graph
default and label back-navigation All projects.

## Verification

- Regression tests through tox: dismissal/remount, explicit resume, new operation,
  denied discovery and dependency parsing/rendering.
- Frontend/native tests, types, packed consumers, lint, Local image/browser checks.
- CI on pushed commit; live two-host acceptance remains tracked in #833.

## Settings assets

Load Local theme and shared layout CSS with the plugin entry rather than its
lazy Settings route. Asset-load failures offer an explicit Reload APME action;
application errors remain visible. Verify Settings and inline rule controls in
the installed image with browser caching enabled.

## Results

- 127 frontend regressions passed, including real controller remount/dismissal,
  explicit resume/start, denied discovery/retry, dependency parsing and asset
  recovery. Native/shared workflow types and 270 tests passed.
- Both packed consumers passed. Local image built successfully. Browser checks
  with caching enabled verified Next → Dismiss → All projects → reopen, explicit
  new checks, 9-node/8-edge dependency diagram, text fallback, fit/zoom, narrow
  layout, repeated Settings visits without a lazy stylesheet request, and rule
  enable/severity/reset writes using disposable fixture data.
- Repository lint passed; Python suite: 4,976 passed, 50 skipped, 85.58% coverage. #833 remains open for
  actual Gateway/Engine acceptance and outstanding replacement functionality.
