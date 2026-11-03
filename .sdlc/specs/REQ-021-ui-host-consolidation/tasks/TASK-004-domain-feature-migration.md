# TASK-004: Complete Shared Domain Surfaces

## Parent Requirement

[REQ-021](../requirement.md), AC4.

## Status

In Progress

## Description

Port remaining native and existing plugin domain surfaces into APME-owned shared
packages. Split into page-family tasks as each family begins.

Project history/findings/dependencies are tracked in
[TASK-007](TASK-007-project-data-surfaces.md).

Rules and health are tracked in [TASK-008](TASK-008-rules-and-health.md).

Model preferences and Galaxy settings are tracked in
[TASK-009](TASK-009-model-galaxy-settings.md).

Content graph and SBOM are tracked in [TASK-010](TASK-010-graph-sbom.md).

Provider administration and paste/upload are tracked in TASK-011 and TASK-012.
The optional authenticated upload transport is specified in ADR-074.

## Prerequisites

TASK-001 through TASK-003.

## Implementation Notes

Reuse current API/client and UI logic for history/findings/dependencies, rules,
health, AI/Galaxy settings, graph/SBOM and paste/upload. Use BUI ordinary controls;
document specialist presentation exceptions. Both hosts receive non-fleet
features. Inventory each native route and Portal journey before removal.

## Files to Create/Modify

Shared UI packages, current native pages, and standard plugin routes.

## Verification

`tox -e frontend-lint,frontend-unit,frontend-dev-build,frontend-pack-smoke` plus
feature-specific browser checks. Validate permission denial, error, empty,
keyboard/theme and reconnect behavior for each surface.

## Acceptance Criteria Reference

AC4 full non-fleet capability coverage; AC5 resource efficiency.
