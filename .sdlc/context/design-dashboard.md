# Dashboard & Presentation Layer Design

> **Governing decisions**: [ADR-029 (Web Gateway Architecture)](/.sdlc/adrs/ADR-029-web-gateway-architecture.md) and [ADR-030 (Frontend Deployment Model)](/.sdlc/adrs/ADR-030-frontend-deployment-model.md) formalize the architectural decisions for the web gateway and presentation layer. This document provides detailed implementation design (schema, components, API, views). ADR-029 also resolves [DR-003 (Dashboard Architecture)](/.sdlc/decisions/closed/decided/DR-003-dashboard-architecture.md) and [DR-008 (Data Persistence)](/.sdlc/decisions/closed/deferred/DR-008-data-persistence.md).

This document outlines the architecture for adding a web-based presentation layer to APME. The design follows a two-tier approach: a **standalone UI** for individual developers and small teams, and an **enterprise integration path** that leverages AAP's existing gateway infrastructure or RHDH/Backstage (see ADR-030). The standalone UI is the initial development focus — a lightweight, single-user dashboard that runs alongside the APME engine pod without external dependencies. For enterprise deployments requiring multi-user access, authentication, and persistent history, APME integrates behind the existing AAP Gateway component or as an RHDH/Backstage plugin rather than duplicating that infrastructure. The frontend uses PatternFly, Red Hat's open source design system, ensuring visual and UX consistency with other Red Hat products.

---

**Status**: proposed (Phase 4)

---

## Two-Tier Architecture

| Tier | Use Case | Authentication | Persistence | Development Priority |
|------|----------|----------------|-------------|---------------------|
| **Standalone UI** | Individual developers, small teams, local dev | None (single-user) | PostgreSQL | **Phase 4 — Initial focus** |
| **Enterprise Integration** | AAP deployments, multi-user, SSO | AAP Gateway (existing) | AAP database | Future — integrates with AAP |

### Design Principle

APME does **not** build its own enterprise gateway. For enterprise needs:

- Authentication, authorization, and session management are handled by **AAP Gateway**
- Multi-tenancy, RBAC, and audit logging use **AAP's existing infrastructure**
- APME registers as a service behind AAP Gateway, similar to other AAP components

This avoids duplicating mature infrastructure and ensures APME fits naturally into existing AAP deployments.

---

## Standalone UI (Initial Development Focus)

The standalone UI is a lightweight dashboard for individual use. Per ADR-029, it runs as a separate container **outside** the engine pod (to enable cross-pod aggregation) and requires no external authentication or database infrastructure.

### Architecture

Per ADR-029, the web gateway lives **outside** the engine pod. This enables
cross-pod aggregation for multi-pod deployments (ADR-012) and keeps persistence
in the presentation layer (ADR-020).

```
                   ┌──────────────────── apme-pod ────────────────────┐
                   │  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
                   │  │ Engine  │  │  Native  │  │   OPA    │  ... │
                   │  │  :50051  │  │  :50055  │  │  :50054  │      │
                   │  └──────────┘  └──────────┘  └──────────┘      │
                   └─────────┬──────────────────────────────────────┘
                             │ gRPC (FixSession, Health; unary Scan/Format as needed)
                             │
┌────────────────────────────┼──────────────────────────────────────┐
│  Web Gateway :8080         │                                      │
│  FastAPI (async)           │                                      │
│  ├── REST API + WebSocket  │                                      │
│  ├── gRPC client ──────────┘                                      │
│  ├── File discovery + chunking (mounted vol or SCM clone)         │
│  ├── WebSocket ↔ FixSession bidi gRPC bridge (ADR-028)            │
│  └── PostgreSQL persistence (activity history, violations, proposals)   │
│                                                                    │
│  Static SPA (PatternFly/React — standalone mode; see ADR-030)     │
└───────────────────────────┬────────────────────────────────────────┘
                            │ HTTP + WebSocket
                            ▼
                       ┌──────────┐
                       │  Browser  │  Single user, no auth (standalone)
                       └──────────┘
```

For multi-pod scaling, the gateway connects to engine pods via a standard L4
load balancer. FixSession bidi streams get natural session affinity (pinned to
one pod for the stream's lifetime). See ADR-029 for details.

### Characteristics

| Aspect | Standalone UI |
|--------|---------------|
| **Users** | Single user (the developer running the pod) |
| **Authentication** | None — assumes trusted local access |
| **Persistence** | PostgreSQL database |
| **Deployment** | Container in the APME pod |
| **State** | Local activity history, remediation queue |

### What It Provides

- **Activity history** — browse past check/remediate runs without re-running
- **Violation browser** — filter by rule, severity, file
- **Code context** — view violations with surrounding lines
- **Diagnostics** — engine timing, validator breakdown
- **Remediation queue** — review AI-proposed fixes (Phase 3 integration)
- **Rule catalog** — browsable documentation for all 93 rules

### What It Does NOT Provide

- Multi-user access
- Authentication / authorization
- Team collaboration features
- Scheduled checks
- Webhook notifications

These are enterprise features handled by AAP Gateway integration.

---

## Enterprise Integration (Future)

For enterprise deployments, APME sits behind the **AAP Gateway** — the existing ingress and authentication layer for Ansible Automation Platform.

### Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        AAP Platform                                  │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    AAP Gateway                                │   │
│  │  - OAuth2/OIDC (Red Hat SSO, LDAP, SAML)                     │   │
│  │  - RBAC (organizations, teams, roles)                         │   │
│  │  - Audit logging                                              │   │
│  │  - API routing                                                │   │
│  └────┬─────────────────────────────────────────────────────────┘   │
│       │                                                              │
│       │  /api/apme/* → APME service                                 │
│       ▼                                                              │
│  ┌──────────────────────────── apme-pod ────────────────────────┐   │
│  │                                                               │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐     │   │
│  │  │ Engine  │  │  Native  │  │   OPA    │  │ Ansible  │ ... │   │
│  │  │  :50051  │  │  :50055  │  │  :50054  │  │  :50053  │     │   │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘     │   │
│  │                                                               │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### AAP Gateway Responsibilities

| Concern | Handled By |
|---------|------------|
| Authentication | AAP Gateway (SSO, LDAP, SAML, OAuth2) |
| Authorization | AAP Gateway (RBAC, organizations, teams) |
| Session management | AAP Gateway |
| Audit logging | AAP Gateway |
| API routing | AAP Gateway routes `/api/apme/*` to APME |
| Persistent storage | AAP database (PostgreSQL) |

### APME Responsibilities (Enterprise Mode)

| Concern | Handled By |
|---------|------------|
| Check / remediate execution | Engine service (gRPC, `FixSession`) |
| Validation | Native, OPA, Ansible, Gitleaks validators |
| Remediation | Remediation engine (Phase 3) |
| API endpoints | Stateless REST API (no auth, trusts gateway headers) |

In enterprise mode, APME exposes a **stateless API** that trusts identity headers from AAP Gateway. It does not manage users, sessions, or permissions — that's AAP Gateway's job.

---

## Frontend Technology

### PatternFly (Mandatory)

The UI uses **PatternFly**, Red Hat's open source design system. This is mandatory for Red Hat product alignment.

| Aspect | Choice |
|--------|--------|
| **Design System** | [PatternFly 5](https://www.patternfly.org/) |
| **Framework** | React + TypeScript (PatternFly's primary target) |
| **Bundler** | Vite |
| **Charts** | PatternFly Charts (Victory-based) |
| **Icons** | PatternFly Icons |

### Why PatternFly

- **Red Hat standard** — consistent UX across AAP, OpenShift Console, Insights, Quay
- **Accessibility** — WCAG 2.1 AA compliant out of the box
- **Enterprise components** — data tables, filters, wizards, code editors
- **React-first** — well-maintained React component library
- **Theming** — supports Red Hat brand theming

### PatternFly Components Used

| View | Components |
|------|------------|
| **Activity list** | `Table`, `Toolbar`, `Pagination`, `Label` (severity badges) |
| **Activity / operation detail** | `Tabs`, `DescriptionList`, `CodeBlock`, `ExpandableSection` |
| **Rule catalog** | `Table`, `SearchInput`, `Label` (validator/fixer badges) |
| **Remediation queue** | `Table`, `CodeEditor` (diff view), `Button` (accept/reject) |
| **Dashboard** | `Card`, `ChartDonut`, `ChartBar`, `ChartLine` |
| **Health** | `Card`, `Label` (status badges), `DescriptionList` |

---

## Standalone UI — API Design

The standalone UI exposes a REST API for the frontend. This API is also usable by scripts and local tooling.

### Endpoints

```
WS     /api/v1/ws/session             Unified check + remediate session (file upload,
                                       progress, Tier 1 results, AI proposals,
                                       approval — all over one connection; maps to `FixSession`)

GET    /api/v1/activity               List activity history (paginated, filterable); records use engine `scan_id`
GET    /api/v1/activity/{scan_id}     Get operation result (violations, diagnostics)
DELETE /api/v1/activity/{scan_id}     Delete an activity record

GET    /api/v1/health                 Aggregate health (UI + all backend services)

GET    /api/v1/rules                  List all rules
GET    /api/v1/rules/{rule_id}        Rule detail
```

### WebSocket Session Protocol (Unified Check + Remediate)

A single WebSocket endpoint handles the full **check** and **remediate** lifecycle. The
connection maps 1:1 to a `FixSession` bidi gRPC stream (ADR-028, ADR-039). The gateway
translates between JSON WebSocket messages and protobuf SessionCommand/Event
messages:

| Direction | WebSocket (JSON) | gRPC (protobuf) |
|---|---|---|
| Client → Server | `{"type": "start", "options": {...}}` | — (gateway-internal) |
| Client → Server | `{"type": "file", "path": "...", "content": "<base64>"}` | `SessionCommand.upload` (ScanChunk) |
| Client → Server | `{"type": "files_done"}` | Last `ScanChunk` with `last=true` |
| Client → Server | `{"type": "approve", "approved_ids": [...]}` | `SessionCommand.approve` |
| Client → Server | `{"type": "extend"}` | `SessionCommand.extend` |
| Client → Server | `{"type": "close"}` | `SessionCommand.close` |
| Server → Client | `{"type": "session_created", ...}` | `SessionEvent.created` |
| Server → Client | `{"type": "progress", ...}` | `SessionEvent.progress` |
| Server → Client | `{"type": "tier1_complete", ...}` | `SessionEvent.tier1_complete` |
| Server → Client | `{"type": "proposals", ...}` | `SessionEvent.proposals` |
| Server → Client | `{"type": "approval_ack", ...}` | `SessionEvent.approval_ack` |
| Server → Client | `{"type": "result", ...}` | `SessionEvent.result` |

Files are uploaded as base64-encoded content within JSON messages. The gateway
writes them to a temp directory, constructs `ScanChunk` protobuf messages, and
streams them to Engine over the `FixSession` gRPC stream.

### No Authentication

The standalone API has **no authentication**. It assumes:

- The user running the pod is the only user
- Access is via localhost or a trusted network
- There is no sensitive data beyond what's on the local filesystem

For multi-user or network-exposed deployments, use enterprise mode behind AAP Gateway.

---

## Persistence (Standalone)

### PostgreSQL Schema

```sql
scans
  id              TEXT PRIMARY KEY    -- UUID
  project_path    TEXT
  created_at      TEXT                -- ISO 8601
  status          TEXT                -- running, completed, failed
  total_violations INTEGER
  diagnostics     TEXT                -- JSON
  options         TEXT                -- JSON

violations
  id              TEXT PRIMARY KEY
  scan_id         TEXT REFERENCES scans(id)
  rule_id         TEXT
  level           TEXT
  message         TEXT
  file            TEXT
  line            INTEGER
  path            TEXT

remediation_proposals
  id              TEXT PRIMARY KEY
  scan_id         TEXT REFERENCES scans(id)
  violation_id    TEXT REFERENCES violations(id)
  tier            INTEGER             -- 1=deterministic, 2=AI, 3=manual
  status          TEXT                -- pending, accepted, rejected, applied
  diff            TEXT
  proposed_by     TEXT
```

### Storage Location

PostgreSQL data is stored in a dedicated persistent volume mounted on the
`postgres` sidecar (`/var/lib/postgresql/data`). The Gateway connects via
`APME_DATABASE_URL`:

```yaml
containers:
  - name: postgres
    image: postgres:16
    env:
      - name: POSTGRES_USER
        value: "apme"
      - name: POSTGRES_PASSWORD
        value: "apme"
      - name: POSTGRES_DB
        value: "apme"
    volumeMounts:
      - name: postgres-data
        mountPath: /var/lib/postgresql/data
  - name: gateway
    env:
      - name: APME_DATABASE_URL
        value: "postgresql+asyncpg://apme:apme@127.0.0.1:5432/apme"
```

See [`containers/podman/pod.yaml`](../../containers/podman/pod.yaml) for the
reference Podman sidecar definition.

---

## Key Views

### Activity List

| Element | PatternFly Component |
|---------|---------------------|
| Table | `Table` with sortable columns |
| Filters | `Toolbar` with `SearchInput`, `Select` (status filter) |
| Pagination | `Pagination` |
| Status badges | `Label` (green=completed, yellow=running, red=failed) |
| Actions | `Button` (view, delete) |

### Activity / Operation Detail

| Element | PatternFly Component |
|---------|---------------------|
| Summary | `Card` with `DescriptionList` |
| Violations | `Table` grouped by file or rule |
| Code context | `CodeBlock` with line highlighting |
| Diagnostics | `Tabs` → timing breakdown, validator stats |
| Severity | `Label` (error=red, warning=orange, info=blue) |

### Rule Catalog

| Element | PatternFly Component |
|---------|---------------------|
| Search | `SearchInput` |
| Table | `Table` with rule_id, description, validator, fixer |
| Badges | `Label` (validator type, fixable status) |
| Detail drawer | `Drawer` with full rule documentation |

### Remediation Queue

| Element | PatternFly Component |
|---------|---------------------|
| Proposals table | `Table` with violation, tier, status |
| Diff viewer | `CodeEditor` in read-only diff mode |
| Actions | `Button` (accept, reject), `Checkbox` (batch) |
| Confidence | `Label` with confidence score |

### Dashboard (Home)

| Element | PatternFly Component |
|---------|---------------------|
| Activity summary | `Card` with count, last run time |
| Violations over time | `ChartLine` |
| Top violated rules | `ChartBar` |
| Severity breakdown | `ChartDonut` |

---

## Implementation Plan

### Phase 4a: Standalone UI Backend

1. **FastAPI app** — REST API + WebSocket with gRPC client to Engine
2. **PostgreSQL persistence** — activity history, violations, proposals
3. **WS /api/v1/ws/session** — unified check + remediate session over WebSocket (`FixSession`)
4. **GET /api/v1/activity** — list with pagination/filtering
5. **Health endpoint** — aggregate backend health
6. **Dockerfile** — add standalone-ui container to pod

### Phase 4b: PatternFly Frontend

1. **React + Vite scaffold** — `frontend/` directory
2. **PatternFly setup** — install `@patternfly/react-core`, `@patternfly/react-table`
3. **Activity list view** — table with filters, pagination
4. **Activity detail view** — violations, code context, diagnostics
5. **Rule catalog** — searchable rule list
6. **Dashboard** — charts and summary cards

### Phase 4c: Remediation Queue

1. **Queue API** — list proposals, accept/reject endpoints
2. **Diff viewer** — PatternFly CodeEditor in diff mode
3. **Batch operations** — select multiple, accept/reject all

### Phase 4d: Enterprise Integration (Future)

1. **Stateless API mode** — trust X-User headers from AAP Gateway
2. **AAP Gateway registration** — service manifest for AAP routing
3. **PostgreSQL support** — use AAP's database for persistence
4. **Documentation** — AAP integration guide

---

## gRPC ↔ HTTP Translation

The standalone UI translates HTTP requests to gRPC calls:

```python
@router.post("/api/v1/activity")
async def create_operation(body: OperationCreate):
    async with grpc.aio.insecure_channel("127.0.0.1:50051") as channel:
        stub = engine_pb2_grpc.EngineStub(channel)
        async for event in stub.FixSession(build_session_commands(body)):
            # process SessionEvent (progress, violations, patches)
            ...

    record = store_operation(events)
    return record
```

The UI/Gateway container **never** runs the engine directly. It always delegates to Engine via the **`FixSession`** bidirectional stream (ADR-039). This means:

- The UI has no dependency on `apme_engine`
- Engine's contract is the single source of truth
- The CLI and UI are interchangeable consumers

---

## Diagnostics Display

The `ScanDiagnostics` proto (ADR-013) is stored as JSON and rendered:

| Component | PatternFly Display |
|-----------|-------------------|
| Summary | `Card` with total time, files, violations |
| Validator timing | `ChartBar` — time per validator |
| Slowest rules | `Table` — ranked by elapsed_ms |
| Engine phases | `DescriptionList` — parse, annotate, tree build |

---

## Open Questions (Standalone Scope)

| Question | Notes |
|----------|-------|
| **Directory selection** | Should the UI provide a file browser, or just accept a path input? Start with path input. |
| **Live reload** | Should the UI auto-refresh when files change? Adds complexity; defer to v2. |
| **Export** | Should check results be exportable (JSON, SARIF, CSV)? Useful for CI integration. |
| **Theming** | Should the standalone UI support light/dark mode? PatternFly supports both. |

---

## Related Documents

- [ADR-029: Web Gateway Architecture](/.sdlc/adrs/ADR-029-web-gateway-architecture.md) — Governing ADR for the gateway backend
- [ADR-030: Frontend Deployment Model](/.sdlc/adrs/ADR-030-frontend-deployment-model.md) — Standalone SPA vs. Backstage plugin
- [ADR-028: Session-Based Fix Workflow](/.sdlc/adrs/ADR-028-session-based-fix-workflow.md) — FixSession bidi streaming protocol
- [ADR-039: Unified Operation Stream](/.sdlc/adrs/ADR-039-unified-operation-stream.md) — Check/remediate via `FixSession`; `ScanStream` removed
- [ADR-013: Structured Diagnostics](/.sdlc/adrs/ADR-013-structured-diagnostics.md) — Timing data captured per engine run
- [ADR-020: Reporting Service](/.sdlc/adrs/ADR-020-reporting-service.md) — Event delivery model and persistence principles
- [design-remediation.md](design-remediation.md) — Remediation engine (Phase 3)
- [design-validators.md](design-validators.md) — Validator abstraction
- [architecture.md](architecture.md) — Container topology
- [PatternFly Documentation](https://www.patternfly.org/) — Red Hat design system
