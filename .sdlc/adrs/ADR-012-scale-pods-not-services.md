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
- Primary
- Native validator
- OPA validator
- Ansible validator
- Gitleaks validator
- Cache Maintainer

To increase throughput, run more pods behind a load balancer.

## Rationale

- The pod is the natural unit for Kubernetes/Podman scaling
- No intra-pod service discovery or routing needed
- Each request is handled entirely within one pod — no cross-pod RPC
- The Cache Maintainer is the one exception that could be extracted to a shared service if multiple pods need a single cache volume

## Consequences

### Positive
- Simple scaling model
- Self-contained pods
- No cross-pod dependencies
- Natural Kubernetes fit

### Negative
- Resource duplication across pods
- Cache Maintainer may need extraction

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

### Cache Maintainer Exception

If shared cache is needed:
1. Extract Cache Maintainer to separate deployment
2. Mount shared volume across pods
3. Or use external cache (Redis)

## Related Decisions

- ADR-004: Podman pod deployment
- ADR-005: No service discovery
