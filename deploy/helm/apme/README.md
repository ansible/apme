# APME Helm Chart

Deploy APME on Kubernetes or OpenShift for **EAP / upstream** installs.
**[ADR-069](../../../.sdlc/adrs/ADR-069-helm-simple-all-in-one.md)** defines a
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

Create a Secret with the Gateway database URL before installing (write the URL to
a protected file so it is not exposed in shell history or process arguments):

```bash
kubectl create namespace apme --dry-run=client -o yaml | kubectl apply -f -
umask 077
tmpfile=$(mktemp)
printf '%s' 'postgresql+asyncpg://apme:CHANGE_ME@postgres.example:5432/apme?sslmode=verify-full' > "$tmpfile"
kubectl create secret generic apme-database \
  --namespace apme \
  --from-file=database-url="$tmpfile"
rm -f "$tmpfile"
```

```bash
helm repo add apme https://ansible.github.io/apme
helm repo update
helm install apme apme/apme \
  --namespace apme --create-namespace \
  --set postgres.enabled=false \
  --set gateway.database.existingSecret.name=apme-database \
  --set route.enabled=true \
  --set route.host=apme.apps.ocp.example.com
```

Replace `route.host` with a hostname under your cluster's OpenShift
ingress domain (for example `apme.apps.<cluster-domain>`) before installing —
`example.com` will not resolve.

Defaults pull from `quay.io/ansible` with image tag `2026.9.2` (`Chart.appVersion`).
For unreleased SHA builds, set `--set image.tag=sha-<commit>`.
The default `2026.9.2` APME image predates the `abbenay-client==2026.8.9`
refresh; when enabling Abbenay before the next APME release, set `image.tag` to
an image built from this change.

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
- Default image tag is pinned to `2026.9.2` (GitHub release `v2026.9.2`; must
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

The EAP Simple chart includes a PostgreSQL 16 sidecar and a persistent
`*-postgres-data` PVC by default. The chart creates a database password Secret
automatically and retains it across upgrades and uninstall; Gateway connects
to PostgreSQL over pod localhost. Set `postgres.password` explicitly when
rendering declaratively without cluster lookups (for example, offline GitOps
diffs).
For production installations, disable the sidecar and configure an external
PostgreSQL URL through `gateway.database.existingSecret` instead. External
database examples must also set `postgres.enabled=false`.

### Standalone UI (default)

```bash
helm repo add apme https://ansible.github.io/apme
helm repo update
helm install apme apme/apme \
  --namespace apme --create-namespace \
  -f https://ansible.github.io/apme/values-standalone.yaml \
  --set route.enabled=true \
  --set route.host=apme.apps.ocp.example.com
```

Omit `-f values-standalone.yaml` if you want the same UI-on default without an
explicit profile (the file is equivalent to chart defaults). Replace
`route.host` with a hostname under your cluster ingress domain — required
whenever Routes are enabled with the standalone UI.

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

The default local-clone install includes PostgreSQL in the Simple pod. To use an
external database, set `postgres.enabled=false` and provide
`gateway.database.existingSecret.name`.

```bash
# Standalone (chart default) — includes PostgreSQL
helm install apme ./deploy/helm/apme/

# Portal / backend-only
helm install apme ./deploy/helm/apme/ \
  -f ./deploy/helm/apme/values-portal.yaml

# With AI enabled (OpenRouter provider). Create the provider Secret out of
# band, and pass the Abbenay token through a protected temporary file so
# credentials are not exposed in shell history or process arguments.
set -eu
set -o pipefail
umask 077
test -n "${OPENROUTER_API_KEY:-}" && test -n "${APME_ABBENAY_TOKEN:-}"
openrouter_key_file=$(mktemp)
abbenay_token_file=$(mktemp)
trap 'rm -f "$openrouter_key_file" "$abbenay_token_file"' EXIT
kubectl create namespace apme --dry-run=client -o yaml | kubectl apply -f -
printf '%s' "$OPENROUTER_API_KEY" > "$openrouter_key_file"
kubectl create secret generic openrouter-credentials \
  --namespace apme \
  --from-file=api-key="$openrouter_key_file" \
  --dry-run=client -o yaml | kubectl apply -f -
printf '%s' "$APME_ABBENAY_TOKEN" > "$abbenay_token_file"
helm install apme ./deploy/helm/apme/ \
  --namespace apme --create-namespace \
  --set image.tag=sha-192169a0 \
  --set abbenay.enabled=true \
  --set-file abbenay.token="$abbenay_token_file" \
  --set-json 'abbenay.providers={"openrouter":{"engine":"openrouter","apiKeySecret":{"name":"openrouter-credentials","key":"api-key"},"models":{"anthropic/claude-sonnet-4-6":{}}}}'
```

Lint and package locally with `tox -e helm` (writes `dist/charts/*.tgz`).

## Breaking change (pre-ADR-069 → Simple)

If you installed an older chart with **separate** Gateway / UI / Abbenay
Deployments:

| Before | After (this chart) |
|--------|--------------------|
| 4 Deployments | 1 Deployment (`*-engine`) |
| Abbenay Service DNS | No Abbenay Service; HTTP admin on `127.0.0.1:8787`; Engine gRPC via Unix socket |
| Optional engine HPA | HPA / `replicas > 1` fail render |
| Gateway/UI Services | Same names; selectors target the Simple pod |

PVC names are stable. Plan a maintenance window: the engine pod restart takes
Gateway DB and Abbenay down together.

## Architecture

```
┌──────────── Simple pod (replicas: 1) — ADR-069 ────────────┐
│  Engine  Native  OPA  Ansible  Gitleaks*                  │
│  Collection-Health*  Dep-Audit*  Galaxy-Proxy              │
│  Gateway  UI*  Abbenay*                                    │
│  (localhost / Unix socket; Abbenay binds loopback, no TLS) │
└────────────────────────────────────────────────────────────┘
```

- **Simple Deployment**: Full stack as sidecars. ClusterIP Services
  (`-engine`, `-gateway`, `-ui`) select this pod for Ingress/port-forward.
- **UI** (optional): nginx SPA; `ui.enabled: false` via `values-portal.yaml`
  for portal / Backstage (ADR-030 Option B).
- **Abbenay** (optional): AI provider gRPC via Unix socket
  (`unix:///tmp/abbenay-run/abbenay/daemon.sock`) plus HTTP admin on
  `127.0.0.1:8787` (no Service / hostPort). TCP `127.0.0.1:50057` is leftover
  bind; Helm probes connect to the Unix socket. Gateway reverse-proxies
  **allowlisted** admin paths under `/api/v1/ai/` → Abbenay `/api/` (config,
  engines, providers, provider configure/delete; not chat/sessions/OpenAI-compat) —
  see [ADR-070](../../../.sdlc/adrs/ADR-070-gateway-abbenay-admin-proxy.md).
  `GET /api/v1/ai/models` remains Engine `ListAIModels`.

## Key values

| Value | Default | Description |
|-------|---------|-------------|
| `image.registry` | `quay.io/ansible` | Container registry |
| `image.tag` | `2026.9.2` | APME image tag (GitHub release `v2026.9.2`; stays here until the next APME release) |
| `engine.replicas` | `1` | Must be `1` (ADR-069) |
| `engine.galaxyProxy.logLevel` | `INFO` | Galaxy Proxy log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`) |
| `gitleaks.enabled` | `true` | Enable Gitleaks validator |
| `collectionHealth.enabled` | `true` | Enable Collection Health validator |
| `depAudit.enabled` | `true` | Enable Dependency Audit validator |
| `gateway.replicas` | `1` | Must be `1` (Gateway sidecar) |
| `ui.enabled` | `true` | Include UI sidecar (`false` via `values-portal.yaml`) |
| `ui.replicas` | `1` | Must be `1` when UI enabled |
| `abbenay.enabled` | `false` | Enable AI provider sidecar |
| `abbenay.token` | `""` | Abbenay gRPC + HTTP admin token (required when `abbenay.enabled=true`) |
| `abbenay.image` | `ghcr.io/redhat-developer/abbenay:v2026.8.9` | Abbenay daemon image (independent of `image.tag`) |
| `abbenay.providers` | `{}` | LLM provider map (see [ABBENAY_AI.md](../../../docs/guides/ABBENAY_AI.md)) |
| `abbenay.aiModel` | `""` | Default AI model ID |
| `ingress.enabled` | `false` | Create Kubernetes Ingress |
| `route.enabled` | `false` | Create OpenShift Route |
| `route.host` | `""` | Shared Route hostname; **required** when `route.enabled` and `ui.enabled` |
| `autoscaling.enabled` | `false` | Must stay `false` (ADR-069) |
| `networkPolicy.enabled` | `false` | Enable NetworkPolicy |
| `podDisruptionBudget.enabled` | `false` | Enable PDB |
| `persistence.sessions.size` | `10Gi` | Session venv PVC size |
| `postgres.enabled` | `true` | Include the PostgreSQL sidecar and persistent database PVC |
| `postgres.image` | `docker.io/library/postgres:16` | PostgreSQL image |
| `postgres.password` | `""` | Empty generates a random retained password; set explicitly for declarative GitOps |
| `persistence.postgres.size` | `5Gi` | PostgreSQL data PVC size |
| `persistence.gateway.size` | `5Gi` | Legacy `*-gateway-data` PVC retained for rollback compatibility |
| `persistence.abbenay.enabled` | `false` | When `true` (and `abbenay.enabled`), PVC for Abbenay runtime config and file-store secrets (`secrets.json`); otherwise `emptyDir` |
| `persistence.abbenay.size` | `100Mi` | Abbenay config PVC size (seed-once from ConfigMap; runtime SoT after configure; also holds `secrets.json` for `secretStore: file`) |

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

When `ui.enabled=true`, `route.host` is **required** so the UI (`/`) and
Gateway (`/api`) Routes share one hostname. When `ui.enabled=false`
(portal), leave `host` empty to let OpenShift auto-assign the API Route
hostname.

### Portal / external UI (backend only)

When automation portal or another Backstage instance is the presentation
layer, install with the portal values profile (or the equivalent override)
and expose only the Gateway:

```bash
helm install apme apme/apme \
  -f https://ansible.github.io/apme/values-portal.yaml \
  --set postgres.enabled=false \
  --set gateway.database.existingSecret.name=apme-database \
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
Multi-replica engine farms need a future topology ADR (external PostgreSQL and
Abbenay cannot share a scaled pod without redesign).

## OpenShift compatibility

The chart works under OpenShift's `restricted-v2` SCC without modification.
APME application container images are built on **UBI10** Application Stream
bases (ADR-061). CI publishes **multi-arch** (`linux/amd64` + `linux/arm64`)
manifest lists under the same tags after ADR-063 (rebuild release tags to pick
that up).

- The APME containers use explicit security values for vanilla Kubernetes; OpenShift
  SCCs may assign the UID/GID. PostgreSQL has its own compatible context.
- The UI container mounts emptyDir volumes for nginx writable paths
- No privilege escalation is required

For vanilla Kubernetes, set explicit security contexts for the UBI-based APME
containers (which run as UID 1001):

```yaml
podSecurityContext:
  fsGroup: 1001
securityContext:
  runAsNonRoot: true
  runAsUser: 1001
```

`fsGroup` ensures PVC mounts for `/sessions`, `/data`, `/cache`,
Abbenay `/etc/abbenay-config`, and the shared `abbenay-run` emptyDir are
group-accessible. Engine, Gateway, and Abbenay must share the same UID
(UBI and the Abbenay image both default to 1001; OpenShift injects one
UID for the pod). Do not set a different `runAsUser` on only one of
those containers — the gRPC socket is created mode `0600`. Local Podman
uses the same PVC definitions in `containers/podman/pvc.yaml` (with
`volume.podman.io/uid` annotations).

The PostgreSQL sidecar does not inherit the chart-wide `securityContext`. The
default `docker.io/library/postgres:16` image starts its entrypoint as root so
it can initialize and chown a fresh PVC, then drops privileges to UID 999. It
therefore cannot satisfy the Kubernetes Restricted Pod Security Standard as-is.
For vanilla Kubernetes, set `platform: kubernetes` and explicitly acknowledge
the required admission-policy exception with
`--set postgres.allowRootInit=true`; the namespace or policy must permit this
bootstrap container to start as root. Helm rejects that platform/image
combination unless the acknowledgement is explicit. For Restricted Pod
Security, use a PostgreSQL image and initialization strategy that supports
`runAsNonRoot: true`, then configure `postgres.securityContext` accordingly.

Set `platform: kubernetes` to apply the `podSecurityContext.fsGroup: 999`
default for the official image. Set `platform: openshift` on OpenShift so the
chart omits the hard-coded group and lets the SCC assign it. If PostgreSQL is
rebuilt with a fixed UID, configure `postgres.securityContext` separately.

The PostgreSQL PVC is retained when PostgreSQL is disabled, so switching to an
external database does not delete the in-pod data before migration. Delete the
retained PVC explicitly after verifying that the data is no longer needed.

## Uninstall

```bash
helm uninstall apme
```

The PostgreSQL PVC and bootstrap Secret are intentionally retained by Helm's
`helm.sh/resource-policy: keep` annotation. This protects database data and
credentials during uninstall and database-mode migration. Delete them
explicitly, only after verifying that the data and credential are no longer
needed. Other chart-managed PVCs are deleted by Helm 3 during uninstall.

If the StorageClass reclaim policy is **Delete**, the CSI driver typically
removes the backing volume when a retained PVC is explicitly deleted.

If the reclaim policy is **Retain**, uninstall (and deleting the PVC) does
**not** erase the disk. A PV in `Released` phase can still hold plaintext
`secrets.json` (Abbenay file store) and runtime `config.yaml`. Identify it,
destroy or crypto-erase the volume in the **storage provider**, then delete
the PV object. `kubectl` cannot securely overwrite those bytes.

```bash
# Find APME PVs (claims are <release>-abbenay-config, -gateway-data, …)
kubectl get pv -o custom-columns=\
NAME:.metadata.name,\
RECLAIM:.spec.persistentVolumeReclaimPolicy,\
STATUS:.status.phase,\
CLAIM:.spec.claimRef.namespace/.spec.claimRef.name

# Delete retained PostgreSQL resources only after migration/backup verification.
# <fullname> is the rendered apme.fullname (affected by nameOverride or
# fullnameOverride), not necessarily the Helm release name.
kubectl delete pvc <fullname>-postgres-data
kubectl delete secret <fullname>-postgres-secrets

# After wiping/destroying a Retain volume in the storage provider:
# kubectl delete pv <pv-name>
```

## Related

- [Deployment Guide](../../../docs/guides/DEPLOYMENT.md) — Overview of all deployment methods
- [ADR-054](../../../.sdlc/adrs/ADR-054-production-deployment.md) — Architecture rationale
- [Scaling docs](../../../docs/architecture/17-scaling-and-deployment.md) — Scaling model
