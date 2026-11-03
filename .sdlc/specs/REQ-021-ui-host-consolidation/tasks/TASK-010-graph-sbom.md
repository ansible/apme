# TASK-010: Shared Content Graph and SBOM

## Parent Requirement

[REQ-021](../requirement.md), AC2–AC5; domain family from TASK-004.

## Status

In Progress

## Description

Move the existing native graph renderer into the shared presentation package and
expose a lazy project graph view and user-requested CycloneDX SBOM download.

## Prerequisites

TASK-001 through TASK-003 complete.

## Implementation Notes

1. Preserve one graph renderer, with a separate graph export so controllers and
   ordinary workflow imports do not load D3/Dagre. Retain the native re-export.
2. Keep specialist SVG styling scoped, use per-instance marker IDs and release
   D3 listeners/transitions on unmount. Provide keyboard-readable node details.
3. Use the existing client and host transport for graph/SBOM requests. Do not
   fall back when Portal lacks a route or denies access.
4. Fetch SBOM only on explicit download, sanitize its local filename and release
   browser object URLs. Show failures without pretending an absent graph is empty.
5. Document Portal route requirements and package/build evidence. Actual-host
   browser, accessibility, theme and authorization acceptance remains TASK-005.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `frontend/packages/ui-workflow/src/graph/` | Create | Existing renderer and isolated export |
| `frontend/src/components/GraphVisualization.tsx` | Replace | Native compatibility export |
| `frontend/backstage/plugins/backstage-apme/src/components/` | Extend | Shared graph view and SBOM control |
| Workspace manifests and locks | Extend | Existing graph dependency versions |
| Host docs and TASK-005 | Extend | Portal route/capability requirements |

## Verification

- [x] Type checking, formatting, browser/native build and packages through tox
- [x] Repository lint through tox
- [x] `git diff --check`

No interaction tests are added or run as part of this task. Runtime acceptance
and complete feature replacement remain outstanding.

## Acceptance Criteria Reference

AC2 shared behavior, AC3 host transport, AC4 graph/SBOM, AC5 deferred work.

## Implementation Evidence

2026-10-30: existing native renderer extracted to the isolated graph export;
native now re-exports it. Shared graph tab includes searchable/paged node details,
disclosed YAML/edges and lazy diagram loading. Diagram markers are per-instance,
SVG styles scoped and D3 listeners/transitions cleaned up. SBOM preparation uses
the host client only on user request, with a safe filename and object URL cleanup.
Adapter changes invalidate pending mutation callbacks and prepared downloads.
Portal's absent graph/SBOM routes are recorded in TASK-005; no fallback added.
Clean root installation, frontend/native builds, type/format checks, packages and
repository lint passed. No new interaction tests were run, and real-host theme,
keyboard, transport and resource acceptance remain outstanding.
