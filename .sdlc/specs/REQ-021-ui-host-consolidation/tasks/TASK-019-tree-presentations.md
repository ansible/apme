# TASK-019: Expanded Dependency and Content Trees

## Parent Requirement

[REQ-021](../requirement.md), AC2–AC4; ADR-073.

## Status

Complete

## Description

Open the dependency tree by default. Reuse its lightweight text presentation for
a content graph tree, preserving the existing diagram and node details as
alternatives. Handle shared nodes, disconnected components and cycles safely.

## Prerequisites

- TASK-017 complete.

## Implementation Notes

1. Reuse a scrollable, selectable monospace tree surface in both views.
2. Build the content hierarchy from existing graph containment edges; explicitly
   distinguish shared/cyclic references and preserve non-hierarchical edges.
3. Keep the D3/Dagre diagram lazy and accessible node details available.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `frontend/backstage/plugins/backstage-apme/src/components/` | Modify | Shared tree surface, graph view and expanded dependency disclosure |
| `frontend/backstage/tests/` | Create | Tree construction and presentation regressions |

## Verification

- Frontend checks and packed consumers through tox.
- Browser checks of both trees, diagram and node list at desktop/narrow widths.

## Acceptance Criteria Reference

- AC2/AC4: preserve complete project graph and dependency access.
- AC5: no new rendering dependencies; diagrams remain lazy.

## Results

- Frontend format/types and 115 tests passed; native/shared workflow types and
  270 tests passed. Both packed-consumer checks passed.
- `tox -e unit`: 4,976 passed, 50 skipped, 85.58% coverage, using native OPA
  and local PostgreSQL. Repository lint verified through tox.
- Local image built and browser-reviewed in light/dark at 1440/800 px, including
  ordinary controls, dialogs, disabled/paginated/error states, rule writes,
  confirmed project deletion, SBOM and graph/tree alternatives with fixture data.
- [Audit inventory](../../../research/apme-local-controls-and-parity-audit.md)
  records functionality gaps explicitly added to #833. Native retirement and live
  two-host acceptance remain open.
