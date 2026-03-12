# ADR-004: Podman Pod as Deployment Unit

## Status

Accepted

## Date

2026-02

## Context

APME runs multiple services:
- Primary (orchestrator)
- Native validator
- OPA validator
- Ansible validator
- Gitleaks validator
- Cache Maintainer
- CLI

We needed a deployment model.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Docker Compose | Widely adopted, good local dev | Requires Docker daemon, not rootless-friendly |
| Podman pod | Rootless, Kubernetes-compatible YAML, no daemon | Less tooling maturity |
| Kubernetes directly | Production-grade orchestration | Heavy for local dev |
| Single monolithic process | Simple deployment | No isolation, no independent scaling |

## Decision

**Use a Podman pod.**

- All backend services share `localhost` within the pod
- The CLI runs on-the-fly outside the pod with a CWD volume mount

## Rationale

- Podman is rootless and daemon-less — better security posture for developer workstations
- The pod spec is Kubernetes-compatible YAML, easing future migration
- Shared localhost within the pod means fixed port assignments, no service discovery
- The CLI is ephemeral (run and exit), not a long-running service

> "Use podman and a pod not docker. The CLI container should be 'on the fly' since it will need the CWD volume mount." — user decision

## Consequences

### Positive
- Rootless security
- No daemon dependency
- Kubernetes-compatible specs
- Simple localhost networking

### Negative
- Less tooling maturity than Docker
- Team needs Podman familiarity

## Implementation Notes

- Pod spec: `deploy/pod.yaml`
- Services share localhost within pod
- CLI invoked via `podman run --rm` with volume mount
- Fixed ports: 50051-50056 for validators

## Related Decisions

- ADR-005: No etcd/service discovery
- ADR-012: Scale pods, not services
