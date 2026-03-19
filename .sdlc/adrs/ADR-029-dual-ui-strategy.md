# ADR-029: Dual-UI Strategy (Standalone + Backstage Plugin)

## Status

Accepted

## Date

2026-03-19

## Context

APME needs to serve two deployment models:

1. **Standalone**: Teams running APME independently need a full-featured web dashboard without external dependencies.
2. **Backstage integration**: Enterprise teams using Backstage as their developer portal want APME capabilities embedded in their existing portal — including entity-level scan cards, full scan management pages, and remediation queues.

Both UIs consume the same REST API (the FastAPI gateway on port 50050). The question is how to structure the frontend code to avoid duplication.

## Decision

**We will build two UI surfaces — a standalone PatternFly SPA and a full-featured Backstage plugin — sharing a common code package (`@apme/ui-shared`).**

The shared package contains:
- TypeScript API types (generated from OpenAPI)
- API client (`ApmeApiClient`)
- React hooks (TanStack Query wrappers)
- PatternFly presentational components (SeverityBadge, ViolationRow, DiagnosticsPanel, DiffViewer, MetricCard)
- Utility functions (formatting, color mapping)

The standalone app imports these directly. The Backstage plugin wraps them with Backstage extension points (API factory, route refs, `Content`/`Header` layout).

## Alternatives Considered

### Alternative 1: Backstage-Only

**Description**: Build only the Backstage plugin; teams without Backstage use the CLI.

**Pros**:
- Single codebase, no standalone app to maintain

**Cons**:
- Requires Backstage deployment, which is heavy for small teams
- CLI alone is insufficient for enterprise findings management
- Not all organizations use Backstage

**Why not chosen**: Many APME users will not have Backstage. The standalone app serves a different deployment audience.

### Alternative 2: Standalone-Only

**Description**: Build only the standalone app; Backstage users iframe it.

**Pros**:
- Single codebase

**Cons**:
- Iframing breaks Backstage UX patterns (nav, auth, theme)
- No entity-level integration (catalog cards)
- Poor user experience in the portal

**Why not chosen**: Backstage users expect native plugin integration, not iframes.

### Alternative 3: No Shared Code

**Description**: Build both UIs independently.

**Pros**:
- Full independence, no coordination needed

**Cons**:
- All components, hooks, and types duplicated
- Bug fixes must be applied twice
- UI divergence over time

**Why not chosen**: The shared package avoids duplication with minimal coupling overhead.

## Consequences

### Positive

- Both deployment models fully supported
- 60-70% of UI code is shared (types, hooks, components)
- Bug fixes and feature additions propagate to both UIs
- Backstage plugin gets native integration (entity cards, auth forwarding, nav)

### Negative

- Three packages to maintain (shared, standalone, backstage-plugin)
- Monorepo tooling adds complexity (pnpm workspaces)
- Shared components must work in both PatternFly-only (standalone) and Backstage-themed contexts

### Neutral

- Both UIs are versioned together in the monorepo
- The gateway API is the contract; UIs are independent consumers

## Implementation Notes

- Monorepo structure: `ui/shared/`, `ui/standalone/`, `ui/backstage-plugin/`
- Use pnpm workspaces for dependency management
- The Backstage plugin has two sub-packages: `apme` (frontend) and `apme-backend` (proxy)
- The backend proxy forwards `/api/apme/*` to the gateway, enabling Backstage auth forwarding
- Entity annotation `apme.io/project-name` links catalog items to scan history

## Related Decisions

- ADR-028: React + PatternFly (enables component sharing)
- ADR-001: gRPC communication (gateway translates to REST)
- ADR-012: Scale pods not services

## References

- [Backstage Plugin Development](https://backstage.io/docs/plugins/)
- [DESIGN_DASHBOARD.md](../../docs/DESIGN_DASHBOARD.md)
- [UI Build Plan](.cursor/plans/apme_ui_build_plan.plan.md)

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-19 | AI Agent | Accepted — dual-UI with shared package |
