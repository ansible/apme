# ADR-029: Web Gateway Architecture

## Status

Implemented

## Date

2026-03-19

## Context

APME is a stateless gRPC microservice that returns scan results to the CLI and
forgets them. DR-003 (Dashboard Architecture) and DR-008 (Data Persistence) were
both deferred to v2, pending real user demand. That demand now exists: the
project needs a web-based dashboard for violation browsing, HITL remediation
review, scan history, and ROI metrics.

Three architectural forces shape this decision:

1. **The engine must stay stateless.** ADR-020 established that persistence is a
   presentation concern, not an engine concern. The engine's job is scanning;
   storage belongs in the presentation layer.

2. **Multi-pod scaling.** ADR-012 scales by running N complete engine pods behind
   a load balancer. Embedding a dashboard database inside an engine pod creates
   N isolated databases with no shared view. The dashboard must sit outside the
   engine pods to aggregate.

3. **The CLI already solves the hard problems.** File discovery, chunking,
   gRPC streaming, HITL approval, progress rendering — the CLI does all of this
   today. The web gateway is architecturally a "CLI without a terminal" that
   replaces stdout with a WebSocket and keyboard input with REST/WebSocket
   messages.

### What the original spec proposed

An earlier design spec proposed a Node.js/Express gateway with Alpine.js
frontend and RHDS Web Components. After review, the team decided to use
Python/FastAPI to keep the stack homogeneous (one language, one type checker,
one linter toolchain) and PatternFly/React for the frontend (matching AAP UI
conventions). The gateway's architectural role — gRPC translation layer with
persistence — remains unchanged.

## Decision

**Add a Python/FastAPI web gateway container that sits outside the engine pod,
translates HTTP/WebSocket to gRPC, and owns scan result persistence.**

The gateway is the combined presentation + reporting service for V1. It serves
the frontend SPA, exposes a REST API for stateless operations, maintains a
WebSocket-to-FixSession bridge for HITL remediation, and persists scan results
in PostgreSQL. The engine pods are unmodified.

### CLI Capability Split

The gateway distributes the CLI's responsibilities between two processes: the
gateway (server-side file I/O + gRPC client) and the browser (rendering + user
interaction).

| CLI Capability | CLI (today) | Web Gateway | Browser |
|---|---|---|---|
| File upload | Client reads `$CWD` | Gateway receives base64 files via WS, or reads mounted vol/SCM clone | File picker + WS upload |
| File chunking (`ScanChunk`) | Client builds protobuf | Gateway builds protobuf from uploaded files | — |
| `FixSession` bidi gRPC | Client holds stream | Gateway holds stream (unified check + remediate) | — |
| `FormatStream` gRPC | Client call | Gateway call | — |
| `Health` gRPC | Client call | Gateway call | — |
| Write patched files | Client writes to disk | Gateway writes to disk | — |
| Progress rendering | Terminal (stdout) | WebSocket relay | UI progress timeline |
| Violation display | Terminal table | REST API + PostgreSQL | Filterable table |
| HITL review (proposals) | Interactive terminal | WebSocket relay | Diff viewer with approve/reject |
| Approval decisions | Keyboard input | WebSocket relay | Accept/Reject buttons |
| Diagnostics (`-v`/`-vv`) | Terminal | REST API | Charts, cards |
| `--json` output | stdout | REST JSON response | — |
| Activity history | None (stateless) | PostgreSQL persistence | Browse/search |

### Architecture

```
                   ┌──────────────────────────── apme-pod ────────────────────────┐
                   │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
                   │  │ Engine   │  │  Native  │  │   OPA    │  │ Ansible  │ ...│
                   │  │  :50051  │  │  :50055  │  │  :50054  │  │  :50053  │    │
                   │  └──────────┘  └──────────┘  └──────────┘  └──────────┘    │
                   └─────────┬──────────────────────────────────────────────────┘
                             │ gRPC (FixSession, FormatStream, Health, ...)
                             │
┌────────────────────────────┼──────────────────────────────────────────────────┐
│  Web Gateway :8080         │                                                  │
│  ┌─────────────────────────┴─────────────────────────────────────────┐        │
│  │  FastAPI (async)                                                   │        │
│  │  ├── REST API (/api/v1/scans, /health, /rules, ...)               │        │
│  │  ├── WebSocket ↔ FixSession bidi gRPC bridge                      │        │
│  │  ├── gRPC client → Engine (FixSession, FormatStream, Health)     │        │
│  │  ├── File discovery + chunking (mounted vol or SCM clone)         │        │
│  │  └── PostgreSQL persistence (scan history, violations, proposals)     │        │
│  └───────────────────────────────────────────────────────────────────┘        │
│                                                                                │
│  ┌─────────────────────────────────────────────────────────────────────┐      │
│  │  Static SPA (PatternFly/React — standalone mode only; see ADR-030) │      │
│  └─────────────────────────────────────────────────────────────────────┘      │
└────────────────────────────────────────────────────────────────────────────────┘
         │ HTTP + WebSocket
         ▼
    ┌──────────┐
    │  Browser  │
    └──────────┘
```

In the Backstage/RHDH deployment model (ADR-030), the gateway does **not** serve
the frontend. The Backstage instance hosts the UI plugin and proxies API
requests to the gateway. The gateway is a headless API server in that mode:

```
RHDH/Backstage instance (hosts UI plugin)
         │ REST + WebSocket (proxied)
         ▼
    Web Gateway :8080 (API only, no static files)
```

### Cross-Pod Deployment

The gateway lives **outside** the engine pod. For V1 (single engine pod), the
gateway connects directly to `APME_ENGINE_ADDRESS` (e.g., `host:50051`). For
multi-pod deployments, a standard L4 load balancer (Kubernetes Service, HAProxy,
Envoy) sits between the gateway and N engine pods:

```
Web Gateway ──gRPC──► Load Balancer ──► Engine Pod 1
                                   ──► Engine Pod 2
                                   ──► Engine Pod N
```

`FixSession` bidi streams get natural session affinity — the HTTP/2 connection
pins to one pod for the stream's entire lifetime. No sticky-session
configuration is required.

For the reverse direction (ADR-020 `ScanCompleted` events), engine pods push
events to the gateway's reporting endpoint. Each engine pod gets
`APME_REPORTING_ENDPOINT` pointing at the gateway.

### WebSocket Session Protocol

Each browser session maps 1:1 to a server-side `FixSession` bidi gRPC stream
(ADR-028). A single WebSocket endpoint (`WS /api/v1/ws/session`) handles the
full check + remediate lifecycle: file upload, real-time progress, Tier 1 auto-fix
results, AI proposal delivery, interactive approval, and final results.

The gateway translates between WebSocket JSON messages and protobuf
`SessionCommand`/`SessionEvent` messages:

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
| Server → Client | `{"type": "expiring", ...}` | `SessionEvent.expiring` |
| Server → Client | `{"type": "closed"}` | `SessionEvent.closed` |

The gateway manages the gRPC stream lifecycle: collects uploaded files into a
temp directory, constructs `ScanChunk` protobuf messages, opens the
`FixSession` gRPC stream, and forwards events bidirectionally. The connection
closes on WebSocket disconnect or explicit close command.

### File Ingestion Paths

Three file ingestion paths are supported, each ending with the gateway
constructing `ScanChunk` protobuf messages and streaming them to Engine:

**Path 1 — Browser upload**: User uploads files via the WebSocket session.
Files are sent as base64-encoded JSON messages, written to a temp directory on
the gateway, then chunked into `ScanChunk` messages. This is the primary path
for the operator UI. Files are small (Ansible YAML content), so base64 overhead
is negligible.

**Path 2 — SCM ingestion**: User submits a repository URL (+ optional PAT).
The gateway clones the repo via direct `git clone` into a temp directory (or
via `CacheMaintainer.CloneOrg` for org-level batch operations), runs
APME-specific file discovery, reads files, and streams `ScanChunk` messages
to Engine. For single-repo URLs, `git clone` is the primary mechanism;
`CloneOrg` is used when scanning entire GitHub/GitLab organizations.

**Path 3 — Local directory**: User submits a filesystem path (e.g.,
`/workspace/my-project`). The gateway reads files from a mounted volume, applies
file discovery, and streams `ScanChunk` messages to Engine.

In all cases the gateway owns the entire file → chunk → gRPC pipeline.

### Persistence

The gateway owns all persistence for V1. This is consistent with ADR-020's
principle that persistence belongs in the presentation layer, not the engine.

**PostgreSQL** — required via `APME_DATABASE_URL`
(`postgresql+asyncpg://...`). Remote hosts require certificate-validated TLS
(`?sslmode=verify-full` with a configured CA); loopback connections may omit
TLS. Schema per [design-dashboard.md](/.sdlc/context/design-dashboard.md).

**Podman** (`tox -e up`) provisions a `postgres:16` sidecar with a dedicated
persistent volume (`apme-postgres-data`).

**bootc** ships no PostgreSQL quadlet or PVC. Operators set `APME_DATABASE_URL`
in `/etc/apme/env/apme.env` and must provision, secure, and back up an external
PostgreSQL service.

**Helm** likewise requires an externally provisioned PostgreSQL database via
`gateway.database.url` or `gateway.database.existingSecret`.

The gateway uses SQLAlchemy + asyncpg only; SQLite and file-path database
configuration are not supported.

**Extraction path** — if the reporting layer needs to serve non-web clients
(Grafana, CI systems), the persistence + query logic can be extracted into a
standalone reporting service per ADR-020's target architecture. The gateway
becomes a thin frontend; the reporting service owns the database. No engine
changes required.

### Authentication

**Standalone mode** (V1): No authentication. Single-user assumption — the user
running the pod is the only user.

**Enterprise mode** (future): The gateway trusts identity headers from AAP
Gateway (`X-User`, `X-Org`, etc.). AAP Gateway handles OAuth2/OIDC, RBAC, and
session management. The gateway exposes a stateless API.

### REST + WebSocket API

Read operations are REST endpoints backed by PostgreSQL. The check + remediate lifecycle
runs over a single WebSocket connection:

```
WS     /api/v1/ws/session             Unified scan + fix session (upload, progress,
                                       Tier 1 results, AI proposals, approval)

GET    /api/v1/scans                  List scan history (paginated, filterable)
GET    /api/v1/scans/{scan_id}        Get scan result
DELETE /api/v1/scans/{scan_id}        Delete scan record

GET    /api/v1/health                 Aggregate health (gateway + backends)
GET    /api/v1/rules                  List all rules
GET    /api/v1/rules/{rule_id}        Rule detail
```

Full API design in [design-dashboard.md](/.sdlc/context/design-dashboard.md).

## Alternatives Considered

### Alternative 1: Node.js/Express Gateway

**Description**: Use Node.js/Express for the gateway with Alpine.js frontend,
as proposed in the original spec.

**Pros**:
- Rich WebSocket ecosystem
- Lightweight for serving static files

**Cons**:
- Introduces a second language runtime (Node.js alongside Python)
- Second type system, linter, CI pipeline
- Cannot reuse APME's existing proto stubs, gRPC client patterns, or test
  infrastructure
- ADR-018 (mypy strict) and ADR-014 (ruff/pydoclint) do not apply

**Why not chosen**: Homogeneous Python stack eliminates an entire class of
integration and tooling overhead. FastAPI's async WebSocket support is
production-grade.

### Alternative 2: Separate Reporting Service (ADR-020 literal)

**Description**: Gateway stays stateless. A separate reporting service container
owns all persistence per ADR-020's target architecture diagram.

**Pros**:
- Clean separation of concerns
- Reporting service reusable by other clients

**Cons**:
- Two new containers for no V1 benefit
- More operational complexity
- The only V1 consumer of the reporting service is the gateway itself

**Why not chosen**: Premature split. V1 combines them. Extraction path is
documented and requires no engine changes when the time comes.

### Alternative 3: Streamlit Dashboard

**Description**: Streamlit app reading JSON files, as proposed in DR-003
Option A.

**Pros**:
- Very fast to prototype
- Python-only

**Cons**:
- No WebSocket support (no real-time HITL flow)
- No FixSession integration
- Limited interactivity for diff review/approval
- Cannot serve as a general-purpose API gateway

**Why not chosen**: Cannot support the HITL remediation workflow that is the
dashboard's core value proposition.

## Consequences

### Positive

- **Engine stays stateless** — no database client, no schema, no persistence
  logic in the engine. Consistent with ADR-020.
- **CLI and web UI are interchangeable consumers** — both talk to the same
  Engine gRPC contract. Adding the web gateway changes zero engine code.
- **HITL remediation via existing protocol** — the FixSession bidi stream
  (ADR-028) was designed to be client-agnostic. The web gateway is the second
  client (after the CLI) proving that design.
- **Single language stack** — Python end to end. mypy, ruff, pydoclint,
  pre-commit hooks all apply uniformly.
- **Frontend-agnostic API** — the REST + WebSocket surface serves any frontend
  (standalone SPA, Backstage plugin, mobile app). See ADR-030.

### Negative

- **New container** — adds one container to the deployment topology (outside the
  engine pod).
- **PostgreSQL connection pooling** — SQLAlchemy's default async queue pool
  allows concurrent connections up to ``pool_size`` + ``max_overflow``; write
  concurrency is bounded by pool limits and PostgreSQL row-level lock waits,
  not a single serialized connection.
- **Gateway becomes a critical path** — if the gateway is down, the web UI is
  unavailable. The CLI continues to work independently (it talks to Engine
  directly).

### Neutral

- The gateway does not import `apme_engine`. It is a pure gRPC client using
  the generated proto stubs from `apme.v1`.
- The CLI is unaffected. It continues to work as before, connecting directly
  to Engine.
- `ScanCompleted` event emission (ADR-020) is optional — the engine works with
  or without a reporting endpoint configured.

## Implementation Notes

### Container

The gateway runs as its own container, deployed alongside (but outside) the
engine pod. It needs:

- Network access to Engine's gRPC port (50051)
- Network access to PostgreSQL configured through ``APME_DATABASE_URL``
  (separate sidecar or external service; database volumes mount on PostgreSQL,
  not Gateway). Remote hosts require certificate-validated TLS
  (`?sslmode=verify-full` with a configured CA).
- Optional: mounted volume for local project scanning (`/workspace`)

### Dependencies (subject to ADR-019 governance)

- `fastapi` + `uvicorn` — async HTTP/WebSocket server
- `grpcio` + `grpcio-tools` — gRPC client
- `sqlalchemy` + `asyncpg` — async PostgreSQL ORM
- `apme.v1` proto stubs (shared with CLI)

### Phased Implementation

- **Phase 4a**: Gateway backend — REST API, gRPC client, PostgreSQL, health
- **Phase 4b**: WebSocket-to-FixSession bridge
- **Phase 4c**: Frontend SPA (see ADR-030 for framework decision)
- **Phase 4d**: Enterprise mode (AAP Gateway integration)

## Related Decisions

- ADR-001: gRPC for inter-service communication (the gateway is a gRPC client)
- ADR-012: Scale pods, not services (gateway sits outside pods, LB between)
- ADR-020: Reporting service and event delivery (gateway IS the reporting
  service for V1; extraction path documented)
- ADR-024: Thin CLI with local daemon mode (gateway mirrors CLI's gRPC client
  role)
- ADR-028: Session-based fix workflow (WebSocket-to-FixSession 1:1 mapping)
- ADR-030: Frontend deployment model (standalone vs. Backstage)
- DR-003: Dashboard architecture (resolved by this ADR)
- DR-008: Data persistence (resolved by this ADR — PostgreSQL in gateway)

## Addendum

> **Note (ADR-039):** The user-facing terminology was renamed: `scan` → `check`, `fix` → `remediate`, `Scans` UI → `Activity`. Engine-internal names (`ScanChunk`, `scan_id`, `_scan_pipeline`) are unchanged. The `ScanStream` RPC was removed; `FixSession` serves both check and remediate modes. The `apme` binary name is unchanged. REST paths such as `/api/v1/scans` may be exposed under **Activity** naming in the product (e.g. `/api/v1/activity`); the historical paths in this ADR describe the original gateway sketch.

## References

- [design-dashboard.md](/.sdlc/context/design-dashboard.md) — Detailed UI
  design, PostgreSQL schema, REST API, PatternFly components
- [FastAPI WebSocket](https://fastapi.tiangolo.com/advanced/websockets/)
- [gRPC Python async](https://grpc.github.io/grpc/python/grpc_asyncio.html)

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-19 | AI Agent | Initial proposal |
| 2026-03-22 | AI Agent | Replace SSE POST /scans with unified WS /ws/session; update file ingestion paths to include browser upload; expand WS protocol table |
