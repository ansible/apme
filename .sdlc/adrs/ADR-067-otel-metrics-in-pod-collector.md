# ADR-067: OpenTelemetry Metrics with In-Pod Collector

## Status

Implemented

## Date

2026-07-29

## Context

APME needs **operational metrics** (scan duration, phase timings, venv acquire
outcomes, Galaxy fetch/wheel cache, HTTP server latency) that survive beyond a
single request and can be scraped, alerted on, and charted. ADR-013 already
delivers **per-scan structured diagnostics** on the gRPC response path for
CLI/UI display. Those concerns are complementary, not interchangeable:

| Concern | Consumer | Lifetime |
|---------|----------|----------|
| ADR-013 diagnostics | CLI, UI, FixSession response | One scan |
| Operational metrics | Prometheus/Grafana, platform collectors | Continuous |

Constraints and drivers:

- Reference deployment is a multi-container Podman pod sharing localhost
  (ADR-004, ADR-005). Primary, Gateway, and Galaxy Proxy all emit metrics.
- Align with the AAP direction for OTEL-based observability (emit OTLP;
  BYO/platform collector; do not bundle a logging product).
- Apps must stay **emitter-only**. They must not embed Prometheus scrape
  servers, vendor-specific agents, or customer collector credentials.
- Prefer **one controlled egress** per pod over N containers each unicasting
  OTLP to an external endpoint (auth, TLS, retry, and failure isolation).
- Local development still needs a scrape target and dashboards without
  requiring a customer OpenShift stack.

## Decision

**1. OpenTelemetry is the standard for APME operational metrics.**  
Instrumented services export OTLP/HTTP. Metric names use the `apme.*`
namespace. Export is opt-in via `OTEL_EXPORTER_OTLP_ENDPOINT` (no-op when
unset). Misconfiguration must never crash the service.

**2. An in-pod OpenTelemetry Collector aggregates emission.**  
App containers send OTLP/HTTP to `http://127.0.0.1:4318`. The collector
sidecar exposes Prometheus scrape on `:8889` for local/dev and is the
future attachment point for optional OTLP forward-out to a customer or
platform collector (tracked separately).

**3. Companion Prometheus/Grafana is local-dev only.**  
`containers/observability/` scrapes the collector hostPort; it is not a
product dependency and must not ship as the production observability stack.

This ADR **does not supersede ADR-013**. Structured diagnostics remain the
source of truth for per-scan timing embedded in the gRPC contract.

This ADR **amends** the practical HTTP surface under architectural
invariant 2 (gRPC between backend services): business traffic stays gRPC;
the in-pod OTel Collector may expose OTLP (`:4318`) and Prometheus scrape
(`:8889`) as observability endpoints.

## Alternatives Considered

### Alternative 1: Prometheus client libraries in each process

**Description**: Each service exposes `/metrics` with `prometheus_client`
(or equivalent) and is scraped independently.

**Pros**:
- Familiar scrape model
- No collector sidecar

**Cons**:
- Multiplies scrape endpoints and auth surface
- Couples apps to Prometheus wire format
- Poor fit for AAP “emit OTLP, BYO collector” direction
- Harder to add traces/logs later under one pipeline

**Why not chosen**: Violates emitter-only posture and fragments the scrape
topology across every container.

### Alternative 2: Every container unicasts OTLP to an external collector

**Description**: Each instrumented process sets
`OTEL_EXPORTER_OTLP_ENDPOINT` to a customer/platform collector URL.

**Pros**:
- No in-pod aggregator
- Matches single-process microservice diagrams

**Cons**:
- N copies of egress config, retries, and TLS
- Partial failure when one container is misconfigured
- Breaks the “one pod, one observability egress” model for localhost pods
- Harder to offer a stable local Prom scrape during development

**Why not chosen**: APME’s co-located pod makes a single in-pod aggregator
strictly simpler and safer than fan-out from every container.

### Alternative 3: Metrics only via ADR-013 diagnostics / log scraping

**Description**: Rely on ScanDiagnostics and/or log aggregation for timing.

**Pros**:
- No new infrastructure

**Cons**:
- Diagnostics die with the response; no continuous series
- Log scraping is brittle for histograms/counters
- Cannot express venv/Galaxy cache hit rates across scans cleanly

**Why not chosen**: Insufficient for operational SLIs and dashboards.

### Alternative 4: Vendor agent / bundled observability product in-pod

**Description**: Ship a commercial or productized agent as the aggregator.

**Pros**:
- Turnkey UI in some environments

**Cons**:
- Contradicts BYO/platform collector direction
- License and supply-chain weight for a reference pod

**Why not chosen**: Collector stays vendor-neutral OpenTelemetry Collector.

## Consequences

### Positive

- One instrumentation API (OTel) across Primary, Gateway, and Galaxy Proxy.
- Apps remain dumb emitters to localhost; collector owns export policy.
- Local Prom scrape (`:8889`) works without external dependencies.
- Forward-out to platform/BYO collectors can be added on the sidecar without
  rewiring every app.
- Coexists cleanly with ADR-013 per-scan diagnostics.

### Negative

- One more container in the reference pod and (eventually) Helm engine
  Deployment.
- Operators must understand OTLP vs Prometheus scrape roles.
- Label cardinality discipline is mandatory (bucket coarse attributes;
  never put secrets or raw userinfo into attributes).

### Neutral

- gRPC OTLP (`:4317`) may be enabled on the collector later; P0 uses
  OTLP/HTTP (`:4318`) only.
- Traces and logs over OTLP are out of scope for this ADR; metrics land
  first.
- Companion Grafana password and loopback binds are local-dev security
  hygiene, not production IAM.

## Implementation Notes

- Setup: `apme_engine.observability.otel_setup.setup_otel` — no-op unless
  `OTEL_EXPORTER_OTLP_ENDPOINT` is set; never raises to callers.
- Instruments: `apme_engine.observability.metrics` (`apme.scan.*`,
  `apme.venv.acquire.*`, `apme.galaxy.fetch.*`, `apme.galaxy.wheel.serve.*`,
  `apme.http.server.*`, `apme.grpc.server.*`).
- Validator RPC middleware: `GrpcMetricsInterceptor` via
  `apme_engine.daemon.validator_grpc.start_validator_server` (all six
  validators). Distinct from Primary's `apme.validator.duration` (ADR-013).
- Pod: `otel-collector` in `containers/podman/pod.yaml`; config under
  `containers/observability/` / collector config in-tree.
- Local dashboards: `containers/observability/up.sh` (Prometheus + Grafana
  on loopback).
- OTLP forward-out from the sidecar: https://github.com/ansible/apme/issues/457
- Helm: engine Deployment should co-locate the collector with the engine
  stack (ADR-054); do not require Gateway/UI Deployments to emit unless
  they gain independent metrics needs.

## Acceptance Criteria

- [x] Instrumented services (Primary, Gateway, Galaxy Proxy, and the six
      validators via `apme.grpc.server.*`) emit OTLP/HTTP only when
      `OTEL_EXPORTER_OTLP_ENDPOINT` is set; otherwise metrics are no-op and
      startup never fails on OTel misconfiguration.
- [x] Reference pod includes an `otel-collector` sidecar; apps target
      `http://127.0.0.1:4318`; Prometheus scrape is available on `:8889`.
- [x] Metric attributes avoid high-cardinality and secret leakage (coarse
      buckets; hostname-only server labels — no URL userinfo).
- [x] ADR-013 structured diagnostics remain on the gRPC path; this ADR does
      not replace them.
- [x] Local companion stack (`containers/observability/`) is documented as
      optional and binds to loopback only.
- [ ] Optional OTLP forward-out from the collector to a platform/BYO
      endpoint (https://github.com/ansible/apme/issues/457).

## Phase Assignment

Not assigned to PHASE-001–004. Operational metrics are a **cross-cutting
platform** concern (engine pod + Gateway), not a feature slice of the CLI
scanner, rewrite engine, dashboard, or AI remediation phases in
`.sdlc/phases/README.md`. Delivery tracks with PR #456 / follow-up #457.

## Verification

```bash
# Lint / ADR index / typecheck (tox only)
tox -e lint

# Unit coverage for OTel setup, instruments, and Galaxy metric labels
tox -e unit -- tests/test_observability_metrics.py tests/test_galaxy_proxy_server.py --no-cov

# Optional live stack (requires built images)
tox -e up
curl -sf http://127.0.0.1:8889/metrics | head
./containers/observability/up.sh
```

## Related Decisions

- ADR-004: Podman pod as deployment unit (collector is a sibling container)
- ADR-005: No service discovery / localhost addressing (OTLP to `127.0.0.1`)
- ADR-012: Scale pods, not services (collector scales with the engine unit)
- ADR-013: Structured diagnostics in gRPC (complementary; not superseded)
- ADR-033: Centralized log bridge (logs path; metrics use OTel instead)
- ADR-054: Production Helm/bootc deployment (collector belongs with engine)

## References

- PR: https://github.com/ansible/apme/pull/456
- Follow-up: https://github.com/ansible/apme/issues/457 (OTLP forward-out)
- AAP Decision Record — Log Forwarding OTEL Backend (ANSTRAT-1730): emit
  OTLP; BYO/platform collector; do not bundle a logging product

## Revision History

| Date | Change |
|------|--------|
| 2026-07-29 | Initial — OTel metrics standard + in-pod collector aggregation |
| 2026-07-29 | Add acceptance criteria, phase assignment, tox verification |
| 2026-07-29 | Note validator gRPC server middleware (`apme.grpc.server.*`) |
