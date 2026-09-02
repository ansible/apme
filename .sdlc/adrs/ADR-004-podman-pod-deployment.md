# ADR-004: Podman Pod as Local Development and Single-Node Deployment Unit

## Status

Implemented

## Date

2026-02

## Scope

**This ADR covers local development and single-node (non-Kubernetes) deployments
only.** For Kubernetes and OpenShift deployments, see
[ADR-054](ADR-054-production-deployment.md) which specifies the APME Operator.

| Environment | Deployment method | ADR |
|-------------|-------------------|-----|
| Developer laptop / workstation | Podman pod (`tox -e up`) | **This ADR** |
| Linux server without Kubernetes | Podman pod or bootc VM | **This ADR**, [ADR-054](ADR-054-production-deployment.md) |
| Kubernetes / OpenShift | **APME Operator** | [ADR-054](ADR-054-production-deployment.md) |

> **If you are deploying on Kubernetes or OpenShift, stop here and follow
> [ADR-054](ADR-054-production-deployment.md) and the
> [apme-operator](https://github.com/ansible/apme-operator) repository.** Do not use `podman play kube`
> or raw pod YAML on K8s/OCP.

## Context

APME runs multiple services:
- Engine (orchestrator + session venv manager)
- Native validator
- OPA validator
- Ansible validator
- Galaxy Proxy (PEP 503 collection proxy)
- Gitleaks validator (optional)
- Collection Health validator (optional)
- Dep Audit validator (optional)
- CLI

We needed a deployment model for **local development and single-node evaluation**.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Docker Compose | Widely adopted, good local dev | Requires Docker daemon, not rootless-friendly |
| Podman pod | Rootless, Kubernetes-compatible YAML, no daemon | Less tooling maturity |
| Kubernetes directly | Production-grade orchestration | Heavy for local dev |
| Single monolithic process | Simple deployment | No isolation, no independent scaling |

## Decision

**Use a Podman pod for local development and single-node deployments.**

- All backend services share `localhost` within the pod
- The CLI runs on-the-fly outside the pod with a CWD volume mount
- For Kubernetes/OpenShift production deployments, the APME Operator is provided
  separately (see [ADR-054](ADR-054-production-deployment.md))

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
- Not suitable for Kubernetes/OpenShift — use the APME Operator (ADR-054) instead

## Implementation Notes

- Pod spec: `containers/podman/pod.yaml`
- Services share localhost within pod
- CLI invoked via `podman run --rm` with volume mount
- Fixed ports: 50051–50056 for gRPC services, 8765 for Galaxy Proxy

## Related Decisions

- ADR-005: No etcd/service discovery
- ADR-012: Scale pods, not services
- ADR-048: Pod-internal admin endpoints rely on network isolation — depends on shared-localhost assumption established here
- **ADR-054: Production Deployment — APME Operator for Kubernetes/OpenShift, bootc for VM**
