# Dashboard & Presentation Layer Design

## Status: proposed (Phase 4)

This document describes the architecture for adding an HTTP/WebSocket presentation layer to APME, enabling a web dashboard for findings management, remediation tracking, and enterprise integration.

---

## Problem statement

The CLI is sufficient for developer workstations and CI pipelines, but enterprise adoption requires:

- A web UI for browsing scan results, filtering by rule/severity/file, and tracking remediation progress
- Persistent scan history (the CLI is fire-and-forget)
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
│  │  gRPC client → Primary.Scan / Primary.Format   │                │
│  │  SQLite/PostgreSQL for scan history             │                │
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
3. Owns the persistence layer (scan history, user sessions, remediation queue)
4. Serves the static SPA assets (or delegates to a CDN in production)

### Why a gateway container, not an HTTP layer on Primary

| Concern | Primary | Gateway |
|---------|---------|---------|
| Protocol | gRPC only | HTTP/WS → gRPC translation |
| State | Stateless (each scan is independent) | Stateful (scan history, users, queue) |
| Scaling | Scale with pod | Could be extracted for multi-pod |
| Auth | None (internal trust) | OAuth2/OIDC, API tokens |
| Dependencies | grpcio, engine | FastAPI, SQLAlchemy, auth libraries |

Primary stays pure gRPC and stateless. The gateway handles everything HTTP, auth, and persistence. This separation means Primary's contract is unchanged — the CLI, gateway, and any future consumer all call the same `Primary.Scan` RPC.

---

## API design

### REST endpoints

```
POST   /api/v1/scans                  Initiate a scan (upload files or point to repo)
GET    /api/v1/scans                  List scan history (paginated, filterable)
GET    /api/v1/scans/{scan_id}        Get scan result (violations, diagnostics)
DELETE /api/v1/scans/{scan_id}        Delete a scan record

POST   /api/v1/format                 Format files (upload, receive diffs)

GET    /api/v1/health                 Aggregate health (gateway + all backend services)

GET    /api/v1/rules                  List all rules (from RULE_CATALOG)
GET    /api/v1/rules/{rule_id}        Rule detail (description, examples, fixer status)

POST   /api/v1/fix                   Run the fix pipeline (format → scan → remediate)
GET    /api/v1/fix/{job_id}          Get fix job status and results

GET    /api/v1/remediation/queue     List AI-proposed fixes pending review
POST   /api/v1/remediation/{id}/accept    Accept a proposed fix
POST   /api/v1/remediation/{id}/reject    Reject a proposed fix
```

### WebSocket

```
WS  /api/v1/scans/{scan_id}/stream   Real-time scan progress (phases, validator status)
WS  /api/v1/fix/{job_id}/stream      Real-time fix pipeline progress
```

WebSocket streams emit JSON events as each phase completes:

```json
{"event": "engine_complete", "elapsed_ms": 142.0, "files_scanned": 12}
{"event": "validator_complete", "validator": "native", "violations": 5, "elapsed_ms": 23.0}
{"event": "validator_complete", "validator": "opa", "violations": 8, "elapsed_ms": 15.0}
{"event": "scan_complete", "total_violations": 13, "total_ms": 180.0}
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
| Single pod (dev/small team) | SQLite | Zero-config, file-based, sufficient for thousands of scans |
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

| Option | Pros | Cons |
|--------|------|------|
| React + TypeScript | Large ecosystem, component libraries | Heavy, complex toolchain |
| Vue 3 + TypeScript | Lighter, good DX, Composition API | Smaller ecosystem |
| HTMX + server-rendered | No build step, progressive enhancement | Limited interactivity for complex views |

**Recommendation: Vue 3 + TypeScript** with Vite for bundling. Rationale:

- Lighter than React for a dashboard (fewer dependencies)
- Composition API maps well to the data-driven violation/rule model
- Vite produces a static bundle that the gateway serves from `/static/`
- PrimeVue or Vuetify for table/filter/chart components

### Key views

| View | Description |
|------|-------------|
| **Scan list** | Paginated table of scans with status, violation count, date. Filter by project, date range, status. |
| **Scan detail** | Violations grouped by file or rule. Severity badges. Expandable code context (3-5 lines around the violation). Diagnostics panel (engine timing, validator breakdown). |
| **Rule catalog** | Browsable list of all rules with description, validator, fixer status. Links to rule `.md` docs. |
| **Remediation queue** | AI-proposed fixes with side-by-side diff viewer. Accept/reject buttons. Batch operations. |
| **Dashboard (home)** | Scan history chart (violations over time). Top violated rules. Scan frequency. Average scan time. |
| **Health** | Service status cards (Primary, Native, OPA, Ansible, Gitleaks, Galaxy Proxy) with latency. |

---

## Implementation plan

### Phase 4a: API gateway + scan history

1. **Gateway container** — FastAPI app with gRPC client to Primary
2. **`POST /api/v1/scans`** — accept file upload or directory path, call `Primary.Scan`, store result
3. **`GET /api/v1/scans`** — list stored scans with pagination and filtering
4. **`GET /api/v1/scans/{id}`** — return violations + diagnostics
5. **Persistence** — SQLite backend with SQLAlchemy async
6. **Health endpoint** — aggregate health from all backend services
7. **Dockerfile + pod YAML update** — add gateway container on port 8080

### Phase 4b: Frontend SPA

8. **Vue 3 scaffold** — Vite project in `frontend/`, build output to `static/`
9. **Scan list view** — table with filters, sorting, pagination
10. **Scan detail view** — violation list with code context, diagnostics panel
11. **Rule catalog view** — browsable rule list with search
12. **Dashboard view** — charts (violations over time, top rules)

### Phase 4c: Remediation queue

13. **Remediation queue API** — CRUD for proposals, accept/reject
14. **Queue view** — diff viewer, batch accept/reject
15. **WebSocket streaming** — real-time scan/fix progress

### Phase 4d: Auth + enterprise

16. **OAuth2/OIDC integration** — login flow, JWT validation
17. **API tokens** — generation, hashing, validation
18. **Audit log** — who scanned what, who accepted which fix

---

## gRPC ↔ HTTP translation

The gateway translates between HTTP and gRPC. The pattern is consistent:

```python
@router.post("/api/v1/scans")
async def create_scan(body: ScanCreate):
    request = build_scan_request(body.files, body.options)

    async with grpc.aio.insecure_channel("127.0.0.1:50051") as channel:
        stub = primary_pb2_grpc.PrimaryStub(channel)
        response = await stub.Scan(request, timeout=120)

    scan_record = store_scan(response)
    return scan_record
```

The gateway never runs the engine directly. It always delegates to Primary via gRPC. This means:

- The gateway has no dependency on `apme_engine` (only on `apme.v1` proto stubs)
- Primary's contract is the single source of truth
- The CLI and gateway are interchangeable consumers of the same backend

---

## Diagnostics in the dashboard

The `ScanDiagnostics` proto message (ADR-013) is stored as JSONB in the `scans` table and displayed in the scan detail view:

- **Summary card**: total time, files scanned, violation count
- **Validator breakdown**: bar chart showing time per validator
- **Slowest rules**: ranked table of rules by elapsed time
- **Engine phases**: parse / annotate / tree build timeline

This data is already collected on every scan (per ADR-013). The dashboard simply renders it.

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

The gateway is extracted from the pod and deployed separately. It load-balances gRPC calls across pods. Scan history is stored in a shared PostgreSQL instance. This is consistent with ADR-012 (scale pods, not services within a pod) — the gateway is the one component that naturally lives outside the pod because it serves external traffic and maintains shared state.

---

## Open questions

1. **File upload vs. repo URL**: Should the dashboard accept file uploads (like the CLI's chunked filesystem) or clone a Git repo? Both? The chunked FS pattern works for the API; repo cloning adds a build step but enables commit-level tracking.

2. **Scan triggers**: Should the dashboard support scheduled/periodic scans (e.g., on every push to a branch), or is it strictly on-demand? Scheduled scans require a job scheduler (e.g., Celery, APScheduler, or a simple cron container).

3. **Multi-tenancy**: For enterprise deployments with multiple teams, should projects be scoped by team/org? This affects the database schema and auth model.

4. **Notifications**: Should the dashboard send notifications (Slack, email, webhook) when a scan completes or a new violation is found? This is common in enterprise tools but adds integration surface.

5. **Frontend hosting**: Should the SPA be served by the gateway container (simple) or deployed to a CDN (faster, but adds deployment complexity)?
