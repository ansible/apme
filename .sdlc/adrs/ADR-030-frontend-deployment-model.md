# ADR-030: Frontend Deployment Model

## Status

Proposed

## Date

2026-03-19

## Context

ADR-029 defines a Python/FastAPI web gateway that exposes a REST + WebSocket API
for the APME dashboard. The gateway's API surface is frontend-agnostic by
design. This ADR decides how the **presentation layer** — the code that runs in
the browser — is built and deployed.

Two viable options exist, each targeting a different deployment scenario:

1. **Standalone SPA** — a self-contained PatternFly/React application served by
   the gateway container. No external platform dependency. Suitable for
   individual developers, small teams, and deployments without RHDH.

2. **RHDH/Backstage plugin** — APME as a plugin in Red Hat Developer Hub
   (Backstage). Inherits authentication, RBAC, and software catalog integration
   from the platform. Aligns with AAP's self-service automation portal, which
   is [built on Backstage](https://www.redhat.com/en/technologies/management/ansible/self-service-automation).

Both options consume the identical gateway API from ADR-029.

### Existing Work

- [design-dashboard.md](/.sdlc/context/design-dashboard.md) specifies
  PatternFly 5/6 components, React + TypeScript, and Vite as the build tool.
- [docs/mockups/](/docs/mockups/) contains HTML mockups (PatternFly 6, dark
  mode) with a Figma file for four key views: dashboard home, scan results,
  scan detail, and ROI metrics.
- The mockups use `@ansible/ansible-ui-framework` component patterns matching
  AAP's existing UI.

## Decision

**Document both deployment models as supported paths. Build the standalone SPA
first (Phase 4). Design the gateway API to support a future Backstage plugin
without changes.**

The standalone SPA is the initial development focus because it has zero external
dependencies, existing mockups, and a clear implementation path. The Backstage
plugin is the enterprise integration path for organizations that already run
RHDH.

### Option A: Standalone PatternFly/React SPA

A single-page application built with React + TypeScript + PatternFly, bundled
with Vite, and served as static files by the gateway container.

| Aspect | Detail |
|--------|--------|
| **Framework** | React 18 + TypeScript |
| **Design system** | PatternFly 6 (dark mode first) |
| **Build** | Vite |
| **Served by** | Gateway container (FastAPI `StaticFiles`) |
| **Auth** | None (standalone) or AAP Gateway headers (enterprise) |
| **Deployment** | Built into the gateway container image |
| **Charts** | PatternFly Charts (Victory-based) |
| **Diff viewer** | PatternFly `CodeEditor` in diff mode |

**Pros**:
- Zero external platform dependency
- Deployable anywhere (Podman, K8s, bare metal)
- Existing mockups and design spec ready
- Full control over UX and release cadence
- PatternFly ensures Red Hat visual consistency

**Cons**:
- Must build auth if multi-user access is needed (or use AAP Gateway)
- No software catalog integration out of the box
- Separate from other developer tools (not in a unified portal)

### Option B: RHDH/Backstage Plugin

APME surfaces as a plugin within a Red Hat Developer Hub (Backstage) instance.
The plugin renders APME views inside Backstage's shell and uses Backstage's
proxy to reach the gateway API.

| Aspect | Detail |
|--------|--------|
| **Framework** | Backstage plugin SDK (React + TypeScript) |
| **Design system** | PatternFly (Backstage uses Material UI by default; RHDH layers PatternFly) |
| **Auth** | Inherited from RHDH (SSO, LDAP, OIDC) |
| **RBAC** | Inherited from RHDH |
| **Software catalog** | APME scans linked to catalog entities (repos, components) |
| **Deployment** | Plugin installed in existing RHDH instance |

**Pros**:
- Authentication, authorization, and session management are handled by RHDH
- Scans can be linked to software catalog entities (repo → scan history)
- Unified developer portal alongside other tools (CI, docs, scaffolder)
- Aligns with AAP self-service automation portal architecture
- SSO/LDAP/OIDC out of the box

**Cons**:
- Requires an existing RHDH/Backstage instance (infrastructure prerequisite)
- Backstage plugin SDK adds constraints on routing, state, and layout
- Release cadence tied to RHDH compatibility
- Smaller team skill pool (Backstage plugin development vs. standard React)

### Backstage SCM Capabilities Are Not Used

Backstage has built-in SCM integration: it can clone repositories, create
branches, and open pull requests via its scaffolder and software templates.
APME **does not use these capabilities** for its file pipeline because:

1. **APME-specific file discovery**: The gateway must determine which files
   are Ansible content (playbooks, roles, collections) using the same discovery
   logic as the CLI. Backstage's generic clone produces a raw filesystem with
   no Ansible awareness.

2. **Protobuf chunking**: Files must be packaged into `ScanChunk` protobuf
   messages for the gRPC stream to Primary. This is APME-specific serialization
   that Backstage cannot provide.

3. **gRPC streaming**: The chunked files are streamed to Primary over
   `FixSession` or `ScanStream` gRPC RPCs. The gateway must hold the gRPC
   stream and correlate it with the WebSocket connection. This is the gateway's
   core responsibility.

4. **Patched file write-back**: After remediation, the gateway writes patched
   files back to disk and (optionally) creates a PR via the SCM provider's API.
   The gateway already has the patched file content and the auth token — routing
   through Backstage's scaffolder would add indirection for no benefit.

**What Backstage provides for APME**:
- Identity and RBAC (who can run scans, approve fixes)
- Software catalog integration (link scans to repos/components)
- Unified developer portal UX (APME alongside CI, docs, monitoring)
- SSO/LDAP/OIDC authentication

**What the gateway provides regardless of frontend**:
- Clone → discover → chunk → gRPC pipeline (file operations)
- WebSocket-to-FixSession bridge (HITL flow)
- Persistence (scan history, ROI metrics)
- PR creation (direct SCM API calls)

### Trade-Off Summary

| Dimension | Standalone SPA | Backstage Plugin |
|-----------|---------------|-----------------|
| **Auth** | None (standalone) or AAP Gateway | RHDH SSO/LDAP/OIDC |
| **Catalog integration** | None | Scans linked to catalog entities |
| **Deployment prerequisite** | None | Existing RHDH instance |
| **Developer skill set** | Standard React + PatternFly | Backstage plugin SDK |
| **Release independence** | Full | Tied to RHDH compatibility |
| **Multi-tool unification** | Separate app | Unified portal |
| **Time to V1** | Faster (mockups exist) | Slower (plugin SDK learning curve) |
| **SCM operations** | Gateway-owned | Gateway-owned (Backstage SCM unused) |

## Alternatives Considered

### Alternative 1: Vue 3 + Vuetify

**Description**: Vue.js SPA with Vuetify components instead of React + PatternFly.

**Pros**:
- Lighter framework
- Good developer experience

**Cons**:
- Not PatternFly — breaks Red Hat design consistency
- No alignment with AAP UI patterns
- Existing mockups are PatternFly-based

**Why not chosen**: PatternFly is mandatory for Red Hat product alignment.
Existing mockups and `@ansible/ansible-ui-framework` components target React.

### Alternative 2: Alpine.js + RHDS Web Components

**Description**: Lightweight static HTML with Alpine.js for interactivity and
Red Hat Design System (RHDS) Web Components for styling.

**Pros**:
- No build step
- Extremely lightweight
- RHDS provides Red Hat branding

**Cons**:
- RHDS has fewer enterprise components than PatternFly (no data tables,
  code editors, or chart library)
- Alpine.js state management is limited for complex HITL workflows
- Does not match AAP UI patterns

**Why not chosen**: Insufficient component library for the dashboard's
requirements (diff viewer, data tables, charts). PatternFly provides all of
these.

### Alternative 3: Backstage Only (No Standalone)

**Description**: Only support the Backstage plugin. No standalone SPA.

**Pros**:
- Single frontend to maintain
- Forces enterprise-grade deployment

**Cons**:
- Requires RHDH infrastructure for any use
- Blocks individual developers and small teams
- Significantly slower V1 (plugin SDK learning curve)

**Why not chosen**: The standalone SPA has zero prerequisites and covers the
majority of initial use cases. The Backstage plugin is an additive enterprise
path, not a replacement.

## Consequences

### Positive

- **Two supported paths** — standalone for simplicity, Backstage for enterprise
  integration. Users choose based on their infrastructure.
- **Gateway API is frontend-agnostic** — adding the Backstage plugin requires
  zero gateway changes. The REST + WebSocket surface serves both.
- **Existing design work is reusable** — PatternFly mockups, component mapping,
  and Figma designs apply to both the standalone SPA and the Backstage plugin
  (RHDH uses PatternFly).

### Negative

- **Two frontends to maintain** (eventually) — standalone SPA and Backstage
  plugin. Mitigated by shared gateway API and shared PatternFly component
  library.
- **Backstage plugin deferred** — enterprise customers wanting RHDH integration
  must wait for the plugin to be built after the standalone SPA.

### Neutral

- The gateway (ADR-029) is unaffected by the frontend choice. It serves static
  files or sits behind a Backstage proxy — the API is identical.
- The CLI continues to work independently of either frontend.

## Implementation Notes

### Phase 4c: Standalone SPA

1. Scaffold React + Vite in `frontend/` directory
2. Install PatternFly (`@patternfly/react-core`, `@patternfly/react-table`,
   `@patternfly/react-charts`)
3. Implement views per [design-dashboard.md](/.sdlc/context/design-dashboard.md):
   scan list, scan detail, rule catalog, dashboard home, remediation queue
4. Build static assets into gateway container image
5. Gateway serves via `FastAPI.mount("/", StaticFiles(...))`

### Future: Backstage Plugin

1. Scaffold Backstage frontend plugin (`@backstage/create-app`)
2. Implement APME views using Backstage plugin SDK + PatternFly
3. Proxy gateway API via Backstage backend proxy
4. Register APME scans as catalog entity annotations
5. Publish plugin to RHDH marketplace

## Related Decisions

- ADR-029: Web gateway architecture (the API this frontend consumes)
- ADR-028: Session-based fix workflow (FixSession protocol the WebSocket maps)

## References

- [design-dashboard.md](/.sdlc/context/design-dashboard.md) — PatternFly
  components, views, SQLite schema
- [docs/mockups/](/docs/mockups/) — HTML mockups and Figma
- [Red Hat self-service automation portal](https://www.redhat.com/en/technologies/management/ansible/self-service-automation)
  — AAP's Backstage-based portal
- [Backstage.io](https://backstage.io/) — Open source developer portal
- [PatternFly](https://www.patternfly.org/) — Red Hat design system

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-19 | AI Agent | Initial proposal |
