# ADR-012: Scale Pods, Not Services Within a Pod

## Status

Accepted

## Date

2026-02

## Context

When throughput needs to increase, where do we scale?

This ADR defines the **scaling unit** — the engine stack as an atomic group.
It does **not** prescribe the deployment tool. For deployment method guidance:

| Environment | Deployment method | ADR |
|-------------|-------------------|-----|
| Developer laptop / workstation | Podman pod (`tox -e up`) | [ADR-004](ADR-004-podman-pod-deployment.md) |
| Linux server without Kubernetes | Podman pod or bootc VM | [ADR-004](ADR-004-podman-pod-deployment.md), [ADR-054](ADR-054-production-deployment.md) |
| **Kubernetes / OpenShift** | **Helm chart** (`deploy/helm/apme/`) | [ADR-054](ADR-054-production-deployment.md) |

> **If you are scaling on Kubernetes or OpenShift, use the Helm chart
> (`deploy/helm/apme/`).** Do not use `podman play kube` on K8s/OCP.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Scale services within a pod (multiple Ansible validators + load balancer) | Fine-grained scaling | Requires service discovery (etcd), complex routing |
| Scale pods horizontally | Simple, self-contained | Each pod has a full copy of every service |

## Decision

**Scale pods, not services within a pod.**

Each pod is a self-contained stack:
- Primary (+ session venv manager)
- Native validator
- OPA validator
- Ansible validator
- Gitleaks validator
- Galaxy Proxy

To increase throughput, run more pods behind a load balancer.

## Rationale

- The pod is the natural unit for Kubernetes/Podman scaling
- No intra-pod service discovery or routing needed
- Each request is handled entirely within one pod — no cross-pod RPC
- The Galaxy Proxy could be extracted to a shared service if multiple pods need a single wheel cache

## Consequences

### Positive
- Simple scaling model
- Self-contained pods
- No cross-pod dependencies
- Natural Kubernetes fit

### Negative
- Resource duplication across pods
- Galaxy Proxy may need extraction for shared wheel cache

## Implementation Notes

### Scaling on Kubernetes / OpenShift (Helm chart)

The **conceptual** scaling unit remains the engine stack (this ADR). The
shipping Helm chart for EAP / upstream is **Simple all-in-one**
([ADR-069](ADR-069-helm-simple-all-in-one.md)): one pod with engine + Gateway +
UI + optional Abbenay, `replicas: 1`, HPA disabled. Multi-replica engine HPA
requires a future topology ADR (Gateway SQLite cannot share a scaled pod).

See [ADR-054](ADR-054-production-deployment.md) (amended by ADR-069) for the
chart shape.

### Scaling with Podman (local dev / single-node)

For local development or single-node Linux servers without Kubernetes:

```bash
# Run multiple pods with Podman
for i in 1 2 3; do
  podman play kube containers/podman/pod.yaml --name apme-$i
done
```

This is appropriate for development testing or non-Kubernetes servers only.

### Galaxy Proxy Exception

If a shared wheel cache is needed:
1. Extract Galaxy Proxy to a separate deployment
2. Point all pods at the shared proxy URL
3. Each pod's `uv` cache also provides a local acceleration layer

## Related Decisions

- ADR-004: Podman pod deployment (local dev and single-node)
- ADR-005: No service discovery
- ADR-048: Pod-internal admin endpoints rely on network isolation — if Galaxy Proxy is extracted (see "Galaxy Proxy Exception" above), auth must be added per ADR-048
- **ADR-054: Production Deployment — Helm chart for Kubernetes/OpenShift**
- **ADR-069: Helm Simple all-in-one (EAP/upstream chart topology)**

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-02 | APME Team | Initial acceptance |
| 2026-08-03 | APME Team | Helm notes amended for ADR-069 Simple (no multi-replica chart path) |
