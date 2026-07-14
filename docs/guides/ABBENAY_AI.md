# Abbenay AI Provider Configuration

This guide covers configuring APME's Abbenay AI service for Tier 2
(AI-assisted) remediation. Abbenay supports multiple LLM backends via the
Vercel AI SDK.

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
