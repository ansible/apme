# 18 — Shared UI Workflow and Host Integration

> Previous: [17 — Scaling and Deployment Topology](17-scaling-and-deployment.md) | Next: (end)

## Purpose

APME’s interactive **scan → pause → choose → remediate** experience is shipped
once as `@apme/ui-workflow` (PatternFly) and mounted by more than one host.
This document covers:

- The package and **adapter** contract (pluggable transport / auth)
- How the **native SPA** and **Portal** hosts wire that adapter
- An end-to-end **API call sequence** for a Quality / project-operation run

It does **not** replace [14 — UI and WebSocket Integration](14-ui-integration.md)
(SPA chrome, dashboards, page map). Prefer this document for the shared
workflow package and dual-host auth path. Live operation transport for the
shared UI is **REST + fetch-stream SSE** (ADR-052 / ADR-065), not the legacy
WebSocket hooks described in 14.

## Package

| Item | Detail |
|------|--------|
| Package | `@apme/ui-workflow` |
| Source | `frontend/packages/ui-workflow` |
| Native SPA | npm workspace (`workspace:*`) |
| External hosts (Portal) | GitHub Release `npm pack` tarball (ADR-066), pin URL in consumer `package.json` |

The package owns workflow UI and client orchestration (`useProjectWorkflow`,
`ProjectWorkflowPanel`, `CheckOptionsForm`, assess / proposal / AI panels).
It does **not** own Backstage catalog APIs, Portal RBAC policy, or SCM
commit/push (Gateway owns SCM — ADR-056).

## Adapter contract

The UI never hard-codes a Gateway origin or auth scheme. Network access goes
through an **`ApmeApiAdapter`** supplied by the host via `ApmeApiProvider`:

| Field | Role |
|-------|------|
| `apiBase` | Prefix for REST paths (e.g. `/api/v1` or `https://…/api/catalog/apme`) |
| `fetch` | `fetch`-compatible function (cookies, Bearer, etc.) |
| `origin` | Absolute origin for SSE / absolute URLs when needed |

Helpers such as `apmeApiUrl('/projects/:id/operation')` resolve against
`apiBase`. Hosts plug in **how** calls are made; the **operation API shape**
stays the Gateway `/api/v1` contract (ADR-060).

```text
┌─────────────────────────────────────────────────────────────┐
│  @apme/ui-workflow                                          │
│  ProjectWorkflowPanel / useProjectWorkflow / panels         │
│         │                                                   │
│         ▼                                                   │
│  ApmeApiAdapter  (apiBase + fetch + origin)                 │
└─────────┬───────────────────────────────────────────────────┘
          │
     ┌────┴────────────────────┐
     │                         │
     ▼                         ▼
 Native SPA                 Portal thin host
 apiBase=/api/v1            apiBase=…/api/catalog/apme
 fetch=window.fetch         fetch=Backstage fetchApi
 nginx → Gateway            catalog-backend-module-apme
                            → Gateway (service call)
```

## Hosts

### Native APME SPA

- Shell: React app on `:8081` (nginx); Activity, Analytics, full routing.
- Default adapter: `apiBase` `/api/v1`, browser `fetch`; nginx proxies to Gateway `:8080`.
- Theme: sets `pf-v6-theme-dark` on `<html>` when the SPA is in dark mode.

### Portal (thin Quality host)

- Shell: `@ansible/plugin-backstage-apme` mounts the package on catalog /
  self-service Quality surfaces.
- Adapter: absolute discovery URL `catalogBase + '/apme'` + Backstage
  `fetchApi` (identity Bearer). Browser does **not** call Gateway `:8080`
  directly.
- Backend: `@ansible/catalog-backend-module-apme` requires a Backstage **user**
  credential, injects SCM token, strips `file_overrides` (ADR-056), proxies
  to Gateway `/api/v1/…`.
- Portal auth / RBAC therefore still gate the workflow: UI → Portal backend →
  Gateway. Pointing the adapter at Gateway would bypass that path; eap-next
  does not.

Portal host detail (mount rules, dark-theme sync, config knobs) lives in the
Portal plugin’s `ARCHITECTURE.md` on the consuming branch.

## Auth implication (FAQ)

> If Portal is only a shell around `@apme/ui-workflow`, do UI API calls still
> go through Portal auth?

**Yes, when the host injects the catalog adapter** as above. The package is
pluggable for transport/auth; Portal chooses the proxy. The native SPA chooses
direct Gateway access through its own edge.

## Quality workflow — API sequence

Paths below are **relative to `apiBase`**. On the native SPA that is
`/api/v1`; on Portal that is `/api/catalog/apme` (which forwards to Gateway
`/api/v1`).

Typical interactive path: **assess pause** + **interactive** Tier-1 review
(ADR-062 / ADR-064), optional AI escalation, then Gateway submit (ADR-056).

```mermaid
sequenceDiagram
  participant Host as Host chrome
  participant UI as ui-workflow
  participant API as apiBase proxy or Gateway
  participant GW as APME Gateway

  Host->>UI: Mount ProjectWorkflowPanel / CheckOptionsForm
  Note over Host,API: Portal may GET /projects lookup or register first
  UI->>API: GET /projects/{id}/operation
  API->>GW: (Portal) or local (SPA)
  UI->>API: POST /projects/{id}/operation action=check assess_pause interactive
  UI->>API: GET /projects/{id}/operation/events (fetch SSE)
  Note over UI,GW: Scan runs; pause after findings
  UI->>API: POST /projects/{id}/operation/begin-remediate
  Note over UI,GW: Tier-1 proposals; user Accept/Decline
  UI->>API: PATCH /projects/{id}/operation/proposals
  UI->>API: POST /projects/{id}/operation/approve
  opt AI escalation
    UI->>API: POST /projects/{id}/operation/escalate-ai
    UI->>API: PATCH/approve AI proposals as needed
  end
  UI->>API: POST /projects/{id}/operation/submit
  Note over GW: SCM commit / PR (Gateway owns push)
```

### Step table

| Step | Method + path | When |
|------|----------------|------|
| Resolve project | Host-specific (`POST/GET /projects`, lookup) | Before panel; Portal registers from catalog SCM annotations |
| Read live op | `GET /projects/{id}/operation` | Attach / refresh |
| Subscribe | `GET /projects/{id}/operation/events` | Fetch + ReadableStream SSE (not `EventSource` — auth headers) |
| Start scan | `POST /projects/{id}/operation` body `{ action: "check", options: { assess_pause, interactive, enable_ai, … } }` | User clicks Scan |
| Continue after assess | `POST /projects/{id}/operation/begin-remediate` | User continues past findings |
| Draft decisions | `PATCH /projects/{id}/operation/proposals` | Accept/Decline while reviewing |
| Gate approve | `POST /projects/{id}/operation/approve` `{ approved_ids }` | Apply selected proposals |
| AI (optional) | `POST /projects/{id}/operation/escalate-ai` `{ targets }` | Leave triage / run AI |
| Cancel | `POST /projects/{id}/operation/cancel` | User abort |
| Commit / PR | `POST /projects/{id}/operation/submit` | Gateway SCM (no Portal `file_overrides`) |

Portal-only helpers used by the shell (not required by the package core):

| Path under catalog `/apme` | Role |
|----------------------------|------|
| `GET /settings`, `GET /ai/models` | AI enablement / model list for CheckOptionsForm |
| `GET /lookup`, `POST /projects` | Resolve or register project from repo URL |

## Related ADRs

| ADR | Relevance |
|-----|-----------|
| [ADR-052](../../.sdlc/adrs/ADR-052-project-operation-sse-architecture.md) | Project operation + SSE |
| [ADR-056](../../.sdlc/adrs/ADR-056-apme-owns-scm-commit-push.md) | Gateway owns SCM commit/push |
| [ADR-060](../../.sdlc/adrs/ADR-060-rest-api-versioning-contract.md) | `/api/v1` contract |
| [ADR-062](../../.sdlc/adrs/ADR-062-ephemeral-proposal-working-set.md) | Interactive proposal gates |
| [ADR-064](../../.sdlc/adrs/ADR-064-assess-pause-session-continue.md) | Assess-pause Scan → Remediate |
| [ADR-065](../../.sdlc/adrs/ADR-065-spa-gateway-live-state-ownership.md) | SPA vs Gateway live state |
| [ADR-066](../../.sdlc/adrs/ADR-066-ui-workflow-github-release-artifacts.md) | Release tarball publish |

## Key source files

| Path | Role |
|------|------|
| `frontend/packages/ui-workflow/` | Shared package |
| `frontend/packages/ui-workflow/src/api/apmeApiAdapter.ts` | Adapter types / defaults |
| `frontend/packages/ui-workflow/src/useProjectWorkflow.ts` | Host-facing workflow hook |
| `frontend/packages/ui-workflow/src/hooks/useProjectOperationActions.ts` | REST actions |
| `frontend/packages/ui-workflow/src/hooks/useProjectOperationState.ts` | Op state + SSE |
| `frontend/src/api/apmeApiAdapter.tsx` | SPA re-export / default provider |
| Portal: `plugins/backstage-apme/src/api/createApmeUiWorkflowAdapter.ts` | Catalog + `fetchApi` adapter |
| Portal: `plugins/catalog-backend-module-apme/` | `/api/catalog/apme` → Gateway |

---

> Previous: [17 — Scaling and Deployment Topology](17-scaling-and-deployment.md) | (end of series)
