# ADR-054: Production Deployment — Kubernetes Operator and bootc VM Image

## Status

Accepted (revised 2026-09-02: Helm chart removed; Kubernetes deployment via [apme-operator](https://github.com/ansible/apme-operator))

## Date

2026-04-10

## Context

APME's reference deployment is a single Podman pod (`containers/podman/pod.yaml`)
with 12 containers sharing localhost networking. This works well for development
and single-node evaluation but does not address production deployment:

- **Kubernetes** is the standard for multi-node, scaled, and managed deployments.
  ADR-004 chose K8s-shaped YAML intentionally as a stepping stone but the
  pod.yaml uses `hostPath` volumes and `hostPort` mappings that do not translate
  directly to production K8s.
- **VM-based deployment** is required for air-gapped, edge, and compliance
  environments. bootc (image-based Linux) provides atomic, reproducible OS
  images that ship applications alongside the OS, enabling consistent VM
  provisioning.

### Decision Drivers

- ADR-012 (scale pods not services) defines the scaling unit: the full engine
  stack (Engine + validators + Galaxy Proxy) replicates as a unit.
- ADR-005 (no service discovery) uses `127.0.0.1:<port>` for intra-pod
  communication. This works identically in Kubernetes pods (containers in the
  same pod share localhost).
- The 12 containers in the reference pod naturally co-locate for single-site installs:
  - **Engine stack** (8 containers): Engine, Native, OPA, Ansible, Gitleaks,
    Collection Health, Dep Audit, Galaxy Proxy
  - **Gateway** (1 container): REST + Reporting + SQLite
  - **Frontend** (1 container): UI nginx (optional via portal profile)
  - **Abbenay** (1 container): Optional AI provider
  - **Observability** (1 container): OpenTelemetry Collector sidecar (ADR-067;
    included in reference `pod.yaml`, optional in minimal profiles)

## Decision

**APME will provide Kubernetes deployment via the APME Operator
([ansible/apme-operator](https://github.com/ansible/apme-operator)) and bootc image
definitions with systemd quadlet files for VM deployment.**

### 1. Kubernetes — APME Operator

Kubernetes and OpenShift deployments are owned by the **APME Operator** repository
(`ansible/apme-operator`), not this repo. The operator reconciles custom resources
into the same **all-in-one** pod topology as the Podman reference deployment:
engine sidecars + Gateway + UI + optional Abbenay on localhost (ADR-005).

#### Workload topology

| K8s Resource | Containers | Scaling |
|-------------|------------|---------|
| Deployment (all-in-one) | engine, native, opa, ansible, gitleaks*, collection-health*, dep-audit*, galaxy-proxy, gateway, ui*, abbenay*, otel-collector* | Operator-managed (single-replica default) |

\* optional via operator configuration

#### Networking

In-stack containers communicate via `127.0.0.1:<port>` (ADR-005), except
Engine→Abbenay gRPC which uses a shared Unix socket when a consumer token
is set (`abbenay-client` ≥ 2026.8.7 rejects tokens on plaintext TCP).
External access uses Service + Ingress/Route:

| From | To | Address |
|------|-----|---------|
| Containers (intra-pod) | Each other | `127.0.0.1:<port>` (Engine→Abbenay gRPC: Unix socket) |
| UI (browser) / external API | Gateway REST | Ingress/Route → Service `:8080` |
| Hosted CI / in-cluster clients | Engine gRPC | ClusterIP `<name>-engine.<namespace>.svc:50051` (see below) |
| Engine | Gateway Reporting | `127.0.0.1:50060` |
| Engine | Abbenay | Unix socket `unix:///tmp/abbenay-run/abbenay/daemon.sock` |
| Gateway | Abbenay admin | HTTP `127.0.0.1:8787` (ADR-070; loopback only) |

#### Hosted CI Engine access

The operator reconciles a ClusterIP Service `<Apme.metadata.name>-engine` on
port `50051` (plaintext gRPC; Engine binds with `add_insecure_port`). Gateway
REST is the only product edge exposed via Route/Ingress. When NetworkPolicy is
enabled, Gateway `:8080` and UI `:8081` accept ingress from the edge; Engine
`:50051` stays off Route/Ingress but must permit ingress from approved hosted
CI runners and in-cluster clients (for example via NetworkPolicy peer labels or
named runner namespaces).

Hosted GitHub Actions set `APME_ENGINE_ADDRESS` to a `host:port` the runner can
reach:

- **In-cluster or VPN-connected runners:** `<name>-engine.<namespace>.svc:50051`
- **bootc VM / Podman:** host-accessible `:50051` (see [DEPLOYMENT.md](../../docs/guides/DEPLOYMENT.md))
- **GitHub-hosted runners on the public internet:** require VPN, peering, or a
  self-hosted runner with cluster network access; the operator does not expose
  Engine gRPC on Route/Ingress in v1

Engine gRPC has no transport TLS or application-level auth in operator v1 —
restrict reachability with firewall rules and NetworkPolicy. Crossing untrusted
networks requires VPN, peering, or an equivalent encrypted overlay; do not
expose plaintext Engine gRPC on the public internet. See
[apme-operator](https://github.com/ansible/apme-operator) for Service and
exposure details.

#### Storage

| PVC | Access Mode | Used By | Purpose |
|-----|-------------|---------|---------|
| `sessions` | ReadWriteOnce | APME pod | Session venvs (Engine rw, validators ro) |
| `gateway-data` | ReadWriteOnce | APME pod (Gateway) | SQLite database |
| `proxy-cache` | ReadWriteOnce | APME pod | Galaxy Proxy wheel cache |

#### Secrets

SCM tokens, API keys, and Abbenay credentials are managed via Kubernetes
Secrets through the operator's CRD and reconciliation logic.

**Install documentation:** [https://github.com/ansible/apme-operator](https://github.com/ansible/apme-operator)

### 2. bootc VM Image (`deploy/bootc/`)

A bootc Containerfile builds an OCI image that can be converted to qcow2, raw,
or AMI for VM provisioning. The image ships Podman and uses systemd quadlet
files for service management.

#### Quadlet structure

Podman quadlet files (`.container`, `.pod`) are the modern replacement for
`podman generate systemd`. They are declarative, support templating via
environment files, and integrate natively with systemd.

| File | Type | Purpose |
|------|------|---------|
| `apme.pod` | Pod | Defines the pod and published ports |
| `apme-engine.container` | Container | Engine orchestrator |
| `apme-native.container` | Container | Native validator |
| `apme-opa.container` | Container | OPA validator |
| `apme-ansible.container` | Container | Ansible validator |
| `apme-gitleaks.container` | Container | Gitleaks validator |
| `apme-collection-health.container` | Container | Collection health |
| `apme-dep-audit.container` | Container | Dependency audit |
| `apme-galaxy-proxy.container` | Container | Galaxy Proxy |
| `apme-gateway.container` | Container | Gateway + REST API |
| `apme-ui.container` | Container | UI nginx |

#### Build and deployment workflow

1. Build: `podman build -f deploy/bootc/Containerfile -t apme-bootc:latest`
2. Convert: `bootc-image-builder` → qcow2/raw/AMI
3. Deploy: fresh install or `bootc switch`
4. Configure: `/etc/apme/env/` for API keys and settings

## Alternatives Considered

### Alternative 1: Split Engine Services into Separate Deployments

**Description**: Each validator gets its own Deployment + Service in K8s.

**Pros**: Fine-grained scaling, independent resource limits.

**Cons**: Violates ADR-012 (scale pods not services). Requires service
discovery or DNS for intra-engine communication, breaking ADR-005.
Significantly more complex networking and debugging.

**Why not chosen**: ADR-012 explicitly decided against this. The sidecar model
preserves localhost semantics and scales the stack as a unit.

### Alternative 2: In-repo Helm Chart

**Description**: Ship a Helm chart at `deploy/helm/apme/` for K8s/OCP installs.

**Pros**: Familiar packaging; parameterization via values files.

**Cons**: Duplicates deployment logic that belongs in a dedicated operator;
chart maintenance diverged from production reconciliation needs.

**Why not chosen**: Kubernetes deployment is consolidated in
[ansible/apme-operator](https://github.com/ansible/apme-operator). The in-repo
Helm chart was removed (2026-09-02). See superseded [ADR-069](ADR-069-helm-simple-all-in-one.md).

### Alternative 3: k3s Embedded in bootc

**Description**: Ship k3s inside the bootc image and deploy via the operator.

**Pros**: Uses the same operator for both K8s and VM-with-k3s deployments.

**Cons**: k3s adds ~100MB and a control plane to the VM image. Overkill for
single-node deployments. Podman + quadlets are simpler and lighter for
the VM use case.

**Why not chosen**: Quadlets are the recommended systemd integration for
Podman on single-node VMs.

## Consequences

### Positive

- **Standard K8s deployment**: Install the operator and create an APME custom
  resource for a production-ready deployment with Services, PVCs, and Ingress.
- **Preserves architecture**: All-in-one pod keeps ADR-005 (localhost); ADR-012’s
  engine unit remains the conceptual scale boundary.
- **Reproducible VMs**: bootc images are atomic and reproducible. `bootc switch`
  enables zero-downtime upgrades.
- **Aligned topologies**: Operator, Podman pod, and bootc/quadlet all
  co-locate the stack on localhost for single-site installs.
- **Separation of concerns**: Application code (this repo) vs cluster lifecycle
  (apme-operator repo).

### Negative

- **Cross-repo coordination**: Operator releases must track container image tags
  published from this repo.
- **Testing gap**: Operator requires a K8s cluster to test. bootc requires
  `bootc-image-builder` which needs a Linux host with specific capabilities.
- **PVC storage classes**: Users must have appropriate StorageClasses configured.

### Neutral

- The existing Podman pod workflow is unchanged. `tox -e up` continues to work
  for development.
- Container images are unchanged — the same GHCR images are used by the operator,
  bootc, and Podman.

## Related Decisions

- ADR-004: Podman pod deployment (reference deployment, K8s-shaped YAML)
- ADR-005: No service discovery (localhost within pod)
- ADR-012: Scale pods, not services (engine stack is the scaling unit)
- ADR-029: Web Gateway architecture (Gateway role)
- ADR-034: Multi-pod health registration (Gateway aggregation)
- ADR-035: Secret externalization (token management)
- ADR-063: Multi-platform container image publish (amd64 + arm64)
- ADR-069: Helm Simple all-in-one topology (**superseded** — Helm removed)

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-04-10 | APME Team | Initial acceptance (Helm + bootc) |
| 2026-08-03 | APME Team | Helm topology amended by ADR-069 (Simple all-in-one) |
| 2026-08-24 | APME Team | Engine→Abbenay uses a shared Unix socket |
| 2026-09-02 | APME Team | Helm chart removed; K8s/OCP via apme-operator |
