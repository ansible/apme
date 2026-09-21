# Deployment

> **Canonical user-facing guide:** [docs/guides/DEPLOYMENT.md](/docs/guides/DEPLOYMENT.md)
>
> This file provides AI-agent context. For the most complete and current
> deployment instructions (including bootc VM and Kubernetes operator), see the
> canonical guide linked above.

## Deployment Method Selection

> **IMPORTANT — Read this first.** Choose the deployment method based on the
> target environment. Do NOT use Podman commands on Kubernetes or OpenShift.

| Target environment | Deployment method | Tool | Guide |
|--------------------|-------------------|------|-------|
| Developer laptop / workstation | Podman pod | `tox -e up` | [Below](#podman-pod) |
| Linux server **without** Kubernetes | Podman pod or bootc VM | `tox -e up` or bootc | [Below](#podman-pod), [deploy/bootc/README.md](/deploy/bootc/README.md) |
| **Kubernetes / OpenShift** | **APME Operator** | Operator install + CR | [apme-operator](https://github.com/ansible/apme-operator) |
| Quick evaluation / CI | CLI daemon | `apme daemon start` | [CLI Guide](/docs/guides/CLI.md) |

**Key rule:** If the target has `kubectl` / `oc` access to a cluster, **always
use the [APME Operator](https://github.com/ansible/apme-operator)** in the
`ansible/apme-operator` repository. Podman pods are for local development and
non-Kubernetes Linux servers only.

## Podman Pod

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

This builds eleven images:

| Image | Dockerfile | Purpose |
|-------|------------|---------|
| `apme-engine:latest` | `containers/engine/Dockerfile` | Orchestrator + engine + session venv manager |
| `apme-native:latest` | `containers/native/Dockerfile` | Native Python validator |
| `apme-opa:latest` | `containers/opa/Dockerfile` | OPA + gRPC wrapper |
| `apme-ansible:latest` | `containers/ansible/Dockerfile` | Ansible validator (reads session venvs) |
| `apme-gitleaks:latest` | `containers/gitleaks/Dockerfile` | Gitleaks secret scanner + gRPC wrapper |
| `apme-collection-health:latest` | `containers/collection-health/Dockerfile` | Installed collection health scanner |
| `apme-dep-audit:latest` | `containers/dep-audit/Dockerfile` | Python CVE scanner (pip-audit) |
| `apme-galaxy-proxy:latest` | `containers/galaxy-proxy/Dockerfile` | PEP 503 proxy: Galaxy tarballs → Python wheels |
| `apme-gateway:latest` | `containers/gateway/Dockerfile` | REST/gRPC gateway + PostgreSQL persistence |
| `apme-ui:latest` | `containers/ui/Dockerfile` | React SPA dashboard (nginx) |
| `apme-cli:latest` | `containers/cli/Dockerfile` | CLI client |

The Abbenay AI image (`ghcr.io/redhat-developer/abbenay`) is pulled from the registry.

CI publishes multi-arch images (`linux/amd64` + `linux/arm64`) per
[ADR-063](/.sdlc/adrs/ADR-063-multi-platform-container-images.md) for tags built
after that ADR; older release tags stay amd64-only until rebuilt. Local
`tox -e build` remains host-native.

### Start the Pod

```bash
tox -e up
```

This runs `podman play kube containers/podman/pod.yaml`, which starts the pod `apme-pod` with all service containers (Engine, Native, OPA, Ansible, Gitleaks, Collection Health, Dep Audit, Galaxy Proxy, PostgreSQL, Gateway, UI, Abbenay). A sessions directory is created for session-scoped venvs, and the Podman volume `apme-postgres-data` is provisioned for PostgreSQL persistence (`APME_DATABASE_URL` points at the in-pod sidecar).

### Run CLI Commands

```bash
tox -e cli                              # default: check .
tox -e cli -- check --json .            # JSON output
tox -e cli -- check --diff .            # preview changes
tox -e cli -- remediate .               # Tier 1 fixes
tox -e cli -- format --check .          # YAML format check
tox -e cli -- health-check              # health check
```

The CLI container joins `apme-pod`, mounts CWD as `/workspace:Z` (read-write for `remediate`/`format`), and communicates with Engine at `127.0.0.1:50051` via gRPC.

The **`remediate`** command uses a **bidirectional streaming RPC** (`FixSession`, ADR-028, ADR-039) for real-time progress and interactive AI proposal review. **`check`** uses the same `FixSession` path in dry-run mode (ADR-039).

### Stop the Pod

```bash
tox -e down                             # stop pod only
tox -e wipe                             # stop pod and delete apme-postgres-data, sessions, Abbenay secrets.json
```

### Health Check

```bash
APME_ENGINE_ADDRESS=127.0.0.1:50051 apme health-check
```

Reports status of configured backend services (Engine, Native, OPA, Ansible, Galaxy Proxy, and any optional validators that are set — Gitleaks, Collection Health, Dep Audit) with latency.

---

## Kubernetes / OpenShift

Production Kubernetes and OpenShift deployments use the **APME Operator** in
[`ansible/apme-operator`](https://github.com/ansible/apme-operator). The
operator reconciles the same co-located pod topology as the Podman reference
deployment (engine stack + Gateway + UI + optional Abbenay on localhost).

See the operator repository for install steps, CRDs, and configuration.

---

## Container Configuration

### Environment Variables

#### Engine

| Variable | Default | Description |
|----------|---------|-------------|
| `APME_ENGINE_LISTEN` | `0.0.0.0:50051` | gRPC listen address |
| `NATIVE_GRPC_ADDRESS` | — | Native validator address (e.g., `127.0.0.1:50055`) |
| `OPA_GRPC_ADDRESS` | — | OPA validator address (e.g., `127.0.0.1:50054`) |
| `ANSIBLE_GRPC_ADDRESS` | — | Ansible validator address (e.g., `127.0.0.1:50053`) |
| `GITLEAKS_GRPC_ADDRESS` | — | Gitleaks validator address (e.g., `127.0.0.1:50056`) |
| `COLLECTION_HEALTH_GRPC_ADDRESS` | — | Collection Health validator address (e.g., `127.0.0.1:50058`) |
| `DEP_AUDIT_GRPC_ADDRESS` | — | Dep Audit validator address (e.g., `127.0.0.1:50059`) |
| `APME_ABBENAY_ADDR` | — | Abbenay AI daemon address (`unix://…` in Podman/operator when a consumer token is set) |
| `APME_REPORTING_ENDPOINT` | — | Gateway gRPC Reporting address (e.g., `127.0.0.1:50060`) |

> Required Engine-core services (Engine, Native, OPA, Ansible, Galaxy Proxy) must be available. Optional validators (Gitleaks, Collection Health, Dep Audit) may be unset and are skipped during fan-out.

#### Native

| Variable | Default | Description |
|----------|---------|-------------|
| `APME_NATIVE_VALIDATOR_LISTEN` | `0.0.0.0:50055` | gRPC listen address |

#### OPA

| Variable | Default | Description |
|----------|---------|-------------|
| `APME_OPA_VALIDATOR_LISTEN` | `0.0.0.0:50054` | gRPC listen address |

> The gRPC wrapper invokes `opa eval` via subprocess (not the REST server on :8181).

#### Ansible

| Variable | Default | Description |
|----------|---------|-------------|
| `APME_ANSIBLE_VALIDATOR_LISTEN` | `0.0.0.0:50053` | gRPC listen address |

#### Galaxy Proxy

| Variable | Default | Description |
|----------|---------|-------------|
| `APME_GALAXY_PROXY_URL` | `http://127.0.0.1:8765` | Galaxy proxy base URL |

#### Gateway (SCM / PR creation)

| Variable | Default | Description |
|----------|---------|-------------|
| `APME_SCM_TOKEN` | — | Global SCM token fallback for private clone and PR/MR creation (ADR-050) |
| `APME_GITHUB_API_URL` | `https://api.github.com` | GitHub API base (GHES: set to enterprise API URL) |
| `APME_GITLAB_API_URL` | `https://gitlab.com/api/v4` | GitLab API base (self-hosted: `https://gitlab.example.com/api/v4`) |
| `APME_BITBUCKET_API_URL` | `https://api.bitbucket.org/2.0` | Bitbucket API base (Server/DC: `https://bitbucket.example.com/rest/api/1.0`) |

Self-hosted GitLab/Bitbucket projects should also set project `scm_provider` to
`gitlab` or `bitbucket`, and must set `APME_GITLAB_API_URL` /
`APME_BITBUCKET_API_URL` to the forge API base (including any context path,
e.g. `https://corp.example/bitbucket/rest/api/1.0`). The Gateway does not
derive API bases from clone URLs (SSRF / context-path safety). Bitbucket Cloud
app passwords may be stored as `username:app_password` in the SCM token field.

### Volumes

| Name | Host Path | Container Mount | Services | Access |
|------|-----------|-----------------|----------|--------|
| `sessions` | `$CACHE/sessions` | `/sessions` | Engine, Ansible | rw (engine), ro (ansible) |
| `postgres-data` | Podman volume `apme-postgres-data` | `/var/lib/postgresql/data` | PostgreSQL | rw |
| `proxy-cache` | `$CACHE/proxy` | `/cache` | Galaxy Proxy | rw |
| `workspace` | CWD (CLI only) | `/workspace` | CLI | rw |
| `abbenay-run` | emptyDir | `/tmp/abbenay-run` | Engine, Gateway, Abbenay | rw |

---

## OPA Container Details

The OPA container uses a **multi-stage Dockerfile**:

1. **Stage 1**: Copies the `opa` binary from `docker.io/openpolicyagent/opa:1.17.1`
2. **Stage 2**: Base image with project code and the Rego bundle at `/bundle`

At runtime, `entrypoint.sh`:

1. Starts OPA REST server in background (`opa run --server --addr :8181 /bundle &`)
2. Waits for readiness (polls `/health` — used only for the entrypoint wait loop)
3. Starts the Python gRPC wrapper (`apme-opa-validator`) as PID 1

**Important:** The gRPC wrapper does **not** query the REST server. It invokes
`opa eval -I -d /bundle data.apme.rules.violations --format json` as a **subprocess**
for each evaluation (the binary is on PATH). The REST server is vestigial — it exists
only for the entrypoint's readiness check and could be removed.

In **daemon mode** (no container), OPA is invoked via `podman run --rm ... opa eval`
(one ephemeral container per evaluation), or directly via a local `opa` binary if
`OPA_USE_PODMAN=0` is set.

The **Rego bundle is baked into the image** at build time (no volume mount needed).

---

## Ansible Container Details

The Ansible container receives session-scoped venvs via the `/sessions` volume (read-only). The Engine builds and manages these venvs using `VenvSessionManager`; the Ansible validator simply uses the `venv_path` provided in each `ValidateRequest`.

Collections are installed into the venv's `site-packages/ansible_collections/` directory by `uv pip install` through the Galaxy Proxy — they're on the Python path natively (no `ANSIBLE_COLLECTIONS_PATH` or `ansible.cfg` needed).

The Ansible validator requires a `venv_path` from the Engine. If none is provided (e.g., standalone testing without Engine), the validator returns an infrastructure error and skips validation.

---

## Local Development (Daemon Mode)

For development and testing without the Podman pod, the CLI can start a
local daemon that runs the Engine, Native, OPA, and Ansible validators plus the Galaxy Proxy
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

**Daemon mode** starts a local Engine with Native, OPA, and Ansible
validators as in-process gRPC servers, plus Galaxy Proxy as an HTTP service
(uvicorn). Optional validators (Gitleaks, Collection Health, Dep Audit) are
not started by the daemon. The OPA validator gRPC server is always started;
policy evaluation uses Podman by default (`OPA_USE_PODMAN=1`) or a local
`opa` binary when `OPA_USE_PODMAN=0`. Install one before running scans that
depend on OPA rules.

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
tox -e wipe                             # stop + wipe apme-postgres-data/sessions/Abbenay secrets.json
```

### Port Map

| Port | Service | Listen Variable |
|------|---------|-----------------|
| 50051 | Engine | `APME_ENGINE_LISTEN` |
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

---

## Related Documents

- [Architecture series](/docs/architecture/) — Container topology and service contracts
- [APME Operator](https://github.com/ansible/apme-operator) — Kubernetes/OpenShift deployment
- [ADR-004](/.sdlc/adrs/ADR-004-podman-pod-deployment.md) — Podman pod decision
- [ADR-006](/.sdlc/adrs/ADR-006-ephemeral-venvs.md) — Ephemeral venvs for Ansible (superseded by ADR-022/ADR-031)
- [ADR-024](/.sdlc/adrs/ADR-024-thin-cli-daemon-mode.md) — Thin CLI with local daemon mode
- [ADR-028](/.sdlc/adrs/ADR-028-session-based-fix-workflow.md) — Session-based fix workflow (FixSession bidi stream)
- [ADR-039](/.sdlc/adrs/ADR-039-unified-operation-stream.md) — Unified check/remediate via `FixSession`; `ScanStream` removed
- [ADR-054](/.sdlc/adrs/ADR-054-production-deployment.md) — Production deployment (operator + bootc)
