# ADR-028: React 18 + PatternFly 6 for Frontend

## Status

Accepted

## Date

2026-03-19

## Context

APME needs a web UI for browsing scan results, managing remediation, and tracking ROI. The DESIGN_DASHBOARD.md initially recommended Vue 3 + TypeScript, but the HTML mockups (docs/mockups/) were implemented using PatternFly 6 components targeting React 18. Additionally, a Backstage plugin was identified as a requirement.

- PatternFly is Red Hat's open-source design system, native to React
- Backstage is built on React; a Vue-based UI cannot share components with a Backstage plugin
- AAP (Ansible Automation Platform) uses `@ansible/ansible-ui-framework`, which wraps PatternFly React components
- The existing mockups already reference PatternFly 6 component names (`PageLayout`, `PageTable`, `PageDashboardCount`)

## Decision

**We will use React 18 + TypeScript 5 + PatternFly 6 for all APME frontend development.**

This supersedes the Vue 3 recommendation in DESIGN_DASHBOARD.md.

## Alternatives Considered

### Alternative 1: Vue 3 + TypeScript

**Description**: Use Vue 3 with Composition API and PrimeVue/Vuetify for components.

**Pros**:
- Lighter framework, faster initial development
- Good DX with Composition API

**Cons**:
- Cannot share components with Backstage (React-only)
- PatternFly has no official Vue port
- AAP ecosystem is React-based; Vue creates friction for contributors

**Why not chosen**: Sharing code between the standalone app and Backstage plugin requires React. Maintaining two component libraries (Vue + React) doubles the UI maintenance burden.

### Alternative 2: HTMX + Server-Rendered

**Description**: Server-rendered HTML with HTMX for progressive enhancement.

**Pros**:
- No build step, minimal JavaScript
- Simple deployment

**Cons**:
- Limited interactivity for complex views (diff viewer, real-time scan progress)
- No Backstage integration possible
- No component reuse

**Why not chosen**: The dashboard requires rich interactivity (WebSocket streams, diff viewers, filterable tables) that HTMX cannot support well.

## Consequences

### Positive

- Single component library shared between standalone app and Backstage plugin
- Aligned with Red Hat/AAP ecosystem (PatternFly, ansible-ui-framework)
- Large React ecosystem for charting, tables, and other complex UI needs

### Negative

- Heavier toolchain than Vue 3 (more boilerplate, JSX verbosity)
- Node.js build step required

### Neutral

- The DESIGN_DASHBOARD.md will be updated to reflect this decision

## Implementation Notes

- Use Vite for bundling (not CRA or webpack)
- Organize as a pnpm monorepo: `ui/shared/`, `ui/standalone/`, `ui/backstage-plugin/`
- Generate TypeScript API client from the gateway's OpenAPI spec
- Use TanStack Query (React Query) for server state management

## Related Decisions

- ADR-001: gRPC communication (gateway translates to REST)
- ADR-012: Scale pods not services (gateway is extractable)
- ADR-029: Dual-UI strategy

## References

- [PatternFly 6](https://www.patternfly.org/)
- [DESIGN_DASHBOARD.md](../../docs/DESIGN_DASHBOARD.md)
- [Mockups](../../docs/mockups/README.md)

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-19 | AI Agent | Accepted — React chosen for Backstage compatibility |
