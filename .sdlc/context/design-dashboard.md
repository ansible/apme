# Dashboard & Presentation Layer Design

This document outlines the architecture for adding a web-based presentation layer to APME. The design follows a two-tier approach: a **standalone UI** for individual developers and small teams, and an **enterprise integration path** that leverages AAP's existing gateway infrastructure. The standalone UI is the initial development focus — a lightweight, single-user dashboard that runs alongside the APME pod without external dependencies. For enterprise deployments requiring multi-user access, authentication, and persistent history, APME integrates behind the existing AAP Gateway component rather than duplicating that infrastructure. The frontend uses PatternFly, Red Hat's open source design system, ensuring visual and UX consistency with other Red Hat products.

---

**Status**: proposed (Phase 4)

---

## Two-Tier Architecture

| Tier | Use Case | Authentication | Persistence | Development Priority |
|------|----------|----------------|-------------|---------------------|
| **Standalone UI** | Individual developers, small teams, local dev | None (single-user) | SQLite (local) | **Phase 4 — Initial focus** |
| **Enterprise Integration** | AAP deployments, multi-user, SSO | AAP Gateway (existing) | AAP database | Future — integrates with AAP |

### Design Principle

APME does **not** build its own enterprise gateway. For enterprise needs:

- Authentication, authorization, and session management are handled by **AAP Gateway**
- Multi-tenancy, RBAC, and audit logging use **AAP's existing infrastructure**
- APME registers as a service behind AAP Gateway, similar to other AAP components

This avoids duplicating mature infrastructure and ensures APME fits naturally into existing AAP deployments.

---

## Standalone UI (Initial Development Focus)

The standalone UI is a lightweight dashboard for individual use. It runs as part of the APME pod and requires no external authentication or database infrastructure.

### Architecture

```
┌──────────────────────────── apme-pod ─────────────────────────────┐
│                                                                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐          │
│  │ Primary  │  │  Native  │  │   OPA    │  │ Ansible  │  ...     │
│  │  :50051  │  │  :50055  │  │  :50054  │  │  :50053  │          │
│  └────┬─────┘  └──────────┘  └──────────┘  └──────────┘          │
│       │                                                           │
│  ┌────┴──────────────────────────────────────────┐                │
│  │          Standalone UI :8080                   │                │
│  │  FastAPI (async) — serves SPA + REST API       │                │
│  │  gRPC client → Primary.Scan / Primary.Format   │                │
│  │  SQLite for local scan history                 │                │
│  └────┬──────────────────────────────────────────┘                │
│       │                                                           │
└───────┼───────────────────────────────────────────────────────────┘
        │ HTTP
        ▼
   ┌──────────┐
   │  Browser │  Single user, no auth
   └──────────┘
```

### Characteristics

| Aspect | Standalone UI |
|--------|---------------|
| **Users** | Single user (the developer running the pod) |
| **Authentication** | None — assumes trusted local access |
| **Persistence** | SQLite file in a mounted volume |
| **Deployment** | Container in the APME pod |
| **State** | Local scan history, remediation queue |

### What It Provides

- **Scan history** — browse past scans without re-running
- **Violation browser** — filter by rule, severity, file
- **Code context** — view violations with surrounding lines
- **Diagnostics** — engine timing, validator breakdown
- **Remediation queue** — review AI-proposed fixes (Phase 3 integration)
- **Rule catalog** — browsable documentation for all 93 rules

### What It Does NOT Provide

- Multi-user access
- Authentication / authorization
- Team collaboration features
- Scheduled scans
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
│  │  │ Primary  │  │  Native  │  │   OPA    │  │ Ansible  │ ... │   │
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
| Scan execution | Primary service (gRPC) |
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
| **Scan list** | `Table`, `Toolbar`, `Pagination`, `Label` (severity badges) |
| **Scan detail** | `Tabs`, `DescriptionList`, `CodeBlock`, `ExpandableSection` |
| **Rule catalog** | `Table`, `SearchInput`, `Label` (validator/fixer badges) |
| **Remediation queue** | `Table`, `CodeEditor` (diff view), `Button` (accept/reject) |
| **Dashboard** | `Card`, `ChartDonut`, `ChartBar`, `ChartLine` |
| **Health** | `Card`, `Label` (status badges), `DescriptionList` |

---

## Standalone UI — API Design

The standalone UI exposes a REST API for the frontend. This API is also usable by scripts and local tooling.

### Endpoints

```
POST   /api/v1/scans                  Initiate a scan (directory path)
GET    /api/v1/scans                  List scan history (paginated, filterable)
GET    /api/v1/scans/{scan_id}        Get scan result (violations, diagnostics)
DELETE /api/v1/scans/{scan_id}        Delete a scan record

POST   /api/v1/format                 Format files (directory path, receive diffs)

GET    /api/v1/health                 Aggregate health (UI + all backend services)

GET    /api/v1/rules                  List all rules
GET    /api/v1/rules/{rule_id}        Rule detail

POST   /api/v1/fix                    Run fix pipeline
GET    /api/v1/fix/{job_id}           Get fix job status

GET    /api/v1/remediation/queue      List pending AI proposals
POST   /api/v1/remediation/{id}/accept    Accept a proposal
POST   /api/v1/remediation/{id}/reject    Reject a proposal
```

### No Authentication

The standalone API has **no authentication**. It assumes:

- The user running the pod is the only user
- Access is via localhost or a trusted network
- There is no sensitive data beyond what's on the local filesystem

For multi-user or network-exposed deployments, use enterprise mode behind AAP Gateway.

---

## Persistence (Standalone)

### SQLite Schema

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

The SQLite database is stored in a mounted volume:

```yaml
volumes:
  - name: apme-data
    hostPath:
      path: ~/.apme/data
      type: DirectoryOrCreate

containers:
  - name: standalone-ui
    volumeMounts:
      - name: apme-data
        mountPath: /data
    env:
      - name: APME_DB_PATH
        value: /data/apme.db
```

---

## Key Views

### Scan List

| Element | PatternFly Component |
|---------|---------------------|
| Table | `Table` with sortable columns |
| Filters | `Toolbar` with `SearchInput`, `Select` (status filter) |
| Pagination | `Pagination` |
| Status badges | `Label` (green=completed, yellow=running, red=failed) |
| Actions | `Button` (view, delete) |

### Scan Detail

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
| Scan summary | `Card` with count, last scan time |
| Violations over time | `ChartLine` |
| Top violated rules | `ChartBar` |
| Severity breakdown | `ChartDonut` |

---

## Implementation Plan

### Phase 4a: Standalone UI Backend

1. **FastAPI app** — REST API with gRPC client to Primary
2. **SQLite persistence** — scan history, violations, proposals
3. **POST /api/v1/scans** — initiate scan, store result
4. **GET /api/v1/scans** — list with pagination/filtering
5. **Health endpoint** — aggregate backend health
6. **Dockerfile** — add standalone-ui container to pod

### Phase 4b: PatternFly Frontend

1. **React + Vite scaffold** — `frontend/` directory
2. **PatternFly setup** — install `@patternfly/react-core`, `@patternfly/react-table`
3. **Scan list view** — table with filters, pagination
4. **Scan detail view** — violations, code context, diagnostics
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
@router.post("/api/v1/scans")
async def create_scan(body: ScanCreate):
    request = build_scan_request(body.project_path, body.options)

    async with grpc.aio.insecure_channel("127.0.0.1:50051") as channel:
        stub = primary_pb2_grpc.PrimaryStub(channel)
        response = await stub.Scan(request, timeout=120)

    scan_record = store_scan(response)
    return scan_record
```

The UI container **never** runs the engine directly. It always delegates to Primary via gRPC. This means:

- The UI has no dependency on `apme_engine`
- Primary's contract is the single source of truth
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
| **Export** | Should scan results be exportable (JSON, SARIF, CSV)? Useful for CI integration. |
| **Theming** | Should the standalone UI support light/dark mode? PatternFly supports both. |

---

## Related Documents

- [ADR-013: Structured Diagnostics](/.sdlc/adrs/ADR-013-structured-diagnostics.md) — Timing data captured per scan
- [design-remediation.md](design-remediation.md) — Remediation engine (Phase 3)
- [design-validators.md](design-validators.md) — Validator abstraction
- [architecture.md](architecture.md) — Container topology
- [PatternFly Documentation](https://www.patternfly.org/) — Red Hat design system
