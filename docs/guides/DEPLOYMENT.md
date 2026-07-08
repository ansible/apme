# Deployment Guide

APME supports multiple deployment methods depending on your environment and needs.

| Target environment | Method | Details |
|--------------------|--------|---------|
| Developer laptop / Linux server (no K8s) | **Podman pod** | [Below](#podman-pod) |
| **Kubernetes / OpenShift** | **Helm chart** | [Helm section](#helm--kubernetes) / [full guide](../../deploy/helm/apme/README.md) |
| Production single-node VM | **bootc VM** | [bootc section](#bootc-vm) / [full guide](../../deploy/bootc/README.md) |
| Quick evaluation / CI | **CLI daemon** | [CLI Guide](CLI.md) |

> **Deploying on Kubernetes or OpenShift?** Use the Helm chart at
> `deploy/helm/apme/`. Do not use Podman on K8s/OCP.

Full deployment methods (Podman, bootc, Helm) run the complete engine stack
(Primary + all validators + Galaxy Proxy). The CLI daemon runs only core
validators (Native, OPA, Ansible) + Galaxy Proxy. The difference is lifecycle
management, persistence, and additional services (UI, Gateway, Abbenay AI).

---

## Podman pod

The primary deployment target is a Podman pod. All backend services run in a single pod sharing `localhost`; the CLI is run on-the-fly outside the pod with the project directory mounted.

### Prerequisites

- Podman (rootless)
- `loginctl enable-linger $USER` (for rootless runtime directory)
- SELinux: volume mounts use `:Z` for private labeling

### Build

From the repo root:

```bash
tox -e build
```

This builds a shared base image, eleven service images, and pulls one official image:

| Image | Source | Purpose |
|-------|--------|---------|
| `apme-primary:latest` | `containers/primary/Dockerfile` | Orchestrator + engine + session venv manager |
| `apme-native:latest` | `containers/native/Dockerfile` | Native Python validator |
| `apme-opa:latest` | `containers/opa/Dockerfile` | OPA + gRPC wrapper |
| `apme-ansible:latest` | `containers/ansible/Dockerfile` | Ansible validator (reads session venvs) |
| `apme-gitleaks:latest` | `containers/gitleaks/Dockerfile` | Gitleaks secret scanner + gRPC wrapper |
| `apme-collection-health:latest` | `containers/collection-health/Dockerfile` | Installed collection health scanner |
| `apme-dep-audit:latest` | `containers/dep-audit/Dockerfile` | Python CVE scanner (pip-audit) |
| `apme-galaxy-proxy:latest` | `containers/galaxy-proxy/Dockerfile` | PEP 503 proxy: Galaxy tarballs → Python wheels |
| `apme-gateway:latest` | `containers/gateway/Dockerfile` | REST API + gRPC Reporting service (SQLite) |
| `apme-ui:latest` | `containers/ui/Dockerfile` | React SPA served by nginx (proxies API to Gateway) |
| `apme-cli:latest` | `containers/cli/Dockerfile` | CLI client |
| `ghcr.io/redhat-developer/abbenay:2026.4.1-alpha` | [Official image](https://github.com/redhat-developer/abbenay/pkgs/container/abbenay) (pulled) | Abbenay AI daemon (LLM gateway for Tier 2 remediation) |

### Configure Abbenay AI (optional)

Abbenay provides LLM-backed AI remediation (Tier 2). Each developer supplies their own API key:

```bash
cp containers/abbenay/.env.example containers/abbenay/.env
# Edit .env and set your LLM provider API key (e.g., OPENROUTER_API_KEY)
```

The `.env` file is gitignored. The default `config.yaml` configures the LLM provider and consumer token. To use a different provider or model, edit `containers/abbenay/config.yaml`.

If `.env` is missing or the key is empty, the Abbenay container starts but model queries return empty results. AI remediation gracefully degrades — Tier 1 deterministic fixes still work.

#### Custom CA certificates

When the pod needs to trust an internal or self-signed CA for outbound HTTPS, set `ABBENAY_CA_BUNDLE` in your `.env` to the absolute path of a PEM CA bundle:

```bash
ABBENAY_CA_BUNDLE=/path/to/ca-bundle.pem
```

The start script (`up.sh`) automatically mounts the bundle into the `abbenay`, `gateway`, and `galaxy-proxy` containers. It sets `NODE_EXTRA_CA_CERTS` for Abbenay and the standard git/HTTP CA variables (`SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`, `GIT_SSL_CAINFO`) for the gateway and Galaxy Proxy so repo cloning, Galaxy downloads, and API calls trust the same bundle. This is only needed when your network uses non-public CAs.

### Start the pod

```bash
tox -e up
```

This runs `podman play kube containers/podman/pod.yaml`, which starts the pod `apme-pod` with all service containers (Primary, Native, OPA, Ansible, Gitleaks, Collection Health, Dep Audit, Galaxy Proxy, Gateway, UI, Abbenay). The `up.sh` script sources `containers/abbenay/.env` to inject LLM API keys into the Abbenay container. A sessions directory is created for session-scoped venvs.

### Run CLI commands

```bash
tox -e cli                              # default: check .
tox -e cli -- check --json .            # JSON output
tox -e cli -- check --diff .            # dry-run with diffs
tox -e cli -- remediate .               # Tier 1 fixes
tox -e cli -- format --check .          # YAML format check
tox -e cli -- health-check              # health check
```

The CLI container joins `apme-pod`, mounts CWD as `/workspace:Z` (read-write for `remediate`/`format`), and communicates with Primary at `127.0.0.1:50051` via gRPC.

The `remediate` command uses a bidirectional streaming RPC (`FixSession`, ADR-028, ADR-039) for real-time progress and interactive AI proposal review. **`check`** uses the same `FixSession` RPC in check mode.

### Stop the pod

```bash
tox -e down
tox -e wipe    # also delete database + session cache
```

### Health check

```bash
apme health-check
```

The CLI discovers the Primary via `APME_PRIMARY_ADDRESS` env var, a running daemon, or auto-starts one locally.

Reports status of all services (Primary, Native, OPA, Ansible, Gitleaks, Collection Health, Dep Audit) with latency.

## Container configuration

### Environment variables

#### Primary

| Variable | Default | Description |
|----------|---------|-------------|
| `APME_PRIMARY_LISTEN` | `0.0.0.0:50051` | gRPC listen address |
| `NATIVE_GRPC_ADDRESS` | — | Native validator address (e.g., `127.0.0.1:50055`) |
| `OPA_GRPC_ADDRESS` | — | OPA validator address (e.g., `127.0.0.1:50054`) |
| `ANSIBLE_GRPC_ADDRESS` | — | Ansible validator address (e.g., `127.0.0.1:50053`) |
| `GITLEAKS_GRPC_ADDRESS` | — | Gitleaks validator address (e.g., `127.0.0.1:50056`) |
| `COLLECTION_HEALTH_GRPC_ADDRESS` | — | Collection Health validator address (e.g., `127.0.0.1:50058`) |
| `DEP_AUDIT_GRPC_ADDRESS` | — | Dep Audit validator address (e.g., `127.0.0.1:50059`) |
| `APME_REPORTING_ENDPOINT` | — | Gateway gRPC Reporting address (e.g., `127.0.0.1:50060`). Events are pushed after each check or remediate run. |
| `APME_ABBENAY_ADDR` | — | Abbenay AI daemon address (e.g., `127.0.0.1:50057`). Supports `host:port` and `unix://` formats. |
| `APME_ABBENAY_TOKEN` | — | Consumer token for Abbenay authentication. Must match a token in Abbenay's `config.yaml`. |
| `APME_AI_MODEL` | — | Default AI model ID (e.g., `anthropic/claude-sonnet-4`). Overridden by UI Settings or CLI `--model`. |
| `APME_RULE_AUTHORITY` | `true` | Set to `true` on exactly one Primary in multi-pod deployments. Only the authority registers the rule catalog to the Gateway (ADR-041). |

If a validator address is unset, that validator is skipped during fan-out. If Abbenay is unreachable, AI remediation is skipped (Tier 1 deterministic fixes still run).

#### Native

| Variable | Default | Description |
|----------|---------|-------------|
| `APME_NATIVE_VALIDATOR_LISTEN` | `0.0.0.0:50055` | gRPC listen address |

#### OPA

| Variable | Default | Description |
|----------|---------|-------------|
| `APME_OPA_VALIDATOR_LISTEN` | `0.0.0.0:50054` | gRPC listen address |

The OPA binary runs internally on `localhost:8181`; the gRPC wrapper proxies to it.

#### Ansible

| Variable | Default | Description |
|----------|---------|-------------|
| `APME_ANSIBLE_VALIDATOR_LISTEN` | `0.0.0.0:50053` | gRPC listen address |

#### Collection Health

| Variable | Default | Description |
|----------|---------|-------------|
| `APME_COLLECTION_HEALTH_VALIDATOR_LISTEN` | `0.0.0.0:50058` | gRPC listen address |

#### Dep Audit

| Variable | Default | Description |
|----------|---------|-------------|
| `APME_DEP_AUDIT_VALIDATOR_LISTEN` | `0.0.0.0:50059` | gRPC listen address |

#### Galaxy Proxy

| Variable | Default | Description |
|----------|---------|-------------|
| `APME_GALAXY_PROXY_URL` | `http://127.0.0.1:8765` | Galaxy proxy base URL |

#### Gateway

| Variable | Default | Description |
|----------|---------|-------------|
| `APME_DB_PATH` | `/data/apme.db` | Path to the SQLite database (stores activity, sessions, rule catalog, and rule overrides) |
| `APME_GATEWAY_GRPC_LISTEN` | `0.0.0.0:50060` | gRPC Reporting service listen address |
| `APME_GATEWAY_HTTP_HOST` | `0.0.0.0` | REST API bind host |
| `APME_GATEWAY_HTTP_PORT` | `8080` | REST API bind port |

#### Abbenay AI

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_API_KEY` | — | OpenRouter API key (from `containers/abbenay/.env`) |
| `VERTEX_ANTHROPIC_API_KEY` | — | Vertex AI Anthropic proxy API key (from `containers/abbenay/.env`) |
| `APME_ABBENAY_TOKEN` | `apme-dev-token` | Consumer token (must match `config.yaml` consumers section) |
| `NODE_EXTRA_CA_CERTS` | — | CA bundle path inside container (auto-set by `up.sh` when `ABBENAY_CA_BUNDLE` is configured) |

#### Gateway and Galaxy Proxy outbound trust

| Variable | Default | Description |
|----------|---------|-------------|
| `SSL_CERT_FILE` | — | CA bundle path inside the gateway and galaxy-proxy containers (auto-set by `up.sh` when `ABBENAY_CA_BUNDLE` is configured) |
| `REQUESTS_CA_BUNDLE` | — | Shared CA bundle for Python HTTP clients in the gateway and Galaxy Proxy |
| `CURL_CA_BUNDLE` | — | Shared CA bundle for curl/libcurl consumers in the gateway and Galaxy Proxy |
| `GIT_SSL_CAINFO` | — | Shared CA bundle for `git ls-remote`, `git clone`, and `ansible-galaxy` git fetches |

Abbenay uses `containers/abbenay/config.yaml` volume-mounted at runtime. The config defines LLM providers and models. API keys are injected from environment variables — never committed to the config file. To add providers or models, edit the `providers` section of the config.

The Abbenay daemon exposes a gRPC API on port 50057. Primary connects to it for AI model listing (`ListAIModels`) and batch remediation requests.

#### UI

The UI container has no environment variables. It serves the React SPA via nginx and proxies `/api/` requests to the Gateway at `127.0.0.1:8080` (same pod network namespace).

The Settings page (`/settings`) provides a model picker that queries available AI models from Abbenay via the gateway. The selected model is stored in the browser's `localStorage`. The Rules page (`/rules`) displays the rule catalog with enable/disable toggles, severity overrides, and category/source filters (ADR-041).

### Volumes

| Name | Host Path | Container Mount | Services | Access |
|------|-----------|-----------------|----------|--------|
| `sessions` | `apme-sessions/` | `/sessions` | Primary, Ansible | rw (primary), ro (ansible) |
| `gateway-data` | `<cache>/gateway/` | `/data` | Gateway | rw |
| `workspace` | CWD (CLI only) | `/workspace` | CLI | rw |

## OPA container details

The OPA container uses a multi-stage Dockerfile:

1. **Stage 1**: Copies the `opa` binary from `docker.io/openpolicyagent/opa:latest`
2. **Stage 2**: Python 3.12 UBI10 base image with `grpcio`, project code, and the Rego bundle

At runtime, `entrypoint.sh`:

1. Starts OPA as a REST server: `opa run --server --addr :8181 /bundle`
2. Waits for OPA to become healthy (polls `/health`)
3. Starts the Python gRPC wrapper (`apme-opa-validator`)

The Rego bundle is baked into the image at build time (no volume mount needed).

### Ansible container details

The Ansible container receives session-scoped venvs via the `/sessions` volume (read-only). The Primary orchestrator builds and manages these venvs using `VenvSessionManager`; the Ansible validator simply uses the `venv_path` provided in each `ValidateRequest`.

Collections are installed into the venv's `site-packages/ansible_collections/` directory by `uv pip install` through the Galaxy Proxy — they're on the Python path natively (no `ANSIBLE_COLLECTIONS_PATH` or `ansible.cfg` needed).

The Ansible validator requires a `venv_path` from the Primary orchestrator. If none is provided (e.g., standalone testing without Primary), the validator returns an infrastructure error and skips validation.

### Galaxy Proxy index strategy (`unsafe-best-match`)

When the venv manager installs collections into session-scoped venvs via
`uv pip install`, it defaults to `--index-strategy unsafe-best-match`.  This is
a deliberate trade-off:

- Galaxy Proxy serves collection tarballs as Python wheels under
  collection-namespaced package names (e.g.
  `ansible-collection-community-vmware`).  These names do not exist on PyPI,
  so dependency-confusion risk is low in practice.
- The alternative strategy (`first-match`) causes transitive dependencies to
  fail resolution when they exist only on PyPI but not on the Galaxy Proxy
  index.

**Risk assessment:** Because the Galaxy Proxy acts as a `--extra-index-url`
alongside PyPI, `unsafe-best-match` picks the highest version across both
indexes.  In theory an attacker who registered matching package names on PyPI
could hijack resolution.  In practice the collection-namespaced names are not
on PyPI, and the transitive deps (standard Python packages) resolve correctly
from PyPI.

If your environment has stricter dependency policies (e.g. air-gapped or
internal registries), note that the `UV_INDEX_STRATEGY` environment variable
**will not** override this setting because the CLI flag takes precedence.
To change the strategy, set the `APME_UV_INDEX_STRATEGY` environment variable
in the Primary container — the venv manager reads this at runtime and passes
it as the `--index-strategy` argument to `uv pip install`.

## Local development (daemon mode)

For development and testing without the Podman pod, the CLI can start a
local daemon that runs Primary, Native, OPA, and Ansible as localhost gRPC
servers plus Galaxy Proxy as an HTTP service (ADR-024). Optional validators
(Gitleaks, Collection Health, Dep Audit) are not started by the daemon.

```bash
# Install tox + project (one-time)
uv tool install tox --with tox-uv
uv sync --extra dev --extra gateway

# Start the local daemon
apme daemon start

# Run commands (thin CLI talks to local daemon via gRPC)
apme check /path/to/project
apme check --diff .
apme remediate .

# Stop the daemon
apme daemon stop
```

**Daemon mode** starts a local Primary server with Native, OPA, and Ansible validators as in-process gRPC servers, plus Galaxy Proxy as an HTTP service (uvicorn). Optional validators (Gitleaks, Collection Health, Dep Audit) are not started. OPA uses Podman by default; falls back to a local `opa` binary if Podman is unavailable; skipped if neither is found.

## Troubleshooting

See [PODMAN_OPA_ISSUES.md](PODMAN_OPA_ISSUES.md) for common Podman rootless issues:

- `/run/libpod: permission denied` — run in a real login shell, enable linger
- Short-name resolution — use fully qualified image names (`docker.io/...`)
- `/bundle: permission denied` — use `--userns=keep-id` and `:z` volume suffix

## Port Map quick reference

| Port | Service | Listen Variable |
|------|---------|-----------------|
| 50051 | Primary | `APME_PRIMARY_LISTEN` |
| 50053 | Ansible | `APME_ANSIBLE_VALIDATOR_LISTEN` |
| 50054 | OPA | `APME_OPA_VALIDATOR_LISTEN` |
| 50055 | Native | `APME_NATIVE_VALIDATOR_LISTEN` |
| 50056 | Gitleaks | `APME_GITLEAKS_VALIDATOR_LISTEN` |
| 50057 | Abbenay AI | `--grpc-port` (Abbenay daemon flag) |
| 50058 | Collection Health | `APME_COLLECTION_HEALTH_VALIDATOR_LISTEN` |
| 50059 | Dep Audit | `APME_DEP_AUDIT_VALIDATOR_LISTEN` |
| 50060 | Gateway (gRPC) | `APME_GATEWAY_GRPC_LISTEN` |
| 8080 | Gateway (HTTP) | `APME_GATEWAY_HTTP_PORT` |
| 8081 | UI (nginx) | — |
| 8765 | Galaxy Proxy | `APME_GALAXY_PROXY_URL` |

## Related Documents

- [CLI Guide](CLI.md) — CLI installation, commands, and limitations
- [bootc full guide](../../deploy/bootc/README.md) — Complete bootc VM documentation
- [Helm chart full guide](../../deploy/helm/apme/README.md) — Complete Helm documentation
- [ADR-006](../../.sdlc/adrs/ADR-006-ephemeral-venvs.md) — Ephemeral venvs for Ansible (superseded by ADR-022/ADR-031)
- [ADR-054](../../.sdlc/adrs/ADR-054-production-deployment.md) — Production Deployment (Helm + bootc)

---

## bootc VM

Deploy APME as an atomic, image-based Linux VM using
[bootc](https://containers.github.io/bootc/). The VM ships Podman and quadlet
definitions for all APME services. Container images are pulled from the registry
on first boot. Systemd manages automatic startup and lifecycle.

**Highlights:**

- RHEL 10 image mode base (ADR-061)
- Systemd quadlet files for each service (automatic restart, dependency ordering)
- Atomic upgrades with automatic rollback on failure (`bootc switch`)
- Persistent storage at `/var/lib/apme/`
- Firewall rules expose only ports 8080 (REST), 8081 (UI), 50051 (gRPC)

### Quick start

```bash
# Build the bootc OCI image
podman build -f deploy/bootc/Containerfile -t apme-bootc:latest .

# Convert to disk image (qcow2, raw, or AMI)
sudo podman run --rm -it --privileged \
  -v ./output:/output \
  -v /var/lib/containers/storage:/var/lib/containers/storage \
  quay.io/centos-bootc/bootc-image-builder:latest \
  --type qcow2 --local apme-bootc:latest

# Boot the VM — APME starts automatically
```

### Upgrading

```bash
sudo bootc switch --transport containers-storage apme-bootc:latest
sudo systemctl reboot
```

See [deploy/bootc/README.md](../../deploy/bootc/README.md) for full
configuration, service management, and troubleshooting.

---

## Helm / Kubernetes

Deploy APME on Kubernetes or OpenShift using the Helm chart at
`deploy/helm/apme/`. The chart models the engine stack as sidecar containers
in a single pod (preserving localhost networking per ADR-005 and pod scaling
per ADR-012).

**Highlights:**

- Engine pod: Primary + Native + OPA + Ansible + Gitleaks + Collection Health + Dep Audit + Galaxy Proxy as sidecars
- Separate deployments for Gateway, UI, and Abbenay (AI)
- HPA for engine scaling, PDB for disruption budget
- Ingress/Route support (OpenShift Routes included)
- NetworkPolicy for pod isolation
- PVC for Gateway persistence; PVC for session venvs (single replica), emptyDir when scaling to multiple replicas

### Quick start

```bash
helm install apme ./deploy/helm/apme/ \
  --set image.tag=sha-7cb2464 \
  --set abbenay.enabled=true \
  --set abbenay.token=$APME_ABBENAY_TOKEN \
  --set-json 'abbenay.providers={"openrouter":{"engine":"openrouter","apiKey":"'$OPENROUTER_API_KEY'","models":{"anthropic/claude-sonnet-4-6":{}}}}'
```

### Key values

| Value | Description |
|-------|-------------|
| `image.tag` | Image tag for all containers (required — no default) |
| `engine.replicas` | Engine pod replicas (default: 1) |
| `abbenay.enabled` | Enable AI provider (default: false) |
| `abbenay.token` | Abbenay service token (required when `abbenay.enabled=true`) |
| `abbenay.providers` | LLM provider map (see [ABBENAY_AI.md](ABBENAY_AI.md)) |
| `ingress.enabled` | Create Ingress resource (default: false) |
| `route.enabled` | Create OpenShift Route (default: false) |

See [deploy/helm/apme/README.md](../../deploy/helm/apme/README.md) for the
full values reference and architecture details.
