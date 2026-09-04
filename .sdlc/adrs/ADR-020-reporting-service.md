# ADR-020: Reporting Service and Event Delivery Model

## Status

Implemented

## Date

2026-03

## Context

The APME engine is stateless compute — it takes Ansible content in, produces violations and risk scores out, and returns results via gRPC or the CLI. There is no database and no persistence.

A persistence layer will become necessary when the project adds an executive dashboard for cost-savings and time-saved metrics. Tracking those numbers over time, across runs, and across projects requires durable storage.

Two architectural observations shape this decision:

1. **Persistence is a presentation concern, not an engine concern.** The engine's job is scanning. The dashboard's job is storing and displaying trends. These are different responsibilities owned by different services.

2. **APME scales by running multiple engine pods (ADR-012).** Each pod has its own isolated filesystem. Embedded storage (local files) in the engine pod cannot serve a shared dashboard — there is no single source of truth. Persistence must live in a service that aggregates results from all engine pods.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Embed a database in the engine | Simple deployment, single binary | Engine becomes stateful; doesn't work with multiple pods (ADR-012); mixes concerns |
| Engine queues and retries events | Guaranteed delivery | Adds state to the engine; retry storms; partial message broker |
| Best-effort with ACK and health-gated emission | Engine stays stateless; self-healing; no queuing | Missing data during reporting outages |
| Message broker between engine and reporting | Guaranteed delivery, decoupled | Adds infrastructure (NATS, Redis); overkill until data completeness is critical |

## Decision

**No persistence layer yet.** This ADR documents the target architecture so the design is informed rather than reactive when the reporting service is built.

When built, the engine will emit events via gRPC using a **best-effort delivery model with health-gated emission**. The reporting service will own all persistence.

### Target Architecture

The engine emits structured `ScanCompleted` events via gRPC to a dedicated reporting service. The reporting service owns all persistence. The engine never imports a database client, never knows a schema, and never holds connection strings.

```
┌─────────────┐     gRPC ScanCompleted     ┌────────────────────┐
│ Engine Pod 1 │ ─────────────────────────▶ │                    │
└─────────────┘                            │  Reporting Service  │
┌─────────────┐     gRPC ScanCompleted     │                    │
│ Engine Pod 2 │ ─────────────────────────▶ │  ┌──────────────┐ │
└─────────────┘                            │  │   Database    │ │
┌─────────────┐     gRPC ScanCompleted     │  └──────────────┘ │
│ Engine Pod N │ ─────────────────────────▶ │                    │
└─────────────┘                            └────────────────────┘
       │                                            │
       │  grpc.health.v1                            │
       └────────── periodic health check ──────────▶│
```

### Event Delivery: Best-Effort with ACK and Health-Gated Emission

The engine uses a best-effort delivery model: it sends an event and expects an acknowledgment, but does not queue, retry, or buffer on failure. A periodic health check gates whether sends are even attempted, avoiding wasted timeouts when the reporting service is known to be down.

1. **Periodic health check.** Each engine pod runs a background gRPC health check against the reporting service (e.g. every 30 seconds) using the standard `grpc.health.v1.Health/Check` protocol. This maintains a boolean flag: `reporting_available`.

2. **Health-gated send.** When a scan completes, the engine checks the flag. If `True`, it sends the `ScanCompleted` event and expects an ACK. If `False`, the event is silently skipped — the scan result was already returned to the CLI user or gRPC caller.

3. **Self-healing.** The health check runs continuously. When the reporting service recovers, the flag flips back to `True` and subsequent scan events are delivered automatically. No operator intervention required.

4. **Failure during send.** If the flag is `True` but the send itself fails (reporting service crashed between health checks), the engine logs a warning and flips the flag to `False`. The health check poll will re-enable it when the service recovers.

### Why This Model

| Property | Benefit |
|----------|---------|
| No queuing in the engine | Engine stays stateless — no in-memory buffers, no write-ahead logs, no retry storms |
| No wasted timeouts | If the reporting service is known-down, the engine skips instantly instead of blocking on a connection timeout |
| Small miss window | With a 30-second health poll, the worst case is ~30 seconds of missed events after an outage begins — invisible on a trend chart |
| Self-healing | Engine pods recover automatically when the reporting service comes back; no restart needed |
| Independent scaling | N engine pods, 1 reporting pod; only the reporting service needs DB credentials |
| Independent schema evolution | Dashboard schema changes are internal to the reporting service; the engine's protobuf contract changes only if the shape of scan results changes |
| No impact on primary path | Scans never block or slow down because of reporting |

### Acceptable Trade-off: Missing Data During Outages

Scans that run while the reporting service is unreachable produce no dashboard data points. This is explicitly acceptable because:

- The scan result itself is always delivered to the caller (CLI output, gRPC response) — that's the primary path
- The dashboard shows trends over time; a few missing points during an outage window do not break trend lines
- If data completeness becomes critical in the future, the architecture can be upgraded to include a message broker (NATS, Redis Streams) between the engine and reporting service without changing the engine's API

## Rationale

- The engine's job is scanning — persistence is a presentation/dashboard concern that belongs in a separate service
- Multiple engine pods (ADR-012) rule out embedded storage as a shared source of truth
- Health-gated emission avoids wasted timeouts and keeps the scan path fast
- Best-effort delivery with health gating is the simplest mechanism that self-heals; a message broker can be added later if data completeness becomes critical
- Missing a few dashboard data points during an outage is acceptable for trend charts — the primary scan result is always delivered to the caller

## Consequences

### Positive

- Engine remains stateless — no database client, no connection strings, no schema knowledge
- Reporting service can evolve its database schema independently of the engine
- Scaling is independent: N engine pods, 1 reporting pod
- Self-healing recovery requires no operator intervention

### Negative

- Dashboard data has gaps during reporting service outages (acceptable trade-off)
- Adds a new service to the pod topology when implemented
- The `ScanCompleted` protobuf contract becomes a versioned API surface between engine and reporting service

### Reporting Must Be Configurable

Event emission to the reporting service must be toggleable. For local CLI use (developer workstation, ad-hoc scans), users may not have — or want — a reporting service running. For managed/enterprise deployments, reporting may be expected by default.

Whether the default is opt-in or opt-out is deferred to implementation time. The engine must support both modes via configuration (e.g. `--reporting-endpoint` flag or config file entry). When no reporting endpoint is configured, the engine skips all health checks and event emission with zero overhead.

## Requirements for the Reporting Service (when built)

### 1. Upgrade hassle must be minimal

Schema migrations are one of the largest sources of operational pain. The reporting service must handle its own data migrations transparently on upgrade — no DBA required. The upgrade path should be a container pull and restart.

### 2. What needs to be stored

At minimum (speculative, to be refined):

- Scan results per project/run (violations, severity counts, timestamps)
- Cost/time-saved metrics derived from remediation actions
- Trend data for dashboard charts (deltas between runs)
- Configuration state (which rules are enabled/disabled per project)

### 3. Database choice is an internal detail

The reporting service chooses its own persistence technology. Candidates to evaluate when the service is built:

| Option | Strengths | Considerations |
|--------|-----------|----------------|
| PostgreSQL | Battle-tested, concurrent writes, rich query language | Requires a PostgreSQL server (sidecar in Podman; external in bootc/Helm) |
| PostgreSQL + Alembic | Proven schema migration tooling | Same server requirement; single-writer per Gateway instance in Simple topology |
| DuckDB | Columnar, excellent for analytics/dashboards, embedded | Newer ecosystem |

### 4. Type safety and governance

Per ADR-018, all code must pass `mypy --strict`. Per ADR-019, any new dependency must pass the governance checklist.

## Implementation Notes (2026-03)

### Pluggable Event Sink Architecture

Engine event emission is implemented via a pluggable `EventSink` protocol
(`src/apme_engine/daemon/event_emitter.py`).  Sinks are registered at startup
and receive fan-out calls on scan/fix completion.  Each sink is best-effort:
failures are logged and never block the primary RPC path.

The initial concrete sink is `GrpcReportingSink`
(`src/apme_engine/daemon/sinks/grpc_reporting.py`), which pushes events to a
gRPC `Reporting` service defined in `proto/apme/v1/reporting.proto`.

Additional sinks (Elasticsearch, Prometheus, webhooks) can be added by
implementing `EventSink` and registering in `start_sinks()`.

### Event Types

- `FixCompletedEvent` — emitted when a `FixSession` reaches COMPLETE status
  (both check and remediate modes per ADR-039), including `ProposalOutcome`
  entries for approved/rejected proposals and `ProjectManifest` dependency
  data (ADR-040)

Both events carry `repeated ProgressUpdate logs` for pipeline milestone
capture (ADR-033).

### Configuration

Set `APME_REPORTING_ENDPOINT` (e.g. `localhost:50060`) to enable event
emission.  When unset, no sinks are loaded and zero overhead is incurred.

## Related Decisions

- ADR-001: gRPC for inter-service communication (the event delivery mechanism)
- ADR-004: Podman pod as deployment unit (reporting service adds a pod to the topology)
- ADR-007: Fully async gRPC servers (health check and event emission follow the same async patterns)
- ADR-012: Scale pods, not services (multiple engine pods require centralized persistence)
- ADR-018: mypy strict mode (reporting service must be fully typed)
- ADR-019: Dependency governance (DB dependency must pass the checklist)
- ADR-033: Centralized log bridge (pipeline logs included in event payloads)
