# APME Helm Chart

Deploy APME on Kubernetes or OpenShift. The chart models the architecture
as defined in [ADR-054](../../../.sdlc/adrs/ADR-054-production-deployment.md):
the engine stack runs as sidecar containers in a single pod (preserving
localhost networking per ADR-005 and pod-level scaling per ADR-012).

## Chart repository (OpenShift / `helm repo add`)

Packaged chart releases are published to a classic HTTP Helm repository on
GitHub Pages:

| | |
|--|--|
| **Repo URL** | `https://ansible.github.io/apme` |
| **Index** | `https://ansible.github.io/apme/index.yaml` |

> **Ops:** Enable GitHub Pages on the `ansible/apme` repository with source
> **Deploy from a branch → `gh-pages` / root**. Chart releases are created by
> `.github/workflows/helm-charts.yml` (chart-releaser) when
> `deploy/helm/apme/**` changes on `main`.

### CLI

```bash
helm repo add apme https://ansible.github.io/apme
helm repo update
helm install apme apme/apme \
  --namespace apme --create-namespace \
  --set route.enabled=true   # OpenShift
```

Defaults pull from `quay.io/ansible` with image tag `2026.7.3` (`Chart.appVersion`).
For unreleased SHA builds, set `--set image.tag=sha-<commit>`.

### OpenShift Developer Catalog

1. **UI:** Developer perspective → **Helm** → **Create** → add chart repository
   with URL `https://ansible.github.io/apme`, then install **apme** from the
   catalog (enable Route / set values as needed).
2. **Cluster-scoped CR** (admin):

```yaml
apiVersion: helm.openshift.io/v1beta1
kind: HelmChartRepository
metadata:
  name: apme
spec:
  name: APME
  connectionConfig:
    url: https://ansible.github.io/apme
```

3. **Namespace-scoped CR** (project member with RBAC):

```yaml
apiVersion: helm.openshift.io/v1beta1
kind: ProjectHelmChartRepository
metadata:
  name: apme
  namespace: my-project
spec:
  name: APME
  connectionConfig:
    url: https://ansible.github.io/apme
```

## Prerequisites

- Kubernetes 1.26+ or OpenShift 4.14+
- Helm 3.x
- Access to `quay.io/ansible` (default pull registry) or a mirror. CI always
  publishes to `ghcr.io/ansible` and publishes to Quay when credentials are set
- Default image tag is pinned to `2026.7.3` (GitHub release `v2026.7.3`; must
  match Chart.appVersion). Override with `--set image.tag=…` for another
  release or a SHA build (e.g. `sha-b7d1683`)

## Quick start

### From the chart repository (recommended)

```bash
helm repo add apme https://ansible.github.io/apme
helm repo update
helm install apme apme/apme --namespace apme --create-namespace
```

### From a local clone (contributors)

```bash
# From the repository root (uses quay.io/ansible + tag 2026.7.3 by default)
helm install apme ./deploy/helm/apme/

# With AI enabled (OpenRouter provider)
helm install apme ./deploy/helm/apme/ \
  --set abbenay.enabled=true \
  --set abbenay.token=$APME_ABBENAY_TOKEN \
  --set-json 'abbenay.providers={"openrouter":{"engine":"openrouter","apiKey":"'$OPENROUTER_API_KEY'","models":{"anthropic/claude-sonnet-4-6":{}}}}'
```

Lint and package locally with `tox -e helm` (writes `dist/charts/*.tgz`).

## Architecture

```
┌─────────── Engine Pod (scaled as unit) ──────────┐
│  Primary  Native  OPA  Ansible  Gitleaks         │
│  Collection-Health  Dep-Audit  Galaxy-Proxy      │
│  (validators via localhost gRPC; Galaxy Proxy via HTTP) │
└──────────────────────────────────────────────────┘

┌──────────┐   ┌──────────┐   ┌──────────┐
│ Gateway  │   │    UI    │   │ Abbenay  │
│ (deploy) │   │ (deploy) │   │ (deploy) │
└──────────┘   └──────────┘   └──────────┘
```

- **Engine Deployment**: All validators run as sidecars in one pod. HPA scales
  the entire engine stack together.
- **Gateway Deployment**: REST API + gRPC Reporting + SQLite persistence.
- **UI Deployment** (optional): nginx-served React SPA, proxies `/api/` to Gateway.
  Disable with `ui.enabled: false` when an external UI (e.g. automation portal)
  consumes the Gateway API.
- **Abbenay Deployment** (optional): AI provider for Tier 2 remediation.

## Key values

| Value | Default | Description |
|-------|---------|-------------|
| `image.registry` | `quay.io/ansible` | Container registry |
| `image.tag` | `2026.7.3` | Image tag (GitHub release `v2026.7.3`; Quay omits the `v`) |
| `engine.replicas` | `1` | Engine pod replicas |
| `gitleaks.enabled` | `true` | Enable Gitleaks validator |
| `collectionHealth.enabled` | `true` | Enable Collection Health validator |
| `depAudit.enabled` | `true` | Enable Dependency Audit validator |
| `gateway.replicas` | `1` | Gateway replicas |
| `ui.enabled` | `true` | Deploy standalone UI (set `false` for portal-only) |
| `ui.replicas` | `1` | UI replicas (when `ui.enabled`) |
| `abbenay.enabled` | `false` | Enable AI provider |
| `abbenay.token` | `""` | Abbenay service token (required when `abbenay.enabled=true`) |
| `abbenay.image` | `ghcr.io/redhat-developer/abbenay:2026.4.1-alpha` | Abbenay image |
| `abbenay.providers` | `{}` | LLM provider map (see [ABBENAY_AI.md](../../../docs/guides/ABBENAY_AI.md)) |
| `abbenay.aiModel` | `""` | Default AI model ID |
| `ingress.enabled` | `false` | Create Kubernetes Ingress |
| `route.enabled` | `false` | Create OpenShift Route |
| `autoscaling.enabled` | `false` | Enable HPA for engine |
| `autoscaling.maxReplicas` | `5` | Max engine replicas under HPA |
| `networkPolicy.enabled` | `false` | Enable NetworkPolicy |
| `podDisruptionBudget.enabled` | `false` | Enable PDB |
| `persistence.sessions.size` | `10Gi` | Session venv PVC size |
| `persistence.gateway.size` | `5Gi` | Gateway DB PVC size |

See [`values.yaml`](values.yaml) for the complete reference with all resource
limits, tolerations, affinity, and topology spread constraints.

## Exposing the UI and API

### Kubernetes Ingress

```yaml
ingress:
  enabled: true
  className: nginx
  hosts:
    - host: apme.example.com
      paths:
        - path: /api
          pathType: Prefix
          service: gateway
        - path: /
          pathType: Prefix
          service: ui
  tls:
    - secretName: apme-tls
      hosts:
        - apme.example.com
```

### OpenShift Route

```yaml
route:
  enabled: true
  host: apme.apps.ocp.example.com
  tls:
    termination: edge
    insecureEdgeTerminationPolicy: Redirect
```

When `host` is empty, OpenShift auto-assigns separate hosts per Route.

### Portal / external UI (backend only)

When automation portal or another Backstage instance is the presentation
layer, deploy APME without the standalone UI and expose only the Gateway:

```yaml
ui:
  enabled: false

# image.tag defaults to 2026.7.3 on quay.io/ansible

route:
  enabled: true
  host: apme-api.apps.ocp.example.com
```

With `ui.enabled: false`, the API Route serves the Gateway at `/` (no `/api`
path prefix). Portal plugins should reach the Gateway via in-cluster DNS,
e.g. `http://<release>-gateway:8080`.

## Scaling

Enable the HPA to auto-scale the engine based on CPU/memory:

```yaml
autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70
```

Each engine replica is a complete stack — scale pods, not individual
services within a pod (ADR-012).

For multi-replica deployments, override the engine strategy:

```yaml
engine:
  strategy:
    type: RollingUpdate
```

## OpenShift compatibility

The chart works under OpenShift's `restricted-v2` SCC without modification.
APME application container images are built on **UBI10** Application Stream
bases (ADR-061).

- `podSecurityContext` and `securityContext` default to empty (OCP injects UID/GID)
- The UI container mounts emptyDir volumes for nginx writable paths
- No privilege escalation is required

For vanilla Kubernetes, set explicit security contexts (UBI images run as UID 1001):

```yaml
podSecurityContext:
  fsGroup: 1001
securityContext:
  runAsNonRoot: true
  runAsUser: 1001
```

`fsGroup` ensures PVC mounts for `/sessions`, `/data`, and `/cache` are writable by
the application UID. Local Podman uses the same PVC definitions in
`containers/podman/pvc.yaml` (with `volume.podman.io/uid` annotations).

## Uninstall

```bash
helm uninstall apme
```

PVCs are not deleted automatically. Remove them manually if desired:

```bash
kubectl delete pvc -l app.kubernetes.io/instance=apme
```

## Related

- [Deployment Guide](../../../docs/guides/DEPLOYMENT.md) — Overview of all deployment methods
- [ADR-054](../../../.sdlc/adrs/ADR-054-production-deployment.md) — Architecture rationale
- [Scaling docs](../../../docs/architecture/17-scaling-and-deployment.md) — Scaling model
