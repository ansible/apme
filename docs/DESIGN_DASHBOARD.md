# Dashboard & Presentation Layer Design

## Status: proposed (Phase 4)

This document describes the architecture for adding an HTTP/WebSocket presentation layer to APME, enabling a web dashboard for findings management, remediation tracking, and enterprise integration.

---

## Problem statement

The CLI is sufficient for developer workstations and CI pipelines, but enterprise adoption requires:

- A web UI for browsing **check** results, filtering by rule/severity/file, and tracking remediation progress
- Persistent **activity** history (the CLI is fire-and-forget)
- A remediation queue for reviewing and accepting/rejecting AI-proposed fixes (Phase 3 integration)
- Multi-user access with authentication
- API access for CI/CD systems, IDE plugins, and third-party integrations

---

## Architecture

### Container placement

The presentation layer adds two components to the pod:

```
┌──────────────────────────── apme-pod ─────────────────────────────┐
│                                                                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐          │
│  │ Primary  │  │  Native  │  │   OPA    │  │ Ansible  │  ...     │
│  │  :50051  │  │  :50055  │  │  :50054  │  │  :50053  │          │
│  └────┬─────┘  └──────────┘  └──────────┘  └──────────┘          │
│       │                                                           │
│  ┌────┴──────────────────────────────────────────┐                │
│  │            API Gateway :8080                   │                │
│  │  FastAPI (async) — REST + WebSocket            │                │
│  │  gRPC client → Primary.FixSession (check/remediate), Format     │                │
│  │  SQLite/PostgreSQL for activity history                         │                │
│  └────┬──────────────────────────────────────────┘                │
│       │                                                           │
└───────┼───────────────────────────────────────────────────────────┘
        │ HTTP/WS
        ▼
   ┌──────────┐
   │  Browser  │  or CI/CD, IDE plugin, curl
   │  (SPA)    │
   └──────────┘
```

The **API Gateway** is a new container in the pod. It:

1. Speaks HTTP/WebSocket externally (port 8080)
2. Speaks gRPC internally to Primary (localhost:50051)
3. Owns the persistence layer (activity history, user sessions, remediation queue)
4. Serves the static SPA assets (or delegates to a CDN in production)

### Why a gateway container, not an HTTP layer on Primary

| Concern | Primary | Gateway |
|---------|---------|---------|
| Protocol | gRPC only | HTTP/WS → gRPC translation |
| State | Stateless (each operation is independent) | Stateful (activity history, users, queue) |
| Scaling | Scale with pod | Could be extracted for multi-pod |
| Auth | None (internal trust) | OAuth2/OIDC, API tokens |
| Dependencies | grpcio, engine | FastAPI, SQLAlchemy, auth libraries |

Primary stays pure gRPC and stateless. The gateway handles everything HTTP, auth, and persistence. This separation means Primary's contract is unchanged — the CLI, gateway, and any future consumer use the same gRPC surface (notably **`FixSession`** for check and remediate per ADR-039; unary **`Scan`** remains for simple callers).

---

## API design

### Endpoints

```
# Health & System
GET    /api/v1/health                 Aggregate health (gateway + all backend services)
GET    /api/v1/ai/models              List available AI models from Abbenay

# Dashboard
GET    /api/v1/dashboard/summary      Aggregate statistics (projects, scans, violations, health)
GET    /api/v1/dashboard/rankings     Ranked project lists (by health, violations, activity)

# Projects (ADR-037)
POST   /api/v1/projects               Create a project
GET    /api/v1/projects               List projects with summary data
GET    /api/v1/projects/{id}          Project detail with latest scan info
PATCH  /api/v1/projects/{id}          Update project metadata
DELETE /api/v1/projects/{id}          Delete project and cascade activity
GET    /api/v1/projects/{id}/activity Project activity history
GET    /api/v1/projects/{id}/violations Violations from latest scan
GET    /api/v1/projects/{id}/trend    Violation trend over time
GET    /api/v1/projects/{id}/dependencies Collections and Python packages (ADR-040)
WS     /api/v1/projects/{id}/ws/operate Check/remediate via WebSocket

# Dependencies (ADR-040)
GET    /api/v1/collections            All collections with usage counts
GET    /api/v1/collections/{fqcn}     Collection detail with dependent projects
GET    /api/v1/collections/{fqcn}/projects Projects using this collection
GET    /api/v1/python-packages        All Python packages with usage counts
GET    /api/v1/python-packages/{name} Package detail with dependent projects

# Sessions & Activity
GET    /api/v1/sessions               List sessions
GET    /api/v1/sessions/{id}          Session detail with activity
GET    /api/v1/sessions/{id}/trend    Session violation trend
GET    /api/v1/activity               List activity (paginated, filterable)
GET    /api/v1/activity/{id}          Activity detail (violations, proposals, logs, patches)
DELETE /api/v1/activity/{id}          Delete activity record

# Statistics
GET    /api/v1/violations/top         Most frequently violated rules
GET    /api/v1/stats/remediation-rates Remediation counts by rule
GET    /api/v1/stats/ai-acceptance    AI proposal acceptance rates by rule

# Feedback (pre-production)
GET    /api/v1/feedback/enabled       Check if feedback feature is enabled
POST   /api/v1/feedback               Submit feedback (creates GitHub issue)

# Playground
WS     /api/v1/ws/session             Unified check + remediate session (file upload,
                                       real-time progress, Tier 1 results,
                                       AI proposals, approval — single connection)
```

### WebSocket Session Protocol

A single WebSocket connection at `/api/v1/ws/session` handles the full **check** and
**remediate** lifecycle. The browser uploads files, receives real-time progress, reviews
Tier 1 auto-fix results and AI proposals, and approves/rejects — all inline.

Client → Server messages:

```json
{"type": "start", "options": {"ansible_version": "2.16", "enable_ai": true}}
{"type": "file", "path": "playbooks/deploy.yml", "content": "<base64>"}
{"type": "files_done"}
{"type": "approve", "approved_ids": ["proposal-1", "proposal-3"]}
{"type": "close"}
```

Server → Client messages:

```json
{"type": "session_created", "session_id": "abc-123", "scan_id": "scan-456", "ttl_seconds": 600}
{"type": "progress", "phase": "validation", "message": "Running native validator...", "level": 2}
{"type": "tier1_complete", "idempotency_ok": true, "patches": [...], "format_diffs": [...]}
{"type": "proposals", "tier": 2, "status": "AWAITING_APPROVAL", "proposals": [{"id": "p1", "file": "playbooks/deploy.yml", "rule_id": "M001", "confidence": 0.92, ...}]}
{"type": "result", "scan_id": "scan-456", "patches": [...], "remaining_violations": [...]}
```

### OpenAPI

FastAPI auto-generates an OpenAPI 3.1 spec at `/api/v1/openapi.json` and serves Swagger UI at `/api/v1/docs`. This gives CI/CD integrations and IDE plugins a machine-readable contract.

---

## Persistence

### Schema (core tables)

```
scans
  id              UUID (PK)
  project_name    TEXT
  created_at      TIMESTAMP
  status          ENUM (running, completed, failed)
  total_violations INT
  diagnostics     JSONB         -- full ScanDiagnostics serialized
  options         JSONB         -- ansible_core_version, collection_specs

violations
  id              UUID (PK)
  scan_id         UUID (FK → scans)
  rule_id         TEXT
  level           TEXT
  message         TEXT
  file            TEXT
  line            INT
  path            TEXT

remediation_proposals
  id              UUID (PK)
  scan_id         UUID (FK → scans)
  violation_id    UUID (FK → violations)
  tier            INT (1=deterministic, 2=AI, 3=manual)
  status          ENUM (pending, accepted, rejected, applied)
  diff            TEXT
  proposed_by     TEXT          -- "transform:L007" or "ai:openllm"
  reviewed_by     TEXT          -- username
  reviewed_at     TIMESTAMP
```

### Storage backend

| Deployment | Backend | Rationale |
|------------|---------|-----------|
| Single pod (dev/small team) | SQLite | Zero-config, file-based, sufficient for thousands of activity records |
| Multi-pod / enterprise | PostgreSQL | Shared state across pods, concurrent writers, full-text search |

The gateway uses SQLAlchemy with async support (`asyncpg` for PostgreSQL, `aiosqlite` for SQLite). The backend is selected by environment variable (`APME_DB_URL`).

---

## Authentication & authorization

### Model

| Mode | Use case | Mechanism |
|------|----------|-----------|
| None | Local dev, single-user | No auth headers required |
| API token | CI/CD pipelines | `Authorization: Bearer <token>` |
| OAuth2/OIDC | Enterprise SSO | Redirect flow, JWT validation |

Auth is opt-in. The gateway starts in "no auth" mode by default. Enterprise deployments configure an OIDC provider via environment variables:

```
APME_AUTH_PROVIDER=oidc
APME_OIDC_ISSUER=https://sso.example.com/realms/apme
APME_OIDC_CLIENT_ID=apme-dashboard
APME_OIDC_CLIENT_SECRET=...
```

API tokens are generated per-user and stored hashed in the database. They bypass the OIDC flow for non-interactive clients.

### Authorization

Phase 4 starts with a single role (all authenticated users can do everything). Role-based access (viewer, operator, admin) is a follow-on.

---

## Frontend

### Technology

**Implemented: React + TypeScript** with Vite for bundling.

- **React 18** with functional components and hooks
- **PatternFly 6** component library (Red Hat design system, consistent with AAP)
- **@ansible/ansible-ui-framework** for navigation and page layout patterns
- **Vite** for fast development and production builds
- Served by nginx in the UI container, proxies `/api/` to Gateway

### Key views

| View | Description |
|------|-------------|
| **Dashboard (home)** | Aggregate metrics (projects, avg health, violations, remediated). Project rankings (cleanest, most violations, stale, most active). |
| **Analytics** | Top violated rules. Remediation rates by rule. AI proposal acceptance rates with confidence scores. |
| **Projects list** | Paginated table with health score, violation count, trend, last checked. Sortable columns. Create/edit/delete. |
| **Project detail** | Overview (health score, violations, severity breakdown), Activity tab (check/remediate history), Violations tab (expandable messages), Dependencies tab (ansible-core, collections, Python packages), Settings tab. |
| **Collections** | All collections seen across projects with version and usage count. Click through to collection detail showing dependent projects. |
| **Python Packages** | All Python packages seen across projects with version and usage count. Click through to package detail showing dependent projects. |
| **Activity list** | Paginated table of runs with status, violation count, date. Filter by project, date range, status. |
| **Activity detail** | Violations grouped by file or rule. Severity badges. Expandable code context (3-5 lines around the violation). Diagnostics panel (engine timing, validator breakdown). |
| **Playground** | File upload for ad-hoc check/remediate. Real-time progress. AI proposal review with diff viewer. |
| **Health** | Service status cards (Primary, Native, OPA, Ansible, Gitleaks, Galaxy Proxy, Abbenay AI) with latency. |
| **Settings** | AI model selection for remediation. |

---

## Implementation plan

### Phase 4a: API gateway + activity history

1. **Gateway container** — FastAPI app with gRPC client + WebSocket to Primary
2. **`WS /api/v1/ws/session`** — unified check + remediate session over WebSocket
3. **`GET /api/v1/activity`** — list stored activity with pagination and filtering
4. **`GET /api/v1/activity/{id}`** — return violations + diagnostics
5. **Persistence** — SQLite backend with SQLAlchemy async
6. **Health endpoint** — aggregate health from all backend services
7. **Dockerfile + pod YAML update** — add gateway container on port 8080

### Phase 4b: Frontend SPA

8. **Vue 3 scaffold** — Vite project in `frontend/`, build output to `static/`
9. **Activity list view** — table with filters, sorting, pagination
10. **Activity detail view** — violation list with code context, diagnostics panel
11. **Rule catalog view** — browsable rule list with search
12. **Dashboard view** — charts (violations over time, top rules)

### Phase 4c: Remediation queue

13. **Remediation queue API** — CRUD for proposals, accept/reject
14. **Queue view** — diff viewer, batch accept/reject
15. **WebSocket streaming** — real-time check/remediate progress

### Phase 4d: Auth + enterprise

16. **OAuth2/OIDC integration** — login flow, JWT validation
17. **API tokens** — generation, hashing, validation
18. **Audit log** — who ran check/remediate, who accepted which fix

---

## gRPC ↔ HTTP translation

The gateway translates between HTTP and gRPC. The pattern is consistent:

```python
@router.post("/api/v1/activity")
async def create_activity(body: ActivityCreate):
    request = build_scan_request(body.files, body.options)

    async with grpc.aio.insecure_channel("127.0.0.1:50051") as channel:
        stub = primary_pb2_grpc.PrimaryStub(channel)
        # Unary Scan for simple request/response; interactive UI mirrors the CLI via FixSession (ADR-039).
        response = await stub.Scan(request, timeout=120)

    activity_record = store_activity(response)
    return activity_record
```

The gateway never runs the engine directly. It always delegates to Primary via gRPC. This means:

- The gateway has no dependency on `apme_engine` (only on `apme.v1` proto stubs)
- Primary's contract is the single source of truth
- The CLI and gateway are interchangeable consumers of the same backend

---

## Diagnostics in the dashboard

The `ScanDiagnostics` proto message (ADR-013) is stored as JSONB in the `scans` table and displayed in the activity detail view:

- **Summary card**: total time, files scanned, violation count
- **Validator breakdown**: bar chart showing time per validator
- **Slowest rules**: ranked table of rules by elapsed time
- **Engine phases**: parse / annotate / tree build timeline

This data is already collected on every engine run (per ADR-013). The dashboard simply renders it.

---

## Scaling considerations

For single-pod deployments, the gateway runs inside the pod alongside all other services. For enterprise multi-pod deployments:

```
                    ┌─────────────┐
  HTTP ───────────► │   Gateway   │ ← shared PostgreSQL
                    │  (separate  │
                    │   deploy)   │
                    └──┬──┬──┬────┘
                       │  │  │  gRPC
              ┌────────┘  │  └────────┐
              ▼           ▼           ▼
         ┌─────────┐ ┌─────────┐ ┌─────────┐
         │ Pod 1   │ │ Pod 2   │ │ Pod 3   │
         │ (full   │ │ (full   │ │ (full   │
         │  stack) │ │  stack) │ │  stack) │
         └─────────┘ └─────────┘ └─────────┘
```

The gateway is extracted from the pod and deployed separately. It load-balances gRPC calls across pods. Activity history is stored in a shared PostgreSQL instance. This is consistent with ADR-012 (scale pods, not services within a pod) — the gateway is the one component that naturally lives outside the pod because it serves external traffic and maintains shared state.

---

## Open questions

1. **File upload vs. repo URL**: Should the dashboard accept file uploads (like the CLI's chunked filesystem) or clone a Git repo? Both? The chunked FS pattern works for the API; repo cloning adds a build step but enables commit-level tracking.

2. **Check triggers**: Should the dashboard support scheduled/periodic checks (e.g., on every push to a branch), or is it strictly on-demand? Scheduled checks require a job scheduler (e.g., Celery, APScheduler, or a simple cron container).

3. **Multi-tenancy**: For enterprise deployments with multiple teams, should projects be scoped by team/org? This affects the database schema and auth model.

4. **Notifications**: Should the dashboard send notifications (Slack, email, webhook) when a check completes or a new violation is found? This is common in enterprise tools but adds integration surface.

5. **Frontend hosting**: Should the SPA be served by the gateway container (simple) or deployed to a CDN (faster, but adds deployment complexity)?
