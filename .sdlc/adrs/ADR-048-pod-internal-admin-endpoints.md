# ADR-048: Pod-Internal Admin Endpoints Rely on Network Isolation

## Status

Accepted

## Date

2026-04-01

## Context

The Galaxy Proxy exposes a `POST /admin/galaxy-config` endpoint that
accepts Galaxy server credentials pushed from the Gateway (ADR-045).
This endpoint stores authentication tokens in memory and controls which
upstream Galaxy/Automation Hub servers are used for collection downloads.

The endpoint is **unauthenticated** — it has no shared-secret header,
mTLS, or middleware guard.  This was flagged during code review as a
potential security concern: anyone with network access to the proxy
could overwrite credentials and redirect downloads.

However, in the current deployment topology (ADR-004, ADR-005) **all
engine services share a single pod's localhost network**.  The Galaxy
Proxy listens on `127.0.0.1:8765` and is not exposed outside the pod.
The only caller is the co-located Gateway, also inside the pod.

Adding authentication for pod-internal HTTP endpoints would introduce
complexity (secret rotation, env-var plumbing, failure modes) with no
meaningful security benefit under the current topology.  The risk
emerges only if the topology changes — specifically if the Galaxy Proxy
is extracted to a shared deployment (noted as a possibility in ADR-012)
or exposed on a routable network.

### Constraints

- ADR-004: All backend services share localhost within the pod.
- ADR-005: No service discovery; services are found by fixed
  `localhost:port` assignments.
- ADR-012: Pods are the scaling unit; Galaxy Proxy extraction to a
  shared service is an acknowledged future possibility.
- ADR-034 (proposed): Multi-pod health registration introduces
  inter-pod addressing, but does not change intra-pod assumptions.

### Decision Drivers

- Simplicity: avoid unnecessary auth plumbing for localhost traffic.
- Defence in depth: ensure the assumption is documented and traceable
  so it is revisited when topology evolves.
- ADR-012 explicitly notes Galaxy Proxy may be extracted.

## Decision

**Pod-internal admin endpoints (e.g. `POST /admin/galaxy-config`) do
not require authentication while all communicating services share a
single pod's localhost network.**

If the deployment topology changes such that an admin endpoint becomes
reachable from outside its pod — for example, Galaxy Proxy extraction
to a shared service (ADR-012) or multi-pod routing (ADR-034) — then
authentication **must** be added before that change ships.

Acceptable mechanisms at that point include:
- Shared-secret header validated against an env var
- mTLS between Gateway and Proxy
- Network policy restricting the route to known callers

## Alternatives Considered

### Alternative 1: Add shared-secret auth now

**Description**: Require a `X-Admin-Token` header on `/admin/galaxy-config`,
validated against an env var injected into both Gateway and Proxy containers.

**Pros**:
- Defence in depth even under current topology
- No future work when topology changes

**Cons**:
- Adds secret management (env var, rotation) with no current threat
- Extra failure mode: misconfigured secret silently breaks config sync
- Violates YAGNI — the attack surface doesn't exist today

**Why not chosen**: The current topology provides equivalent isolation.
The cost of the mechanism exceeds the risk it mitigates.  This ADR
ensures the assumption is revisited when that changes.

### Alternative 2: Do nothing (no ADR)

**Description**: Leave the endpoint unauthenticated without documenting
the assumption.

**Pros**:
- No effort

**Cons**:
- The topology assumption is invisible; future developers may extract
  the proxy without realising the endpoint needs hardening
- Review feedback recurs on every related PR

**Why not chosen**: Undocumented security assumptions are a liability.
An ADR makes the assumption explicit and links it to the ADRs that
could invalidate it.

## Consequences

### Positive

- Zero additional complexity for pod-internal admin traffic.
- The assumption is documented and cross-referenced in every ADR that
  could change the topology (ADR-004, ADR-005, ADR-012, ADR-034,
  ADR-045), reducing the chance it is overlooked.

### Negative

- If a topology change ships without updating this ADR, the admin
  endpoint would be exposed without auth.  Mitigation: cross-references
  in related ADRs and the AGENTS.md invariant on pod scaling (invariant 6).

### Neutral

- This ADR does not prescribe which auth mechanism to use when the time
  comes — that decision should be captured in a new ADR at that point.

## Implementation Notes

- **No code changes required now.**  The endpoint remains unauthenticated.
- When ADR-012's "Galaxy Proxy Exception" is implemented, or ADR-034's
  multi-pod registration introduces routable inter-pod traffic, revisit
  this ADR and add auth before merging the topology change.
- A lightweight check: any PR that changes the Galaxy Proxy's listen
  address away from `127.0.0.1` or modifies pod networking should
  trigger a review of this ADR.

## Related Decisions

- [ADR-004](ADR-004-podman-pod-deployment.md): Podman Pod as Deployment
  Unit — establishes the shared-localhost assumption
- [ADR-005](ADR-005-no-service-discovery.md): No Service Discovery —
  fixed `localhost:port` assignments within a pod
- [ADR-012](ADR-012-scale-pods-not-services.md): Scale Pods, Not
  Services — notes Galaxy Proxy extraction as a future possibility
- [ADR-034](ADR-034-multi-pod-health-registration.md): Multi-Pod Health
  Registration — introduces inter-pod addressing (proposed)
- [ADR-045](ADR-045-galaxy-auth-delegation.md): Galaxy Auth Delegation —
  the admin endpoint this ADR covers

## References

- [PR #183 review comment](https://github.com/ansible/apme/pull/183#discussion_r3023457489):
  Copilot review flagging unauthenticated admin endpoint

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-04-01 | APME Team | Initial proposal — accepted |
