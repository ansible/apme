# Deployment

## Podman pod (recommended)

The primary deployment target is a Podman pod. All backend services run in a single pod sharing `localhost`; the CLI is run on-the-fly outside the pod with the project directory mounted.

### Prerequisites

- Podman (rootless)
- `loginctl enable-linger $USER` (for rootless runtime directory)
- SELinux: volume mounts use `:Z` for private labeling

### Build

From the repo root:

```bash
./containers/podman/build.sh
```

This builds a shared base image, nine service images, and pulls one official image:

| Image | Source | Purpose |
|-------|--------|---------|
| `apme-primary:latest` | `containers/primary/Dockerfile` | Orchestrator + engine + session venv manager |
| `apme-native:latest` | `containers/native/Dockerfile` | Native Python validator |
| `apme-opa:latest` | `containers/opa/Dockerfile` | OPA + gRPC wrapper |
| `apme-ansible:latest` | `containers/ansible/Dockerfile` | Ansible validator (reads session venvs) |
| `apme-gitleaks:latest` | `containers/gitleaks/Dockerfile` | Gitleaks secret scanner + gRPC wrapper |
| `apme-galaxy-proxy:latest` | `containers/galaxy-proxy/Dockerfile` | PEP 503 proxy: Galaxy tarballs → Python wheels |
| `apme-gateway:latest` | `containers/gateway/Dockerfile` | REST API + gRPC Reporting service (SQLite) |
| `apme-ui:latest` | `containers/ui/Dockerfile` | React SPA served by nginx (proxies API to Gateway) |
| `apme-cli:latest` | `containers/cli/Dockerfile` | CLI client |
| `ghcr.io/redhat-developer/abbenay:2026.3.7-alpha` | [Official image](https://github.com/redhat-developer/abbenay/pkgs/container/abbenay) (pulled) | Abbenay AI daemon (LLM gateway for Tier 2 remediation) |

### Configure Abbenay AI (optional)

Abbenay provides LLM-backed AI remediation (Tier 2). Each developer supplies their own API key:

```bash
cp containers/abbenay/.env.example containers/abbenay/.env
# Edit .env and set your LLM provider API key (e.g., OPENROUTER_API_KEY)
```

The `.env` file is gitignored. The default `config.yaml` configures OpenRouter with the `apme-dev-token` consumer. To use a different provider or model, edit `containers/abbenay/config.yaml`.

If `.env` is missing or the key is empty, the Abbenay container starts but model queries return empty results. AI remediation gracefully degrades — Tier 1 deterministic fixes still work.

### Start the pod

```bash
./containers/podman/up.sh
```

This runs `podman play kube containers/podman/pod.yaml`, which starts the pod `apme-pod` with all service containers (Primary, Native, OPA, Ansible, Gitleaks, Galaxy Proxy, Gateway, UI, Abbenay). The `up.sh` script sources `containers/abbenay/.env` to inject LLM API keys into the Abbenay container. A sessions directory is created for session-scoped venvs.

### Run CLI commands

```bash
cd /path/to/your/ansible/project

# Check (policy validation; default target is `.` when your wrapper passes it)
/path/to/apme/containers/podman/run-cli.sh
/path/to/apme/containers/podman/run-cli.sh check --json .

# Remediate (Tier 1 deterministic fixes; `FixSession` RPC)
containers/podman/run-cli.sh check --diff .        # dry-run with diffs
containers/podman/run-cli.sh remediate .           # apply

# Format (YAML normalization)
containers/podman/run-cli.sh format --check .

# Health check
containers/podman/run-cli.sh health-check
```

The CLI container joins `apme-pod`, mounts CWD as `/workspace:Z` (read-write for `remediate`/`format`), and communicates with Primary at `127.0.0.1:50051` via gRPC.

The `remediate` command uses a bidirectional streaming RPC (`FixSession`, ADR-028, ADR-039) for real-time progress and interactive AI proposal review. **`check`** uses the same `FixSession` RPC in check mode.

### Stop the pod

```bash
podman pod stop apme-pod
podman pod rm apme-pod
```

### Health check

```bash
apme health-check
```

The CLI discovers the Primary via `APME_PRIMARY_ADDRESS` env var, a running daemon, or auto-starts one locally.

Reports status of all services (Primary, Native, OPA, Ansible, Gitleaks) with latency.

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
| `APME_REPORTING_ENDPOINT` | — | Gateway gRPC Reporting address (e.g., `127.0.0.1:50060`). Events are pushed after each check or remediate run. |
| `APME_ABBENAY_ADDR` | — | Abbenay AI daemon address (e.g., `127.0.0.1:50057`). Supports `host:port` and `unix://` formats. |
| `APME_ABBENAY_TOKEN` | — | Consumer token for Abbenay authentication. Must match a token in Abbenay's `config.yaml`. |
| `APME_AI_MODEL` | — | Default AI model ID (e.g., `anthropic/claude-sonnet-4`). Overridden by UI Settings or CLI `--model`. |

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

#### Galaxy Proxy

| Variable | Default | Description |
|----------|---------|-------------|
| `APME_GALAXY_PROXY_URL` | `http://127.0.0.1:8765` | Galaxy proxy base URL |
| `GALAXY_URL` | `https://galaxy.ansible.com` | Single-server fallback Galaxy URL |
| `GALAXY_TOKEN` | — | Token for the fallback Galaxy URL |
| `GALAXY_SERVER_LIST` | — | Comma-separated server names for multi-server config (e.g., `certified,community`) |

**Multi-server configuration (ansible.cfg-style):**

For each server name in `GALAXY_SERVER_LIST`, set corresponding env vars:

| Variable Pattern | Description |
|------------------|-------------|
| `GALAXY_SERVER_<NAME>_URL` | Server base URL (required) |
| `GALAXY_SERVER_<NAME>_TOKEN` | API token or offline token |
| `GALAXY_SERVER_<NAME>_AUTH_URL` | SSO/OIDC token endpoint for offline-token exchange |
| `GALAXY_SERVER_<NAME>_AUTH_TYPE` | `token` (default), `bearer`, or `sso` |

Example for Red Hat Automation Hub + community Galaxy:

```bash
GALAXY_SERVER_LIST=certified,community

GALAXY_SERVER_CERTIFIED_URL=https://console.redhat.com/api/automation-hub/
GALAXY_SERVER_CERTIFIED_TOKEN=<offline-token-from-console.redhat.com>
GALAXY_SERVER_CERTIFIED_AUTH_URL=https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token
GALAXY_SERVER_CERTIFIED_AUTH_TYPE=sso

GALAXY_SERVER_COMMUNITY_URL=https://galaxy.ansible.com
```

Servers are tried in order; the first to return a successful response wins.

#### Gateway

| Variable | Default | Description |
|----------|---------|-------------|
| `APME_DB_PATH` | `/data/apme.db` | Path to the SQLite database |
| `APME_GATEWAY_GRPC_LISTEN` | `0.0.0.0:50060` | gRPC Reporting service listen address |
| `APME_GATEWAY_HTTP_HOST` | `0.0.0.0` | REST API bind host |
| `APME_GATEWAY_HTTP_PORT` | `8080` | REST API bind port |

#### Abbenay AI

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_API_KEY` | — | LLM provider API key (from `containers/abbenay/.env`) |
| `APME_ABBENAY_TOKEN` | `apme-dev-token` | Consumer token (must match `config.yaml` consumers section) |

Abbenay uses the official container image (`ghcr.io/redhat-developer/abbenay`) with `containers/abbenay/config.yaml` volume-mounted at runtime. The default config uses OpenRouter as the LLM provider. To add providers or models, edit the `providers` section. API keys are injected from environment variables — never committed to the config file.

The Abbenay daemon exposes a gRPC API on port 50057. Primary connects to it for AI model listing (`ListAIModels`) and batch remediation requests.

#### UI

The UI container has no environment variables. It serves the React SPA via nginx and proxies `/api/` requests to the Gateway at `127.0.0.1:8080` (same pod network namespace).

The Settings page (`/settings`) provides a model picker that queries available AI models from Abbenay via the gateway. The selected model is stored in the browser's `localStorage`.

### Volumes

| Name | Host Path | Container Mount | Services | Access |
|------|-----------|-----------------|----------|--------|
| `sessions` | `apme-sessions/` | `/sessions` | Primary, Ansible | rw (primary), ro (ansible) |
| `gateway-data` | `<cache>/gateway/` | `/data` | Gateway | rw |
| `workspace` | CWD (CLI only) | `/workspace` | CLI | rw |

## OPA container details

The OPA container uses a multi-stage Dockerfile:

1. **Stage 1**: Copies the `opa` binary from `docker.io/openpolicyagent/opa:latest`
2. **Stage 2**: Python slim image with `grpcio`, project code, and the Rego bundle

At runtime, `entrypoint.sh`:

1. Starts OPA as a REST server: `opa run --server --addr :8181 /bundle`
2. Waits for OPA to become healthy (polls `/health`)
3. Starts the Python gRPC wrapper (`apme-opa-validator`)

The Rego bundle is baked into the image at build time (no volume mount needed).

### Ansible container details

The Ansible container receives session-scoped venvs via the `/sessions` volume (read-only). The Primary orchestrator builds and manages these venvs using `VenvSessionManager`; the Ansible validator simply uses the `venv_path` provided in each `ValidateRequest`.

Collections are installed into the venv's `site-packages/ansible_collections/` directory by `uv pip install` through the Galaxy Proxy — they're on the Python path natively (no `ANSIBLE_COLLECTIONS_PATH` or `ansible.cfg` needed).

The Ansible validator requires a `venv_path` from the Primary orchestrator. If none is provided (e.g., standalone testing without Primary), the validator returns an infrastructure error and skips validation.

## Local development (daemon mode)

For development and testing without the Podman pod, the CLI can start a
local daemon that runs the Primary, Native, OPA, Ansible, and Galaxy Proxy services
as localhost gRPC servers (ADR-024). Gitleaks is excluded (requires the gitleaks binary).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e ".[dev]"

# Start the local daemon
python -m apme_engine.cli daemon start

# Run commands (thin CLI talks to local daemon via gRPC)
python -m apme_engine.cli check /path/to/project
python -m apme_engine.cli check --diff .
python -m apme_engine.cli remediate .

# Stop the daemon
python -m apme_engine.cli daemon stop
```

**Daemon mode** starts a local Primary server with Native, OPA, and Ansible validators plus the Galaxy Proxy running in-process. OPA runs via the local `opa` binary; if `opa` is not installed, the OPA validator is automatically skipped.

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
| 50057 | Abbenay AI | `--grpc-port` (CLI flag) |
| 50060 | Gateway (gRPC) | `APME_GATEWAY_GRPC_LISTEN` |
| 8080 | Gateway (HTTP) | `APME_GATEWAY_HTTP_PORT` |
| 8081 | UI (nginx) | — |
| 8765 | Galaxy Proxy | `APME_GALAXY_PROXY_URL` |

## Related Documents

- [ADR-006](../.sdlc/adrs/ADR-006-ephemeral-venvs.md) — Ephemeral venvs for Ansible (superseded by ADR-022/ADR-031)
