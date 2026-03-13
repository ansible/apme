# ADR-020: Reporting Service and Event Delivery Model

## Status

Proposed

## Date

2026-03

## Context

The APME engine is stateless compute — it takes Ansible content in, produces violations and risk scores out, and returns results via gRPC or the CLI. There is no database and no persistence.

A persistence layer will become necessary when the project adds an executive dashboard for cost-savings and time-saved metrics. Tracking those numbers over time, across runs, and across projects requires durable storage.

Two architectural observations shape this decision:

1. **Persistence is a presentation concern, not an engine concern.** The engine's job is scanning. The dashboard's job is storing and displaying trends. These are different responsibilities owned by different services.

2. **APME scales by running multiple engine pods (ADR-012).** Each pod has its own isolated filesystem. Embedded storage (SQLite, local files) in the engine pod cannot serve a shared dashboard — there is no single source of truth. Persistence must live in a service that aggregates results from all engine pods.

## Decision

**No persistence layer yet.** This ADR documents the target architecture so the design is informed rather than reactive when the reporting service is built.

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

### Event Delivery: Fire-ACK with Health-Gated Emission

The engine does not queue, retry, or buffer events. Delivery works as follows:

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
| PostgreSQL | Battle-tested, concurrent writes, rich query language | Adds infrastructure; justified if multi-user dashboard access is needed |
| SQLite + Alembic | Zero-infrastructure, proven migration tooling | Single-writer; may suffice if only one reporting pod runs |
| DuckDB | Columnar, excellent for analytics/dashboards, embedded | Newer ecosystem |

### 4. Type safety and governance

Per ADR-018, all code must pass `mypy --strict`. Per ADR-019, any new dependency must pass the governance checklist.

## Related Decisions

- ADR-001: gRPC for inter-service communication (the event delivery mechanism)
- ADR-004: Podman pod as deployment unit (reporting service adds a pod to the topology)
- ADR-007: Fully async gRPC servers (health check and event emission follow the same async patterns)
- ADR-012: Scale pods, not services (multiple engine pods require centralized persistence)
- ADR-018: mypy strict mode (reporting service must be fully typed)
- ADR-019: Dependency governance (DB dependency must pass the checklist)
