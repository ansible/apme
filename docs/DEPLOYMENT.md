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
./containers/podman/build.sh          # engine images only
./containers/podman/build.sh --ui     # engine + gateway + UI images
```

Engine images (always built):

| Image | Dockerfile | Purpose |
|-------|------------|---------|
| `apme-primary:latest` | `containers/primary/Dockerfile` | Orchestrator + engine |
| `apme-native:latest` | `containers/native/Dockerfile` | Native Python validator |
| `apme-opa:latest` | `containers/opa/Dockerfile` | OPA + gRPC wrapper |
| `apme-ansible:latest` | `containers/ansible/Dockerfile` | Ansible validator with pre-built venvs |
| `apme-gitleaks:latest` | `containers/gitleaks/Dockerfile` | Gitleaks secret scanner + gRPC wrapper |
| `apme-cache-maintainer:latest` | `containers/cache-maintainer/Dockerfile` | Collection cache manager |
| `apme-cli:latest` | `containers/cli/Dockerfile` | CLI client |

UI images (built with `--ui`):

| Image | Dockerfile | Purpose |
|-------|------------|---------|
| `apme-gateway:latest` | `containers/gateway/Dockerfile` | FastAPI HTTP/WS → gRPC gateway |
| `apme-ui:latest` | `containers/ui/Dockerfile` | React SPA served by Nginx |

### Start the pod

```bash
./containers/podman/up.sh          # engine pod only
./containers/podman/up.sh --ui     # engine pod + gateway + UI containers
```

This runs `podman play kube containers/podman/pod.yaml`, which starts the pod `apme-pod` with six containers (Primary, Native, OPA, Ansible, Gitleaks, Cache Maintainer). A cache directory is created under `$XDG_CACHE_HOME/apme` (default `~/.cache/apme`).

With `--ui`, the gateway and UI containers are started **outside** the pod on a separate `apme-ui-net` Podman network (ADR-012, ADR-029). The gateway connects to Primary via `host.containers.internal:50051`.

Port and path overrides are read from `.env` in the repo root:

| Variable | Default | Description |
|----------|---------|-------------|
| `APME_GATEWAY_PORT` | `8080` | Host port for the HTTP/WS gateway |
| `APME_UI_PORT` | `8081` | Host port for the web dashboard |
| `APME_GATEWAY_DATA` | `~/.local/share/apme/gateway` | SQLite database directory |
| `APME_CACHE_HOST_PATH` | `~/.cache/apme` | Engine cache directory |

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

### Stop everything

```bash
./containers/podman/down.sh          # stop engine pod + gateway + UI
./containers/podman/down.sh --wipe   # also delete the gateway SQLite database
```

This removes the gateway and UI containers, cleans up any lingering CLI containers, and stops/removes the engine pod.

### Health check

```bash
apme-scan health-check
```

The CLI discovers the Primary via `APME_PRIMARY_ADDRESS` env var, a running daemon, or auto-starts one locally.

Reports status of all services (Primary, Native, OPA, Ansible, Gitleaks, Cache Maintainer) with latency.

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
| `APME_CACHE_ROOT` | `/cache` | Collection cache mount point |

#### Cache Maintainer

| Variable | Default | Description |
|----------|---------|-------------|
| `APME_CACHE_MAINTAINER_LISTEN` | `0.0.0.0:50052` | gRPC listen address |
| `APME_CACHE_ROOT` | `/cache` | Collection cache directory |

#### Gateway (outside pod)

| Variable | Default | Description |
|----------|---------|-------------|
| `APME_PRIMARY_ADDRESS` | `host.containers.internal:50051` | Primary gRPC address |
| `APME_DATABASE_URL` | `sqlite+aiosqlite:////data/apme.db` | SQLAlchemy database URL |
| `APME_GATEWAY_HOST` | `0.0.0.0` | HTTP listen host |
| `APME_GATEWAY_PORT` | `8080` | HTTP listen port |

The gateway subscribes to Primary's `SubscribeScanEvents` stream (ADR-020) to automatically persist CLI-initiated scan results in its SQLite database.

### Volumes

| Name | Host path | Container mount | Services | Access |
|------|-----------|-----------------|----------|--------|
| `cache` | `~/.cache/apme` | `/cache` | Cache Maintainer, Ansible | rw (cache-maintainer), ro (ansible) |
| workspace | CWD (CLI only) | `/workspace` | CLI | rw |
| gateway-data | `~/.local/share/apme/gateway` | `/data` | Gateway | rw |

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

The Ansible container pre-builds venvs for multiple ansible-core versions during `podman build`:

```
/opt/apme-venvs/
  ├── 2.18/    # venv with ansible-core==2.18.*
  ├── 2.19/    # venv with ansible-core==2.19.*
  └── 2.20/    # venv with ansible-core==2.20.*
```

`prebuild-venvs.sh` runs during the Docker build to create these. At runtime, the validator selects the appropriate venv based on `ansible_core_version` from the `ValidateRequest`.

Collections from the cache volume are symlinked or copied into the venv's `site-packages/ansible_collections/` directory so they're on the Python path (no `ANSIBLE_COLLECTIONS_PATH` or `ansible.cfg` needed).

## Local development (daemon mode)

For development and testing without the Podman pod, the CLI can start a
local daemon that runs the Primary, Cache Maintainer, Native, and OPA validators
as localhost gRPC servers (ADR-024). Ansible and Gitleaks are optional
(`--include-optional`):

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

Daemon mode starts a background process with Primary, Cache Maintainer,
Native, and OPA validators as localhost gRPC servers. Ansible and Gitleaks
are optional (`_OPTIONAL_SERVICES` in `launcher.py`) and not started by
default — Ansible requires pre-built venvs and Gitleaks requires the
gitleaks binary. OPA runs via the local `opa` binary; if `opa` is not
installed, the OPA validator is automatically skipped.

## Troubleshooting

See [PODMAN_OPA_ISSUES.md](PODMAN_OPA_ISSUES.md) for common Podman rootless issues:

- `/run/libpod: permission denied` — run in a real login shell, enable linger
- Short-name resolution — use fully qualified image names (`docker.io/...`)
- `/bundle: permission denied` — use `--userns=keep-id` and `:z` volume suffix
