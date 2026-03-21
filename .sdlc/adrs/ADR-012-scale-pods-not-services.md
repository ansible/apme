# ADR-012: Scale Pods, Not Services Within a Pod

## Status

Accepted

## Date

2026-02

## Context

When throughput needs to increase, where do we scale?

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

### Scaling

```bash
# Scale to 3 pods
kubectl scale deployment apme --replicas=3

# Or with Podman
for i in 1 2 3; do
  podman play kube pod.yaml --name apme-$i
done
```

### Load Balancer

```yaml
apiVersion: v1
kind: Service
metadata:
  name: apme-lb
spec:
  type: LoadBalancer
  selector:
    app: apme
  ports:
    - port: 50050
      targetPort: 50050
```

### Galaxy Proxy Exception

If a shared wheel cache is needed:
1. Extract Galaxy Proxy to a separate deployment
2. Point all pods at the shared proxy URL
3. Each pod's `uv` cache also provides a local acceleration layer

## Related Decisions

- ADR-004: Podman pod deployment
- ADR-005: No service discovery
