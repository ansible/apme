# ADR-005: Reject etcd/Service Discovery for Single-Pod Deployment

## Status

Accepted

## Date

2026-02

## Context

An "Introspective Pod" design was proposed with etcd for service discovery, registration heartbeats, and client-side load balancing within the pod.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| etcd sidecar | Dynamic discovery, load metrics | Heavy (Raft consensus), unnecessary for fixed service set |
| Fixed-port env vars | Zero dependencies, simple, deterministic | Must update pod spec to add services |

## Decision

**Fixed-port environment variables.**

Each validator has a known port (50051–50056) and is discovered via env vars:
- `NATIVE_GRPC_ADDRESS`
- `OPA_GRPC_ADDRESS`
- `ANSIBLE_GRPC_ADDRESS`
- `GITLEAKS_GRPC_ADDRESS`
- etc.

No etcd, no registration, no heartbeats.

## Rationale

- Within a single pod, the service set is known at deploy time from the pod YAML
- etcd adds operational complexity (Raft cluster, persistence, health monitoring) for a problem that doesn't exist — there's no dynamic service topology
- If a validator is not configured (env var unset), Primary simply skips it — graceful degradation with zero infrastructure

## Consequences

### Positive
- Zero infrastructure dependencies
- Simple, deterministic discovery
- Graceful degradation when validators missing

### Negative
- Adding new validators requires pod spec update
- No dynamic load balancing within pod

## Implementation Notes

```yaml
# Example env vars in pod spec
- name: NATIVE_GRPC_ADDRESS
  value: "localhost:50051"
- name: OPA_GRPC_ADDRESS
  value: "localhost:50052"
- name: ANSIBLE_GRPC_ADDRESS
  value: "localhost:50053"
```

## Related Decisions

- ADR-004: Podman pod deployment
- ADR-012: Scale pods, not services
