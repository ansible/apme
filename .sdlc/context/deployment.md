# Deployment

## Podman Pod (Recommended)

The primary deployment target is a **Podman pod**. All backend services run in a single pod sharing localhost; the CLI is run on-the-fly outside the pod with the project directory mounted.

### Prerequisites

- **Podman** (rootless)
- `loginctl enable-linger $USER` (for rootless runtime directory)
- **SELinux**: volume mounts use `:Z` for private labeling

### Build

From the repo root:

```bash
tox -e build
```

This builds nine images:

| Image | Dockerfile | Purpose |
|-------|------------|---------|
| `apme-primary:latest` | `containers/primary/Dockerfile` | Orchestrator + engine + session venv manager |
| `apme-native:latest` | `containers/native/Dockerfile` | Native Python validator |
| `apme-opa:latest` | `containers/opa/Dockerfile` | OPA + gRPC wrapper |
| `apme-ansible:latest` | `containers/ansible/Dockerfile` | Ansible validator (reads session venvs) |
| `apme-gitleaks:latest` | `containers/gitleaks/Dockerfile` | Gitleaks secret scanner + gRPC wrapper |
| `apme-galaxy-proxy:latest` | `containers/galaxy-proxy/Dockerfile` | PEP 503 proxy: Galaxy tarballs → Python wheels |
| `apme-gateway:latest` | `containers/gateway/Dockerfile` | REST/gRPC gateway + SQLite persistence |
| `apme-ui:latest` | `containers/ui/Dockerfile` | React SPA dashboard (nginx) |
| `apme-cli:latest` | `containers/cli/Dockerfile` | CLI client |

### Start the Pod

```bash
tox -e up
```

This runs `podman play kube containers/podman/pod.yaml`, which starts the pod `apme-pod` with eight containers (Primary, Native, OPA, Ansible, Gitleaks, Galaxy Proxy, Gateway, UI). A sessions directory and gateway data directory are created for session-scoped venvs and persistent activity data.

### Run CLI Commands

```bash
tox -e cli                              # default: check .
tox -e cli -- check --json .            # JSON output
tox -e cli -- check --diff .            # preview changes
tox -e cli -- remediate .               # Tier 1 fixes
tox -e cli -- format --check .          # YAML format check
tox -e cli -- health-check              # health check
```

The CLI container joins `apme-pod`, mounts CWD as `/workspace:Z` (read-write for `remediate`/`format`), and communicates with Primary at `127.0.0.1:50051` via gRPC.

The **`remediate`** command uses a **bidirectional streaming RPC** (`FixSession`, ADR-028, ADR-039) for real-time progress and interactive AI proposal review. **`check`** uses the same `FixSession` path in dry-run mode (ADR-039).

### Stop the Pod

```bash
tox -e down                             # stop pod only
tox -e wipe                             # stop pod and delete DB + session cache
```

### Health Check

```bash
apme health-check --primary-addr 127.0.0.1:50051
```

Reports status of all services (Primary, Native, OPA, Ansible, Gitleaks) with latency.

---

## Container Configuration

### Environment Variables

#### Primary

| Variable | Default | Description |
|----------|---------|-------------|
| `APME_PRIMARY_LISTEN` | `0.0.0.0:50051` | gRPC listen address |
| `NATIVE_GRPC_ADDRESS` | — | Native validator address (e.g., `127.0.0.1:50055`) |
| `OPA_GRPC_ADDRESS` | — | OPA validator address (e.g., `127.0.0.1:50054`) |
| `ANSIBLE_GRPC_ADDRESS` | — | Ansible validator address (e.g., `127.0.0.1:50053`) |

> If a validator address is unset, that validator is skipped during fan-out.

#### Native

| Variable | Default | Description |
|----------|---------|-------------|
| `APME_NATIVE_VALIDATOR_LISTEN` | `0.0.0.0:50055` | gRPC listen address |

#### OPA

| Variable | Default | Description |
|----------|---------|-------------|
| `APME_OPA_VALIDATOR_LISTEN` | `0.0.0.0:50054` | gRPC listen address |

> The OPA binary runs internally on `localhost:8181`; the gRPC wrapper proxies to it.

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
| `sessions` | `$CACHE/sessions` | `/sessions` | Primary, Ansible | rw (primary), ro (ansible) |
| `gateway-data` | `$CACHE/gateway` | `/data` | Gateway | rw |
| `proxy-cache` | `$CACHE/proxy` | `/cache` | Galaxy Proxy | rw |
| `workspace` | CWD (CLI only) | `/workspace` | CLI | rw |

---

## OPA Container Details

The OPA container uses a **multi-stage Dockerfile**:

1. **Stage 1**: Copies the `opa` binary from `docker.io/openpolicyagent/opa:latest`
2. **Stage 2**: Python slim image with `grpcio`, project code, and the Rego bundle

At runtime, `entrypoint.sh`:

1. Starts OPA as a REST server: `opa run --server --addr :8181 /bundle`
2. Waits for OPA to become healthy (polls `/health`)
3. Starts the Python gRPC wrapper (`apme-opa-validator`)

The **Rego bundle is baked into the image** at build time (no volume mount needed).

---

## Ansible Container Details

The Ansible container receives session-scoped venvs via the `/sessions` volume (read-only). The Primary orchestrator builds and manages these venvs using `VenvSessionManager`; the Ansible validator simply uses the `venv_path` provided in each `ValidateRequest`.

Collections are installed into the venv's `site-packages/ansible_collections/` directory by `uv pip install` through the Galaxy Proxy — they're on the Python path natively (no `ANSIBLE_COLLECTIONS_PATH` or `ansible.cfg` needed).

The Ansible validator requires a `venv_path` from the Primary orchestrator. If none is provided (e.g., standalone testing without Primary), the validator returns an infrastructure error and skips validation.

---

## Local Development (Daemon Mode)

For development and testing without the Podman pod, the CLI can start a
local daemon that runs the Primary, Native, OPA, and Ansible validators plus the Galaxy Proxy
in-process (ADR-024):

```bash
# Install tox + project (one-time)
uv tool install tox --with tox-uv
uv sync --extra dev --extra gateway

# Start the local daemon (background process)
apme daemon start

# Run commands (same thin CLI, talks to local daemon via gRPC)
apme check /path/to/project
apme check --diff .
apme remediate .

# Stop the daemon
apme daemon stop
```

**Daemon mode** starts a local Primary server with Native, OPA, and Ansible
validators running in-process. Gitleaks is excluded (requires the gitleaks binary). OPA runs via the local `opa` binary (no container
needed); skip it with `--no-opa` if `opa` is not installed.

The CLI is a **thin gRPC client** — it sends file bytes to the daemon and
receives results. It does not import engine internals.

---

## Troubleshooting

See `PODMAN_OPA_ISSUES.md` for common Podman rootless issues:

| Issue | Solution |
|-------|----------|
| `/run/libpod: permission denied` | Run in a real login shell, enable linger |
| Short-name resolution | Use fully qualified image names (`docker.io/...`) |
| `/bundle: permission denied` | Use `--userns=keep-id` and `:z` volume suffix |

---

## Quick Reference

### Build and Run

```bash
tox -e up                               # build + start
tox -e cli                              # run a scan (check .)
tox -e down                             # stop
tox -e wipe                             # stop + wipe DB/sessions
```

### Port Map

| Port | Service | Listen Variable |
|------|---------|-----------------|
| 50051 | Primary | `APME_PRIMARY_LISTEN` |
| 50053 | Ansible | `APME_ANSIBLE_VALIDATOR_LISTEN` |
| 50054 | OPA | `APME_OPA_VALIDATOR_LISTEN` |
| 50055 | Native | `APME_NATIVE_VALIDATOR_LISTEN` |
| 50056 | Gitleaks | `APME_GITLEAKS_VALIDATOR_LISTEN` |
| 50060 | Gateway (gRPC) | `APME_GATEWAY_GRPC_LISTEN` |
| 8080 | Gateway (HTTP) | `APME_GATEWAY_HTTP_LISTEN` |
| 8081 | UI (nginx) | — |
| 8765 | Galaxy Proxy | `APME_GALAXY_PROXY_URL` |

---

## Related Documents

- [ARCHITECTURE.md](/ARCHITECTURE.md) — Container topology and service contracts
- [DATA_FLOW.md](/DATA_FLOW.md) — Request lifecycle and serialization
- [ADR-004](/.sdlc/adrs/ADR-004-podman-pod-deployment.md) — Podman pod decision
- [ADR-006](/.sdlc/adrs/ADR-006-ephemeral-venvs.md) — Ephemeral venvs for Ansible (superseded by ADR-022/ADR-031)
- [ADR-024](/.sdlc/adrs/ADR-024-thin-cli-daemon-mode.md) — Thin CLI with local daemon mode
- [ADR-028](/.sdlc/adrs/ADR-028-session-based-fix-workflow.md) — Session-based fix workflow (FixSession bidi stream)
- [ADR-039](/.sdlc/adrs/ADR-039-unified-operation-stream.md) — Unified check/remediate via `FixSession`; `ScanStream` removed
