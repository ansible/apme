# ADR-034: Multi-Pod Health Registration

## Status

Proposed

## Date

2026-03-23

## Context

The current `/api/v1/health` endpoint (PR #69) probes a **hardcoded list** of upstream services via env-var-configured addresses:

```python
_UPSTREAM_SERVICES: list[tuple[str, str, str]] = [
    ("Primary Orchestrator", "APME_PRIMARY_ADDRESS", "127.0.0.1:50051"),
    ("Native Validator",     "NATIVE_GRPC_ADDRESS",  "127.0.0.1:50055"),
    ...
]
```

This works for single-pod and local daemon deployments where all services share `localhost`. It breaks in multi-pod topologies (ADR-012) because:

1. **Unknown pod count.** The gateway cannot enumerate N engine pods from static env vars. Each pod is a self-contained stack (Primary + validators + Galaxy Proxy), and pods are created/destroyed dynamically by an orchestrator.

2. **No registration path.** ADR-005 rejected etcd/service-discovery for the single-pod case. That decision is correct *within* a pod (fixed ports, known services), but does not address the *between-pod* problem: how does the gateway know which pods exist?

3. **Health is unidirectional.** The gateway currently pulls health by probing addresses it already knows. In a multi-pod world, the gateway has no addresses to probe until pods announce themselves.

4. **ADR-020 already established a push channel.** Engine pods push `ScanCompleted`/`FixCompleted` events to the gateway's Reporting service via gRPC. Extending this channel with a heartbeat is a natural, low-cost addition.

### Forces in tension

- **ADR-005**: No service discovery infrastructure (etcd, Consul) — still correct
- **ADR-012**: Scale pods, not services — pods must self-announce to a central aggregation point
- **ADR-020**: Engine → gateway push model already exists for events — reusable for health
- **Operational simplicity**: Operators should not have to manually configure pod addresses in the gateway

## Decision

**Push-based registration with TTL and selective pull verification.**

Engine pods announce themselves to the gateway via periodic heartbeat messages over the existing gRPC Reporting channel. The gateway maintains a registry of known pods with TTL-based expiry. On health queries, the gateway returns the registry and optionally probes pods nearing TTL expiry.

### Design

```
                         heartbeat (10s interval)
┌──────────────┐   ────────────────────────────────▶  ┌──────────────────┐
│ Engine Pod 1 │    PodHeartbeat{pod_id, services,    │                  │
│              │     started_at, version, load}       │     Gateway      │
└──────────────┘                                      │                  │
                                                      │  ┌────────────┐ │
┌──────────────┐   ────────────────────────────────▶  │  │  Registry  │ │
│ Engine Pod 2 │                                      │  │  (TTL=30s) │ │
└──────────────┘                                      │  └────────────┘ │
                                                      │                  │
┌──────────────┐   ────────────────────────────────▶  │  GET /health     │
│ Engine Pod N │                                      │  → all pods      │
└──────────────┘                                      └──────────────────┘
```

### Heartbeat message

```protobuf
message PodHeartbeat {
  string pod_id       = 1;  // stable identifier (hostname or UUID)
  string version      = 2;  // apme-engine version
  map<string, string> services = 3;  // name → address (e.g. "primary" → "10.0.1.5:50051")
  google.protobuf.Timestamp started_at = 4;
  float cpu_load      = 5;  // optional: enables future load-aware routing
}

// Added to Reporting service
rpc PodHeartbeat(PodHeartbeat) returns (ReportAck);
```

### Gateway registry

- In-memory dictionary keyed by `pod_id`, storing the latest heartbeat + received timestamp
- Entries expire after `TTL` (default 30s, configurable)
- Expired entries are pruned lazily on health queries and periodically by a background task
- No persistent storage needed — pods re-announce on restart

### Health endpoint behavior

| Scenario | Behavior |
|----------|----------|
| **Single pod (current)** | Falls back to static `_UPSTREAM_SERVICES` list when registry is empty — fully backward-compatible |
| **Multi-pod** | Returns all registered pods with per-service health status from the last heartbeat |
| **Pod nearing TTL** | Gateway optionally does an on-demand gRPC probe to distinguish "slow heartbeat" from "actually dead" before marking unavailable |
| **Pod crash** | TTL expires → pod removed from registry on next prune cycle |

### Engine-side integration

The `GrpcReportingSink` already runs a background health loop every 10s. The heartbeat piggybacks on this loop:

```python
async def _health_loop(self) -> None:
    while True:
        await asyncio.sleep(_HEALTH_INTERVAL_S)
        await self._probe()
        if self._available:
            await self._send_heartbeat()  # new: announce to gateway
```

No new connections, no new dependencies. The heartbeat uses the same gRPC channel and stub.

## Alternatives Considered

### Alternative 1: Pure pull (gateway probes registered addresses)

**Description**: Pods register on startup via a `POST /internal/register` endpoint. Gateway stores the address list and probes all pods on health queries.

**Pros**:
- Gateway verifies health directly (not trusting self-reports)
- Detects network partitions (pod thinks it's healthy but gateway can't reach it)

**Cons**:
- Startup ordering dependency: gateway must be reachable when a pod starts
- Explicit deregistration needed for clean shutdown (or TTL fallback anyway)
- Gateway becomes the active prober, scaling O(N) probes per health query
- No natural crash recovery — requires TTL anyway, converging to the chosen approach

**Why not chosen**: Introduces a startup ordering constraint and reimplements TTL-based expiry, which the push model handles more naturally.

### Alternative 2: External service discovery (Consul, etcd, Kubernetes endpoints)

**Description**: Delegate registration and health to infrastructure tooling.

**Pros**:
- Mature, battle-tested solutions
- Built-in health checking, DNS, and load balancing
- Kubernetes Endpoints API is zero-config for k8s deployments

**Cons**:
- Adds infrastructure dependencies (violates ADR-005 spirit)
- Overkill for Podman-based deployments where there is no orchestrator
- Different solution needed per deployment target (Podman vs. k8s vs. bare metal)

**Why not chosen**: APME must work in Podman-only environments with no orchestrator. External service discovery is welcome as an optional overlay (e.g. Kubernetes Endpoints) but cannot be the primary mechanism.

### Alternative 3: Shared database polling

**Description**: Pods write their status to a shared database (the gateway's SQLite or a separate Redis). Gateway reads the table on health queries.

**Pros**:
- Durable registration survives gateway restarts
- Simple query model

**Cons**:
- SQLite doesn't support concurrent writes from multiple pods (file locking)
- Adds a database dependency to the engine (violates ADR-020's "engine never imports a database client")
- Redis adds infrastructure

**Why not chosen**: Violates the engine's stateless design principle (ADR-020) and introduces shared-state coordination.

## Consequences

### Positive

- Multi-pod health visibility with zero operator configuration
- Backward-compatible: single-pod deployments work unchanged
- Reuses existing gRPC channel — no new ports, protocols, or dependencies
- Natural crash recovery via TTL expiry
- Foundation for future load-aware routing (heartbeat carries `cpu_load`)
- ADR-005 remains valid: no etcd, no external service discovery

### Negative

- Heartbeat adds ~1 small RPC every 10s per pod — negligible but nonzero
- 30s TTL means up to 30s stale health data after a pod crash
- In-memory registry is lost on gateway restart (pods re-announce within 10s)

### Neutral

- Does not change intra-pod service discovery (ADR-005 still governs that)
- Does not change the scaling model (ADR-012 still governs that)
- The heartbeat is a superset of the existing health probe — could eventually replace it

## Implementation Notes

### Phase 1: Heartbeat proto + gateway registry (this ADR)

1. Add `PodHeartbeat` message and RPC to `reporting.proto`
2. Implement in-memory registry in gateway's Reporting servicer
3. Extend `/api/v1/health` to merge static services + registered pods
4. Add heartbeat sender to `GrpcReportingSink._health_loop`

### Phase 2: UI and operational tooling

1. Update `HealthPage.tsx` to show per-pod breakdown
2. Add `apme-cli pods` command to list registered pods
3. Add Prometheus metrics for pod count, heartbeat latency

### Phase 3: Load-aware routing (future)

1. Use `cpu_load` from heartbeats to weight routing decisions
2. Gateway forwards scan requests to least-loaded pod
3. Requires a routing layer in front of Primary (separate ADR)

### Backward compatibility

When the registry is empty (no pods have sent heartbeats), the health endpoint falls back to the current static `_UPSTREAM_SERVICES` list. This means:

- Existing single-pod deployments work with zero changes
- Existing daemon-mode deployments work with zero changes
- Multi-pod deployments get automatic registration once the engine is updated

## Related Decisions

- ADR-005: No service discovery — still valid for intra-pod; this ADR addresses inter-pod
- ADR-012: Scale pods, not services — pods self-announce to gateway
- ADR-020: Reporting service and event delivery — heartbeat extends the same push channel
- ADR-029: Web gateway architecture — health endpoint is a gateway concern
- ADR-048: Pod-internal admin endpoints rely on network isolation — if inter-pod routing exposes admin endpoints, auth must be added per ADR-048

## References

- PR #69: health endpoint implementation that motivated this discussion
- `src/apme_gateway/api/router.py`: current static `_UPSTREAM_SERVICES` list
- `src/apme_engine/daemon/sinks/grpc_reporting.py`: existing health loop to extend

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-23 | cidrblock | Initial proposal |
