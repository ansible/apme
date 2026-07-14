# ADR-054: Production Deployment — Helm Chart and bootc VM Image

## Status

Accepted

## Date

2026-04-10

## Context

APME's reference deployment is a single Podman pod (`containers/podman/pod.yaml`)
with 11 containers sharing localhost networking. This works well for development
and single-node evaluation but does not address production deployment:

- **Kubernetes** is the standard for multi-node, scaled, and managed deployments.
  No Helm chart or K8s manifests exist. ADR-004 chose K8s-shaped YAML
  intentionally as a stepping stone but the pod.yaml uses `hostPath` volumes
  and `hostPort` mappings that do not translate to production K8s.
- **VM-based deployment** is required for air-gapped, edge, and compliance
  environments. bootc (image-based Linux) provides atomic, reproducible OS
  images that ship applications alongside the OS, enabling consistent VM
  provisioning.

### Decision Drivers

- ADR-012 (scale pods not services) defines the scaling unit: the full engine
  stack (Primary + validators + Galaxy Proxy) replicates as a unit.
- ADR-005 (no service discovery) uses `127.0.0.1:<port>` for intra-pod
  communication. This works identically in Kubernetes pods (containers in the
  same pod share localhost).
- ADR-029 (Gateway architecture) positions Gateway as an independent service
  that can scale separately from the engine.
- The 11 containers in the pod naturally divide into three groups:
  - **Engine stack** (8 containers): Primary, Native, OPA, Ansible, Gitleaks,
    Collection Health, Dep Audit, Galaxy Proxy — always co-located
  - **Gateway** (1 container): Scales independently, owns persistence
  - **Frontend** (1 container): UI nginx, stateless, scales independently
  - **Abbenay** (1 container): Optional AI provider, separate concerns

## Decision

**APME will provide a Helm chart for Kubernetes deployment and bootc image
definitions with systemd quadlet files for VM deployment.**

### 1. Helm Chart (`deploy/helm/apme/`)

The chart uses the sidecar model for the engine stack, preserving ADR-005's
localhost networking. Gateway, UI, and Abbenay are separate Deployments.

#### Workload topology

| K8s Resource | Containers | Scaling |
|-------------|------------|---------|
| Deployment `engine` | primary, native, opa, ansible, gitleaks, collection-health, dep-audit, galaxy-proxy | Replicas (HPA optional) |
| Deployment `gateway` | gateway | Independent replicas |
| Deployment `ui` | ui (nginx) | Independent replicas |
| Deployment `abbenay` | abbenay | Optional, independent |

#### Networking

Inside the engine pod, all containers communicate via `127.0.0.1:<port>` —
identical to the Podman pod (ADR-005). Cross-Deployment communication uses
Kubernetes Service DNS names:

| From | To | Address |
|------|-----|---------|
| Engine containers (intra-pod) | Each other | `127.0.0.1:<port>` |
| Gateway | Primary | `{{ release }}-engine:50051` |
| Gateway | Collection Health | `{{ release }}-engine:50058` |
| UI (browser) | Gateway REST | Ingress → `{{ release }}-gateway:8080` |
| Engine | Gateway Reporting | `{{ release }}-gateway:50060` |

#### Storage

| PVC | Access Mode | Used By | Purpose |
|-----|-------------|---------|---------|
| `sessions` | ReadWriteOnce | Engine pod | Session venvs (Primary rw, validators ro) |
| `gateway-data` | ReadWriteOnce | Gateway | SQLite database |
| `proxy-cache` | ReadWriteOnce | Engine pod | Galaxy Proxy wheel cache |

ReadWriteOnce is sufficient because each engine replica has its own sessions
and proxy cache. If a shared Galaxy Proxy cache is needed across replicas,
extract it as a separate Deployment with ReadWriteMany (per ADR-012's Galaxy
Proxy Exception).

#### Secrets

SCM tokens, API keys, and Abbenay credentials are managed via Kubernetes
Secrets. The chart currently supports native Kubernetes `Secret` resources
and inline values rendered into those secrets. Support for
external-secrets-operator `ExternalSecret` resources is not yet implemented.

#### Chart distribution (HTTP repository)

Chart **source** lives in-repo at `deploy/helm/apme/`. Packaged releases are
published to a classic Helm HTTP repository on GitHub Pages
(`https://ansible.github.io/apme`) via chart-releaser
(`.github/workflows/helm-charts.yml`). That URL is what OpenShift
`HelmChartRepository` / Developer Catalog and `helm repo add` consume.
OCI chart refs are out of scope for the OpenShift console path.

On release tags `vYYYY.M.P` (CalVer), container CI publishes matching image
tags (without the leading `v`). `Chart.appVersion` must stay aligned with
that tag so Catalog installs with an empty `image.tag` pull real images.

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
| `apme-primary.container` | Container | Primary orchestrator |
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

### Alternative 2: Kustomize Instead of Helm

**Description**: Use Kustomize overlays on the existing pod.yaml.

**Pros**: No template engine, native kubectl support.

**Cons**: pod.yaml uses `hostPath` and `hostPort` which require significant
patching. Kustomize cannot add new resources (Services, Ingress, PVCs)
as cleanly as Helm templates. No parameterization for image tags,
replicas, or feature toggles (Gitleaks, Abbenay).

**Why not chosen**: Helm's parameterization is essential for the variability
in APME's deployment (optional components, multiple scaling targets,
secret injection). Kustomize is better for simpler resource overlays.

### Alternative 3: k3s Embedded in bootc

**Description**: Ship k3s inside the bootc image and deploy the Helm chart.

**Pros**: Uses the same Helm chart for both K8s and VM deployments.

**Cons**: k3s adds ~100MB and a control plane to the VM image. Overkill for
single-node deployments. Podman + quadlets are simpler and lighter for
the VM use case.

**Why not chosen**: Quadlets are the recommended systemd integration for
Podman. They are simpler, lighter, and a better fit for single-node VM
deployments. The k3s path remains available for users who want it but is
not the default.

## Consequences

### Positive

- **Standard K8s deployment**: `helm repo add apme https://ansible.github.io/apme`
  then `helm install apme apme/apme` (or path install from `deploy/helm/apme`)
  gives a production-ready deployment with proper Services, PVCs, and Ingress.
  OpenShift can add the same URL as a Helm chart repository for Developer Catalog.
- **Preserves architecture**: Sidecar model keeps ADR-005 (localhost) and
  ADR-012 (scale pods) intact — no service discovery changes needed.
- **Reproducible VMs**: bootc images are atomic and reproducible. `bootc switch`
  enables zero-downtime upgrades.
- **Separated concerns**: Engine, Gateway, and UI scale independently in K8s.
  In the VM (single node), all services run in one pod as today.

### Negative

- **Maintenance surface**: Helm chart and bootc definitions must be kept in sync
  with container images and pod topology changes.
- **Testing gap**: Helm chart requires a K8s cluster to test. bootc requires
  `bootc-image-builder` which needs a Linux host with specific capabilities.
- **PVC storage classes**: Users must have appropriate StorageClasses configured.
  The chart uses the cluster default.

### Neutral

- The existing Podman pod workflow is unchanged. `tox -e up` continues to work
  for development.
- Container images are unchanged — the same GHCR images are used by Helm, bootc,
  and Podman.

## Related Decisions

- ADR-004: Podman pod deployment (reference deployment, K8s-shaped YAML)
- ADR-005: No service discovery (localhost within pod)
- ADR-012: Scale pods, not services (engine stack is the scaling unit)
- ADR-029: Web Gateway architecture (independent Gateway scaling)
- ADR-034: Multi-pod health registration (Gateway aggregation)
- ADR-035: Secret externalization (token management)
