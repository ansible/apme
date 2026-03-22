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

This builds seven images:

| Image | Dockerfile | Purpose |
|-------|------------|---------|
| `apme-primary:latest` | `containers/primary/Dockerfile` | Orchestrator + engine + session venv manager |
| `apme-native:latest` | `containers/native/Dockerfile` | Native Python validator |
| `apme-opa:latest` | `containers/opa/Dockerfile` | OPA + gRPC wrapper |
| `apme-ansible:latest` | `containers/ansible/Dockerfile` | Ansible validator (reads session venvs) |
| `apme-gitleaks:latest` | `containers/gitleaks/Dockerfile` | Gitleaks secret scanner + gRPC wrapper |
| `apme-galaxy-proxy:latest` | `containers/galaxy-proxy/Dockerfile` | PEP 503 proxy: Galaxy tarballs → Python wheels |
| `apme-cli:latest` | `containers/cli/Dockerfile` | CLI client |

### Start the pod

```bash
./containers/podman/up.sh
```

This runs `podman play kube containers/podman/pod.yaml`, which starts the pod `apme-pod` with six containers (Primary, Native, OPA, Ansible, Gitleaks, Galaxy Proxy). A sessions directory is created for session-scoped venvs.

### Run CLI commands

```bash
cd /path/to/your/ansible/project

# Scan (default: scan .)
/path/to/apme/containers/podman/run-cli.sh
/path/to/apme/containers/podman/run-cli.sh scan --json .

# Fix (Tier 1 deterministic fixes)
containers/podman/run-cli.sh fix --check .   # dry-run
containers/podman/run-cli.sh fix .           # apply

# Format (YAML normalization)
containers/podman/run-cli.sh format --check .

# Health check
containers/podman/run-cli.sh health-check
```

The CLI container joins `apme-pod`, mounts CWD as `/workspace:Z` (read-write for `fix`/`format`), and communicates with Primary at `127.0.0.1:50051` via gRPC.

The `fix` command uses a bidirectional streaming RPC (`FixSession`, ADR-028) for real-time progress and interactive AI proposal review.

### Stop the pod

```bash
podman pod stop apme-pod
podman pod rm apme-pod
```

### Health check

```bash
apme-scan health-check
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

If a validator address is unset, that validator is skipped during fan-out.

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

### Volumes

| Name | Host Path | Container Mount | Services | Access |
|------|-----------|-----------------|----------|--------|
| `sessions` | `apme-sessions/` | `/sessions` | Primary, Ansible | rw (primary), ro (ansible) |
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
python -m apme_engine.cli scan /path/to/project
python -m apme_engine.cli fix --check .
python -m apme_engine.cli fix .

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
| 8765 | Galaxy Proxy | `APME_GALAXY_PROXY_URL` |

## Related Documents

- [ADR-006](../.sdlc/adrs/ADR-006-ephemeral-venvs.md) — Ephemeral venvs for Ansible (superseded by ADR-022/ADR-031)
