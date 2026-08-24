# ADR-005: Reject etcd/Service Discovery for Single-Pod Deployment

## Status

Implemented

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

Each service has a known fixed port and is discovered via env vars. Required
core range: Engine `50051`, Ansible `50053`, OPA `50054`, Native `50055`,
Galaxy Proxy `8765`. Optional validators: Gitleaks `50056`, Collection Health
`50058`, Dep Audit `50059`.

- `APME_ENGINE_ADDRESS` — Engine orchestrator gRPC (`host:port`)
- `APME_GALAXY_PROXY_URL` — Galaxy Proxy HTTP base URL (PEP 503 + `/health`)
- `NATIVE_GRPC_ADDRESS`
- `OPA_GRPC_ADDRESS`
- `ANSIBLE_GRPC_ADDRESS`
- `GITLEAKS_GRPC_ADDRESS`
- `COLLECTION_HEALTH_GRPC_ADDRESS`
- `DEP_AUDIT_GRPC_ADDRESS`
- etc.

No etcd, no registration, no heartbeats.

## Rationale

- Within a single pod, the service set is known at deploy time from the pod YAML
- etcd adds operational complexity (Raft cluster, persistence, health monitoring) for a problem that doesn't exist — there's no dynamic service topology
- **Required** engine-core services (Engine, Native, OPA, Ansible, Galaxy Proxy) must be configured; scans fail if any required dependency is missing or unhealthy
- **Optional** validators (Gitleaks, Collection Health, Dep Audit) may be omitted — Engine skips unset addresses with graceful degradation

## Consequences

### Positive
- Zero infrastructure dependencies
- Simple, deterministic discovery
- Graceful degradation for optional validators only; required validators are enforced at daemon startup and scan fan-out

### Negative
- Adding new validators requires pod spec update
- No dynamic load balancing within pod

## Implementation Notes

```yaml
# Example env vars in pod spec
- name: NATIVE_GRPC_ADDRESS
  value: "localhost:50055"
- name: OPA_GRPC_ADDRESS
  value: "localhost:50054"
- name: ANSIBLE_GRPC_ADDRESS
  value: "localhost:50053"
- name: GITLEAKS_GRPC_ADDRESS
  value: "localhost:50056"
```

## Related Decisions

- ADR-004: Podman pod deployment
- ADR-012: Scale pods, not services
- ADR-048: Pod-internal admin endpoints rely on network isolation — depends on fixed localhost:port assumption established here
