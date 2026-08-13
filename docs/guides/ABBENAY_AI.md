# Abbenay AI Provider Configuration

This guide covers configuring APME's Abbenay AI service for Tier 2
(AI-assisted) remediation. Abbenay supports multiple LLM backends via the
Vercel AI SDK.

## Gateway admin proxy (ADR-070)

In the Simple in-pod topology (ADR-069 / ADR-070), Abbenay serves HTTP admin
and gRPC on loopback (`--host 127.0.0.1 --port 8787` and
`--grpc-host 127.0.0.1 --grpc-port 50057`; image ≥ v2026.8.0). No cluster
Service or hostPort — Helm Simple and Podman share a netns, so Primary/
Gateway reach Abbenay at `127.0.0.1`. The Gateway reverse-proxies an
**allowlisted** admin surface:

| Gateway | Abbenay |
|---------|---------|
| `GET/POST /api/v1/ai/config` | `/api/config` |
| `GET /api/v1/ai/engines` | `/api/engines` |
| `GET /api/v1/ai/providers` | `/api/providers` |
| `POST /api/v1/ai/provider/{id}/configure` | `/api/provider/{id}/configure` |
| `DELETE /api/v1/ai/provider/{id}` | `/api/provider/{id}` |
| `GET/POST /api/v1/ai/secrets` | `/api/secrets` |
| `DELETE /api/v1/ai/secrets/{key}` | `/api/secrets/{key}` |

`GET /api/v1/ai/models` remains Primary → Abbenay gRPC (`ListAIModels`). Chat
is **not** proxied. Set `APME_ABBENAY_HTTP_URL` (default
`http://127.0.0.1:8787`) and `APME_ABBENAY_HTTP_TOKEN` on the Gateway (same
secret as `ABBENAY_API_TOKEN` / `abbenay.token` in Helm).

### Memory secret store (Abbenay >= v2026.8.5)

Abbenay supports a process-lifetime in-memory secret store for containerized
environments where a system keychain is unavailable. Secrets (API keys) can be
injected at runtime via the Gateway proxy instead of requiring env vars or Helm
Secrets at deploy time.

**Inject a secret at runtime:**

```bash
curl -X POST http://gateway:8080/api/v1/ai/secrets \
  -H "Content-Type: application/json" \
  -d '{"key": "OPENROUTER_API_KEY", "value": "sk-or-...", "secretStore": "memory"}'
```

**List stored secret names:**

```bash
curl http://gateway:8080/api/v1/ai/secrets
```

**Remove a secret:**

```bash
curl -X DELETE http://gateway:8080/api/v1/ai/secrets/OPENROUTER_API_KEY
```

After injecting a secret, configure a provider to use it via
`POST /api/v1/ai/provider/{id}/configure` with `secretName` and
`secretStore: memory`. Memory-stored secrets do not survive pod restarts;
re-inject after a restart or use Helm Secrets / env vars for persistence.

> **Security note:** `GET /api/v1/ai/secrets` returns stored key **names**
> (not values) to any client that can reach Gateway `:8080`. The Gateway REST
> API relies on network-isolation auth (ADR-048) — operators must ensure an
> outer auth layer (Ingress, Route, reverse proxy) before exposing `:8080`
> outside the cluster.

### Writable config volume (#498)

Runtime admin writes (configure / delete provider) persist on a **writable**
Abbenay config directory. Deploy-time values seed that directory once; after
the first write, the runtime file is the source of truth.

| Deploy | Seed | Writable volume | Notes |
|--------|------|-----------------|-------|
| **Helm** | ConfigMap `*-abbenay-config` (from `abbenay.providers`) | `emptyDir` by default; optional PVC via `persistence.abbenay.enabled=true` | Init `init-abbenay-config` copies seed only if `config.yaml` is absent. Mount: `/etc/abbenay-config`. |
| **Podman** | `containers/abbenay/config/` (or legacy `config.yaml` / `.example`) on first `tox -e up` | Cache dir `${XDG_CACHE_HOME:-$HOME/.cache}/apme/abbenay/config/` → `/home/abbenay/.config/abbenay` | `up.sh` seeds into the cache path (mode `0700`/`0600`). Rootful chowns the cache copy to UID 1001; rootless keeps host ownership and grants UID 1001 a POSIX ACL. The repo tree is never chowned. |

Helm PVC knobs (`persistence.abbenay.*`):

```yaml
persistence:
  abbenay:
    enabled: true    # false = emptyDir (lost on pod restart)
    size: 100Mi
    storageClass: ""
    accessMode: ReadWriteOnce
```

See [ADR-070](../../.sdlc/adrs/ADR-070-gateway-abbenay-admin-proxy.md) §6.

---

## Supported Engines

| Engine | Auth | Notes |
|--------|------|-------|
| `openrouter` | API key | Multi-model router; supports 200+ models |
| `anthropic` | API key | Direct Anthropic API |
| `vertex-anthropic` | GCP ADC or proxy | Claude on Vertex AI; keyless with workload identity |
| `ollama` | None | Local/self-hosted models; no auth required |

---

## Quick Start: OpenRouter

The simplest setup — one API key gives access to multiple models:

```yaml
abbenay:
  enabled: true
  token: "generate-a-random-token-here"    # e.g. openssl rand -hex 16
  aiModel: "openrouter/anthropic/claude-sonnet-4-6"

  providers:
    openrouter:
      engine: openrouter
      apiKey: "sk-or-..."
      models:
        anthropic/claude-sonnet-4-6: {}
        anthropic/claude-opus-4-6: {}
```

For production, use an existing Secret instead of inline keys:

```yaml
  providers:
    openrouter:
      engine: openrouter
      apiKeySecret:
        name: openrouter-credentials
        key: api-key
      models:
        anthropic/claude-sonnet-4-6: {}
```

---

## Direct Anthropic API

```yaml
abbenay:
  enabled: true
  token: "your-token"

  providers:
    anthropic:
      engine: anthropic
      apiKeySecret:
        name: anthropic-credentials
        key: api-key
      models:
        claude-sonnet-4-6: {}
        claude-sonnet-4-5: {}
        claude-haiku-4-5@20251001: {}
        claude-opus-4-6: {}
```

---

## Vertex AI (GCP)

Claude on Vertex AI uses Application Default Credentials (ADC) — no API key
needed. This is the preferred path for GCP-native deployments.

### Known Valid Models

| Model ID | Description |
|----------|-------------|
| `claude-sonnet-4-6` | Latest Sonnet |
| `claude-sonnet-4-5` | Previous Sonnet |
| `claude-haiku-4-5@20251001` | Fast, cost-effective |
| `claude-opus-4-6` | Most capable |

### Option A: Workload Identity (recommended for GKE/OCP)

If your cluster uses GKE Workload Identity or OpenShift Workload Identity
Federation, the pod inherits credentials from the attached service account
automatically. No Secret is needed:

```yaml
abbenay:
  enabled: true
  token: "your-token"
  aiModel: "vertex-claude/claude-sonnet-4-6"

  providers:
    vertex-claude:
      engine: vertex-anthropic
      models:
        claude-sonnet-4-6: {}
        claude-sonnet-4-5: {}
        claude-haiku-4-5@20251001: {}
        claude-opus-4-6: {}

  gcp:
    project: "your-gcp-project-id"
    location: us-east5
```

Ensure the Kubernetes service account is annotated for workload identity:

```bash
# GKE example
gcloud iam service-accounts add-iam-policy-binding \
  apme-vertex-ai@YOUR_PROJECT.iam.gserviceaccount.com \
  --role roles/iam.workloadIdentityUser \
  --member "serviceAccount:YOUR_PROJECT.svc.id.goog[apme/apme]"
```

### Option B: Service Account Key (non-GKE clusters)

For clusters without workload identity, provide a service account key:

**1. Create service account and key:**

```bash
export GCP_PROJECT="your-gcp-project-id"
export SA_NAME="apme-vertex-ai"

gcloud iam service-accounts create "$SA_NAME" \
  --project="$GCP_PROJECT" \
  --display-name="APME Vertex AI"

gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
  --member="serviceAccount:${SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"

gcloud iam service-accounts keys create sa-key.json \
  --iam-account="${SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com"
```

**2. Create Kubernetes Secret:**

```bash
kubectl create secret generic apme-gcp-credentials \
  --from-file=service-account-key.json=./sa-key.json \
  -n apme
```

**3. Reference in values:**

```yaml
abbenay:
  enabled: true
  token: "your-token"
  aiModel: "vertex-claude/claude-sonnet-4-6"

  providers:
    vertex-claude:
      engine: vertex-anthropic
      models:
        claude-sonnet-4-6: {}

  gcp:
    project: "your-gcp-project-id"
    location: us-east5
    existingSecret: apme-gcp-credentials
```

### Option C: Inline key (dev/CI only)

```yaml
  gcp:
    project: "your-gcp-project-id"
    location: us-east5
    serviceAccountKey: |
      {
        "type": "service_account",
        "project_id": "your-gcp-project-id",
        ...
      }
```

> **Security note:** Never commit service account keys. Use `existingSecret`
> or workload identity in production.

### Corporate Vertex Proxy

If your organization routes Vertex AI traffic through an API proxy:

```yaml
abbenay:
  enabled: true
  token: "your-token"

  providers:
    corp-vertex:
      engine: vertex-anthropic
      baseUrl: "https://your-proxy.example.com/models"
      apiKeySecret:
        name: vertex-proxy-credentials
        key: bearer-token
      models:
        claude-sonnet-4-6: {}

  # gcp section not needed — the proxy handles authentication
```

---

## Ollama (Local / Self-Hosted)

For local development or air-gapped environments:

```yaml
abbenay:
  enabled: true
  token: "your-token"
  aiModel: "local-ollama/llama3.2"

  providers:
    local-ollama:
      engine: ollama
      baseUrl: "http://ollama.default.svc:11434/v1"
      models:
        llama3.2: {}
        codellama:13b: {}
```

No API key or credentials needed — Ollama serves models locally.

---

## Multiple Providers

You can configure multiple providers simultaneously. Abbenay selects the
model specified by `aiModel` (format: `<provider-name>/<model-id>`):

```yaml
abbenay:
  enabled: true
  token: "your-token"
  aiModel: "vertex-claude/claude-sonnet-4-6"  # default model

  providers:
    vertex-claude:
      engine: vertex-anthropic
      models:
        claude-sonnet-4-6: {}
    openrouter:
      engine: openrouter
      apiKeySecret:
        name: openrouter-secret
        key: api-key
      models:
        anthropic/claude-opus-4-6: {}
    local-ollama:
      engine: ollama
      baseUrl: "http://ollama.default.svc:11434/v1"
      models:
        llama3.2: {}

  gcp:
    project: "your-gcp-project-id"
    location: us-east5
```

---

## Environment Variables (Vertex AI)

The chart sets these automatically when a `vertex-anthropic` provider uses
ADC (no `baseUrl` or `apiKey`):

| Variable | Source | Purpose |
|----------|--------|---------|
| `GOOGLE_APPLICATION_CREDENTIALS` | Volume mount path | Points to the mounted SA JSON (only when credentials Secret is set) |
| `GOOGLE_VERTEX_PROJECT` | `abbenay.gcp.project` | GCP project for Vertex AI API calls |
| `GOOGLE_VERTEX_LOCATION` | `abbenay.gcp.location` | Vertex AI region (e.g. `us-east5`) |

These are the env var names that Abbenay's Vercel AI SDK integration reads.
Do not use `ANTHROPIC_VERTEX_PROJECT_ID` or `CLOUD_ML_REGION` — those are
for different SDKs and will be ignored.

---

## Install / Upgrade

```bash
helm repo add apme https://ansible.github.io/apme
helm repo update
helm upgrade --install apme apme/apme \
  -n apme --create-namespace \
  -f values.yaml
```

From a local clone: `helm upgrade --install apme deploy/helm/apme/ …`.

## Verify

```bash
kubectl get pods -n apme -l app.kubernetes.io/component=abbenay
kubectl logs -n apme -l app.kubernetes.io/component=abbenay --tail=50
```

For Vertex AI, a working URL in the logs looks like:

```
https://us-east5-aiplatform.googleapis.com/v1/projects/your-project/locations/us-east5/publishers/anthropic/models/claude-sonnet-4-6:streamRawPredict
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `undefined` in Vertex API URL | Missing `gcp.project` or `gcp.location` | Set both in values |
| `PERMISSION_DENIED` | SA lacks `roles/aiplatform.user` | Grant role to the service account |
| Pod stuck in `ContainerCreating` | Credentials Secret missing | Create Secret or use workload identity |
| `apme-engine: connection refused` on port 50057 | Abbenay not running | Check `abbenay.enabled: true` and pod logs |
| `401 Unauthorized` on OpenRouter/Anthropic | Wrong or expired API key | Rotate key in Secret |
