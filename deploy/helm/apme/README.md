# APME Helm Chart

Deploy APME on Kubernetes or OpenShift. The chart models the architecture
as defined in [ADR-054](../../../.sdlc/adrs/ADR-054-production-deployment.md):
the engine stack runs as sidecar containers in a single pod (preserving
localhost networking per ADR-005 and pod-level scaling per ADR-012).

## Prerequisites

- Kubernetes 1.26+ or OpenShift 4.14+
- Helm 3.x
- Access to `ghcr.io/ansible` container registry (or mirror images locally)
- A published image tag (CI tags images by git SHA, e.g. `sha-7cb2464`)

## Quick start

```bash
# From the repository root
helm install apme ./deploy/helm/apme/ \
  --set image.tag=sha-7cb2464

# With AI enabled (OpenRouter provider)
helm install apme ./deploy/helm/apme/ \
  --set image.tag=sha-7cb2464 \
  --set abbenay.enabled=true \
  --set abbenay.token=$APME_ABBENAY_TOKEN \
  --set-json 'abbenay.providers={"openrouter":{"engine":"openrouter","apiKey":"'$OPENROUTER_API_KEY'","models":{"anthropic/claude-sonnet-4-6":{}}}}'
```

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
| `image.registry` | `ghcr.io/ansible` | Container registry |
| `image.tag` | `""` | Image tag (required — set explicitly) |
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

image:
  registry: quay.io/your-org
  tag: sha-7cb2464

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

For vanilla Kubernetes, set explicit security contexts:

```yaml
podSecurityContext:
  fsGroup: 1000
securityContext:
  runAsNonRoot: true
  runAsUser: 1000
```

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
