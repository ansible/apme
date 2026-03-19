# ADR-029: Web Gateway Architecture

## Status

Proposed

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
in SQLite. The engine pods are unmodified.

### CLI Capability Split

The gateway distributes the CLI's responsibilities between two processes: the
gateway (server-side file I/O + gRPC client) and the browser (rendering + user
interaction). Files never travel over WebSocket.

| CLI Capability | CLI (today) | Web Gateway | Browser |
|---|---|---|---|
| File discovery | Client reads `$CWD` | Gateway reads mounted vol or SCM clone | — |
| File chunking (`ScanChunk`) | Client builds protobuf | Gateway builds protobuf | — |
| `ScanStream` gRPC | Client call | Gateway call | — |
| `FixSession` bidi gRPC | Client holds stream | Gateway holds stream | — |
| `FormatStream` gRPC | Client call | Gateway call | — |
| `Health` gRPC | Client call | Gateway call | — |
| Write patched files | Client writes to disk | Gateway writes to disk | — |
| Progress rendering | Terminal (stdout) | WebSocket relay | UI status line |
| Violation display | Terminal table | REST API + SQLite | Filterable table |
| HITL review (proposals) | Interactive terminal | WebSocket relay | Diff viewer |
| Approval decisions | Keyboard input | WebSocket relay | Accept/Reject buttons |
| Diagnostics (`-v`/`-vv`) | Terminal | REST API | Charts, cards |
| `--json` output | stdout | REST JSON response | — |
| Scan history | None (stateless) | SQLite persistence | Browse/search |

### Architecture

```
                   ┌──────────────────────────── apme-pod ────────────────────────┐
                   │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
                   │  │ Primary  │  │  Native  │  │   OPA    │  │ Ansible  │ ...│
                   │  │  :50051  │  │  :50055  │  │  :50054  │  │  :50053  │    │
                   │  └──────────┘  └──────────┘  └──────────┘  └──────────┘    │
                   └─────────┬──────────────────────────────────────────────────┘
                             │ gRPC (ScanStream, FixSession, Health, ...)
                             │
┌────────────────────────────┼──────────────────────────────────────────────────┐
│  Web Gateway :8080         │                                                  │
│  ┌─────────────────────────┴─────────────────────────────────────────┐        │
│  │  FastAPI (async)                                                   │        │
│  │  ├── REST API (/api/v1/scans, /health, /rules, ...)               │        │
│  │  ├── WebSocket ↔ FixSession bidi gRPC bridge                      │        │
│  │  ├── gRPC client → Primary (ScanStream, FormatStream, Health)     │        │
│  │  ├── File discovery + chunking (mounted vol or SCM clone)         │        │
│  │  └── SQLite persistence (scan history, violations, proposals)     │        │
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
gateway connects directly to `APME_PRIMARY_ADDRESS` (e.g., `host:50051`). For
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

### WebSocket-to-FixSession Mapping

Each browser session maps 1:1 to a server-side `FixSession` bidi gRPC stream
(ADR-028). The gateway translates between WebSocket JSON messages and protobuf
`SessionCommand`/`SessionEvent` messages:

| Direction | WebSocket (JSON) | gRPC (protobuf) |
|---|---|---|
| Client → Server | `{"type": "approve", "ids": [...]}` | `SessionCommand.approve` |
| Client → Server | `{"type": "extend"}` | `SessionCommand.extend` |
| Client → Server | `{"type": "close"}` | `SessionCommand.close` |
| Client → Server | `{"type": "resume", "session_id": "..."}` | `SessionCommand.resume` |
| Server → Client | `{"type": "progress", "message": "..."}` | `SessionEvent.progress` |
| Server → Client | `{"type": "proposals", "items": [...]}` | `SessionEvent.proposals` |
| Server → Client | `{"type": "tier1_summary", ...}` | `SessionEvent.tier1_summary` |
| Server → Client | `{"type": "result", "patches": [...]}` | `SessionEvent.result` |
| Server → Client | `{"type": "expiration_warning", ...}` | `SessionEvent.expiration_warning` |

The gateway manages the gRPC stream lifecycle: opens on WebSocket connect,
closes on WebSocket disconnect or explicit close command, and handles
reconnection via `SessionCommand.resume`.

### File Ingestion Paths

Files flow server-side only. The browser submits a target (URL or path); the
gateway handles all file I/O:

**Path 1 — SCM ingestion**: User submits a repository URL (+ optional PAT).
The gateway clones the repo via direct `git clone` into a temp directory (or
via `CacheMaintainer.CloneOrg` for org-level batch operations), runs
APME-specific file discovery, reads files, and streams `ScanChunk` messages
to Primary. For single-repo URLs, `git clone` is the primary mechanism;
`CloneOrg` is used when scanning entire GitHub/GitLab organizations.

**Path 2 — Local directory**: User submits a filesystem path (e.g.,
`/workspace/my-project`). The gateway reads files from a mounted volume, applies
file discovery, and streams `ScanChunk` messages to Primary.

In both cases the gateway owns the entire clone → discover → chunk → gRPC
pipeline. The browser never touches the filesystem.

### Persistence

The gateway owns all persistence for V1. This is consistent with ADR-020's
principle that persistence belongs in the presentation layer, not the engine.

**SQLite for V1** — zero external infrastructure. The database file lives in a
mounted volume (`/data/apme.db`). Schema per
[design-dashboard.md](/.sdlc/context/design-dashboard.md).

**PostgreSQL upgrade path** — for enterprise deployments requiring concurrent
multi-user access. Switchable via `APME_DATABASE_URL` environment variable.
The gateway uses an async ORM (e.g., SQLAlchemy + aiosqlite/asyncpg) that
supports both backends.

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

### REST API

Stateless operations that translate to unary gRPC calls and persist results:

```
POST   /api/v1/scans                  Initiate scan (path or repo URL)
GET    /api/v1/scans                  List scan history (paginated, filterable)
GET    /api/v1/scans/{scan_id}        Get scan result
DELETE /api/v1/scans/{scan_id}        Delete scan record

POST   /api/v1/format                 Format files
GET    /api/v1/health                 Aggregate health (gateway + backends)
GET    /api/v1/rules                  List all rules
GET    /api/v1/rules/{rule_id}        Rule detail

WS     /api/v1/fix                    FixSession WebSocket (HITL flow)
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
  Primary gRPC contract. Adding the web gateway changes zero engine code.
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
- **SQLite single-writer limitation** — concurrent writes are serialized.
  Acceptable for single-user V1; PostgreSQL upgrade path documented.
- **Gateway becomes a critical path** — if the gateway is down, the web UI is
  unavailable. The CLI continues to work independently (it talks to Primary
  directly).

### Neutral

- The gateway does not import `apme_engine`. It is a pure gRPC client using
  the generated proto stubs from `apme.v1`.
- The CLI is unaffected. It continues to work as before, connecting directly
  to Primary.
- `ScanCompleted` event emission (ADR-020) is optional — the engine works with
  or without a reporting endpoint configured.

## Implementation Notes

### Container

The gateway runs as its own container, deployed alongside (but outside) the
engine pod. It needs:

- Network access to Primary's gRPC port (50051)
- A mounted volume for SQLite (`/data`)
- Optional: mounted volume for local project scanning (`/workspace`)

### Dependencies (subject to ADR-019 governance)

- `fastapi` + `uvicorn` — async HTTP/WebSocket server
- `grpcio` + `grpcio-tools` — gRPC client
- `sqlalchemy` + `aiosqlite` — async SQLite ORM
- `apme.v1` proto stubs (shared with CLI)

### Phased Implementation

- **Phase 4a**: Gateway backend — REST API, gRPC client, SQLite, health
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
- DR-008: Data persistence (resolved by this ADR — SQLite in gateway)

## References

- [design-dashboard.md](/.sdlc/context/design-dashboard.md) — Detailed UI
  design, SQLite schema, REST API, PatternFly components
- [FastAPI WebSocket](https://fastapi.tiangolo.com/advanced/websockets/)
- [gRPC Python async](https://grpc.github.io/grpc/python/grpc_asyncio.html)

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-19 | AI Agent | Initial proposal |
