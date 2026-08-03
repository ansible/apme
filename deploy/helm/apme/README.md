# APME Helm Chart

Deploy APME on Kubernetes or OpenShift for **EAP / upstream** installs.
[ADR-069](../../../.sdlc/adrs/ADR-069-helm-simple-all-in-one.md) defines a
**Simple all-in-one** Deployment: engine + Gateway + UI + optional Abbenay in
one pod on localhost ([ADR-005](../../../.sdlc/adrs/ADR-005-no-service-discovery.md)).
`replicas` must be `1`.

## Chart repository (OpenShift / `helm repo add`)

Packaged chart releases are published to a classic HTTP Helm repository on
GitHub Pages:

| | |
|--|--|
| **Repo URL** | `https://ansible.github.io/apme` |
| **Index** | `https://ansible.github.io/apme/index.yaml` |
| **Values profiles** | `https://ansible.github.io/apme/values-portal.yaml`, `https://ansible.github.io/apme/values-standalone.yaml` |

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

> **Observability:** The reference Podman pod includes an OpenTelemetry Collector
> (ADR-067) on ports `:4318` (OTLP) and `:8889` (Prometheus). The Helm chart does
> not deploy this collector — configure OTLP export separately for Kubernetes if needed.

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
- Cluster nodes on `linux/amd64` or `linux/arm64`. Tags published by CI after
  [ADR-063](../../../.sdlc/adrs/ADR-063-multi-platform-container-images.md) are
  multi-arch manifest lists (older release tags remain amd64-only until rebuilt)

## Quick start

Install flavors are **named values files** shipped with the chart (one chart;
ADR-030 Options A and B). Chart `values.yaml` keeps the standalone UI on by
default so a bare `helm install` is not a footgun for SPA evaluators.

| Profile | File | UI | Use when |
|---------|------|----|----------|
| Standalone SPA | [`values-standalone.yaml`](values-standalone.yaml) | on | Bundled PatternFly UI (default) |
| Portal / backend | [`values-portal.yaml`](values-portal.yaml) | off | Automation portal / Backstage / Gateway API only |

### Standalone UI (default)

```bash
helm repo add apme https://ansible.github.io/apme
helm repo update
helm install apme apme/apme \
  --namespace apme --create-namespace \
  -f https://ansible.github.io/apme/values-standalone.yaml \
  --set route.enabled=true   # OpenShift
```

Omit `-f values-standalone.yaml` if you want the same UI-on default without an
explicit profile (the file is equivalent to chart defaults).

### Portal / backend-only

No standalone UI Deployment — suitable for automation portal and other
Backstage integrations that consume the Gateway API (ADR-030 Option B).

```bash
helm repo add apme https://ansible.github.io/apme
helm repo update
helm install apme apme/apme \
  --namespace apme --create-namespace \
  -f https://ansible.github.io/apme/values-portal.yaml \
  --set route.enabled=true   # OpenShift
```

### From a local clone (contributors)

```bash
# Standalone (chart default)
helm install apme ./deploy/helm/apme/

# Portal / backend-only
helm install apme ./deploy/helm/apme/ \
  -f ./deploy/helm/apme/values-portal.yaml

# With AI enabled (OpenRouter provider)
helm install apme ./deploy/helm/apme/ \
  --set abbenay.enabled=true \
  --set abbenay.token=$APME_ABBENAY_TOKEN \
  --set-json 'abbenay.providers={"openrouter":{"engine":"openrouter","apiKey":"'$OPENROUTER_API_KEY'","models":{"anthropic/claude-sonnet-4-6":{}}}}'
```

Lint and package locally with `tox -e helm` (writes `dist/charts/*.tgz`).

## Breaking change (pre-ADR-069 → Simple)

If you installed an older chart with **separate** Gateway / UI / Abbenay
Deployments:

| Before | After (this chart) |
|--------|--------------------|
| 4 Deployments | 1 Deployment (`*-engine`) |
| Abbenay Service DNS | Abbenay on `127.0.0.1` only (Service removed) |
| Optional engine HPA | HPA / `replicas > 1` fail render |
| Gateway/UI Services | Same names; selectors target the Simple pod |

PVC names are stable. Plan a maintenance window: the engine pod restart takes
Gateway DB and Abbenay down together.

## Architecture

```
┌──────────── Simple pod (replicas: 1) — ADR-069 ────────────┐
│  Primary  Native  OPA  Ansible  Gitleaks*                  │
│  Collection-Health*  Dep-Audit*  Galaxy-Proxy              │
│  Gateway  UI*  Abbenay*                                    │
│  (all via 127.0.0.1; Abbenay binds loopback, no TLS)       │
└────────────────────────────────────────────────────────────┘
```

- **Simple Deployment**: Full stack as sidecars. ClusterIP Services
  (`-engine`, `-gateway`, `-ui`) select this pod for Ingress/port-forward.
- **UI** (optional): nginx SPA; `ui.enabled: false` via `values-portal.yaml`
  for portal / Backstage (ADR-030 Option B).
- **Abbenay** (optional): AI provider gRPC on `127.0.0.1:50057` plus HTTP
  admin on `127.0.0.1:8787` (no Service / hostPort). Gateway reverse-proxies
  **allowlisted** admin paths under `/api/v1/ai/` → Abbenay `/api/` (config,
  providers, provider configure/delete; not chat/sessions/OpenAI-compat) —
  see [ADR-070](../../../.sdlc/adrs/ADR-070-gateway-abbenay-admin-proxy.md).
  `GET /api/v1/ai/models` remains Primary `ListAIModels`.

## Key values

| Value | Default | Description |
|-------|---------|-------------|
| `image.registry` | `quay.io/ansible` | Container registry |
| `image.tag` | `2026.7.3` | Image tag (GitHub release `v2026.7.3`; Quay omits the `v`) |
| `engine.replicas` | `1` | Must be `1` (ADR-069) |
| `gitleaks.enabled` | `true` | Enable Gitleaks validator |
| `collectionHealth.enabled` | `true` | Enable Collection Health validator |
| `depAudit.enabled` | `true` | Enable Dependency Audit validator |
| `gateway.replicas` | `1` | Must be `1` (Gateway sidecar) |
| `ui.enabled` | `true` | Include UI sidecar (`false` via `values-portal.yaml`) |
| `ui.replicas` | `1` | Must be `1` when UI enabled |
| `abbenay.enabled` | `false` | Enable AI provider sidecar |
| `abbenay.token` | `""` | Abbenay gRPC + HTTP admin token (required when `abbenay.enabled=true`) |
| `abbenay.image` | `ghcr.io/redhat-developer/abbenay:2026.4.1-alpha` | Abbenay image |
| `abbenay.providers` | `{}` | LLM provider map (see [ABBENAY_AI.md](../../../docs/guides/ABBENAY_AI.md)) |
| `abbenay.aiModel` | `""` | Default AI model ID |
| `ingress.enabled` | `false` | Create Kubernetes Ingress |
| `route.enabled` | `false` | Create OpenShift Route |
| `autoscaling.enabled` | `false` | Must stay `false` (ADR-069) |
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
layer, install with the portal values profile (or the equivalent override)
and expose only the Gateway:

```bash
helm install apme apme/apme \
  -f https://ansible.github.io/apme/values-portal.yaml \
  --set route.enabled=true \
  --set route.host=apme-api.apps.ocp.example.com
```

Equivalent values fragment (already in [`values-portal.yaml`](values-portal.yaml)):

```yaml
ui:
  enabled: false

route:
  enabled: true
  host: apme-api.apps.ocp.example.com
```

With `ui.enabled: false`, the API Route serves the Gateway at `/` (no `/api`
path prefix). Portal plugins should reach the Gateway via in-cluster DNS,
e.g. `http://<release>-gateway:8080`.

### Standalone UI

The chart default (and [`values-standalone.yaml`](values-standalone.yaml))
deploys the bundled React SPA:

```yaml
ui:
  enabled: true

route:
  enabled: true
  host: apme.apps.ocp.example.com
```

With `ui.enabled: true`, OpenShift Routes expose the UI at `/` and the
Gateway API at `/api`.

## Scaling

The chart is **Simple / single-replica** (ADR-069). Setting
`engine.replicas > 1` or `autoscaling.enabled: true` fails Helm render.
Multi-replica engine farms need a future topology ADR (Gateway SQLite and
Abbenay cannot share a scaled pod without redesign).

## OpenShift compatibility

The chart works under OpenShift's `restricted-v2` SCC without modification.
APME application container images are built on **UBI10** Application Stream
bases (ADR-061). CI publishes **multi-arch** (`linux/amd64` + `linux/arm64`)
manifest lists under the same tags after ADR-063 (rebuild release tags to pick
that up).

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
