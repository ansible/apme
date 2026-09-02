# Deployment Guide

APME supports multiple deployment methods depending on your environment and needs.

| Target environment | Method | Details |
|--------------------|--------|---------|
| Developer laptop / Linux server (no K8s) | **Podman pod** | [Below](#podman-pod) |
| **Kubernetes / OpenShift** | **APME Operator** | [Kubernetes section](#kubernetes--openshift) / [apme-operator](https://github.com/ansible/apme-operator) |
| Production single-node VM | **bootc VM** | [bootc section](#bootc-vm) / [full guide](../../deploy/bootc/README.md) |
| Quick evaluation / CI | **CLI daemon** | [CLI Guide](CLI.md) |

> **Deploying on Kubernetes or OpenShift?** Use the [APME Operator](https://github.com/ansible/apme-operator)
> in the `ansible/apme-operator` repository. Do not use Podman on K8s/OCP.

Full deployment methods (Podman, bootc, operator) run the complete engine stack
(Engine + all validators + Galaxy Proxy). The CLI daemon runs Engine plus core
validators (Native, OPA, Ansible) + Galaxy Proxy. Optional validators
(Gitleaks, Collection Health, Dep Audit) are not started unless
`include_optional=True`. Gateway HTTP/Reporting gRPC co-location in the local
daemon is planned (ADR-049) but not implemented in `launcher.py` yet — use the
Podman pod or start Gateway separately for REST-backed commands such as
`apme sbom`. The difference from full deployment is lifecycle management and
additional pod-level services (Gateway, UI, Abbenay AI, OTel Collector).

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
| `apme-engine:latest` | `containers/engine/Dockerfile` | Orchestrator + engine + session venv manager |
| `apme-native:latest` | `containers/native/Dockerfile` | Native Python validator |
| `apme-opa:latest` | `containers/opa/Dockerfile` | OPA + gRPC wrapper |
| `apme-ansible:latest` | `containers/ansible/Dockerfile` | Ansible validator (reads session venvs) |
| `apme-gitleaks:latest` | `containers/gitleaks/Dockerfile` | Gitleaks secret scanner + gRPC wrapper |
| `apme-collection-health:latest` | `containers/collection-health/Dockerfile` | Installed collection health scanner |
| `apme-dep-audit:latest` | `containers/dep-audit/Dockerfile` | Python CVE scanner (pip-audit) |
| `apme-galaxy-proxy:latest` | `containers/galaxy-proxy/Dockerfile` | PEP 503 proxy: Galaxy tarballs → Python wheels |
| `apme-gateway:latest` | `containers/gateway/Dockerfile` | REST API + gRPC Reporting service (SQLite or PostgreSQL) |
| `apme-ui:latest` | `containers/ui/Dockerfile` | React SPA served by nginx (proxies API to Gateway) |
| `apme-cli:latest` | `containers/cli/Dockerfile` | CLI client |
| `ghcr.io/redhat-developer/abbenay:v2026.8.7` | [Official image](https://github.com/redhat-developer/abbenay/pkgs/container/abbenay) (pulled) | Abbenay AI daemon (LLM gateway for Tier 2 remediation) |

### Configure Abbenay AI (optional)

Abbenay provides LLM-backed AI remediation (Tier 2). Each developer supplies their own API key:

```bash
cp containers/abbenay/.env.example containers/abbenay/.env
# Edit .env and set your LLM provider API key (e.g., OPENROUTER_API_KEY)
```

The `.env` file is gitignored. Abbenay config is **seeded** from
`containers/abbenay/config/` (or legacy `config.yaml` /
`config.yaml.example`) into
`${XDG_CACHE_HOME:-$HOME/.cache}/apme/abbenay/config/` (override with
`APME_CACHE_HOST_PATH`). That cache directory is the RW hostPath mount
(`0700` / `0600`). Rootful Podman chowns the cache copy to UID 1001; rootless
keeps host ownership and grants UID 1001 a POSIX ACL so you can still edit the
file. The git checkout is never chowned. Edit the cache `config.yaml` (or use
the Gateway admin proxy) to change providers/models — runtime configure writes
persist across container restarts.

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

This runs `podman play kube containers/podman/pod.yaml`, which starts the pod `apme-pod` with twelve service containers (Engine, Native, OPA, Ansible, Gitleaks, Collection Health, Dep Audit, Galaxy Proxy, Gateway, UI, Abbenay, OTel Collector). The `up.sh` script sources `containers/abbenay/.env` to inject LLM API keys into the Abbenay container. A sessions directory is created for session-scoped venvs.

### Run CLI commands

```bash
tox -e cli                              # default: check .
tox -e cli -- check --json .            # JSON output
tox -e cli -- check --diff .            # dry-run with diffs
tox -e cli -- remediate .               # Tier 1 fixes
tox -e cli -- format --check .          # YAML format check
tox -e cli -- health-check              # health check
```

The CLI container joins `apme-pod`, mounts CWD as `/workspace:Z` (read-write for `remediate`/`format`), and communicates with Engine at `127.0.0.1:50051` via gRPC.

The `remediate` command uses a bidirectional streaming RPC (`FixSession`, ADR-028, ADR-039) for real-time progress and interactive AI proposal review. **`check`** uses the same `FixSession` RPC in check mode.

### Stop the pod

```bash
tox -e down
tox -e wipe    # also delete database, session cache, and Abbenay secrets.json
```

### Health check

```bash
apme health-check
```

The CLI discovers the Engine via `APME_ENGINE_ADDRESS` env var, a running daemon, or auto-starts one locally.

Reports status of Engine-core services (Engine, Native, OPA, Ansible, Galaxy
Proxy, Gateway HTTP, Gateway Reporting gRPC) plus optional validators
(Gitleaks, Collection Health, Dep Audit) with latency.

## Container configuration

### Environment variables

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
| `APME_REPORTING_ENDPOINT` | — | Gateway gRPC Reporting address (e.g., `127.0.0.1:50060`). Events are pushed after each check or remediate run. |
| `APME_ABBENAY_ADDR` | — | Abbenay AI daemon address. Podman default is `unix:///tmp/abbenay-run/abbenay/daemon.sock` (required when a consumer token is set; `abbenay-client` ≥ 2026.8.7 rejects tokens on plaintext TCP). Also accepts `host:port`. |
| `APME_ABBENAY_TOKEN` | — | Consumer token for Abbenay authentication. Must match a token in Abbenay's `config.yaml`. |
| `APME_AI_MODEL` | — | Default AI model ID (e.g., `anthropic/claude-sonnet-4`). Overridden by UI Settings or CLI `--model`. |
| `APME_RULE_AUTHORITY` | `true` | Set to `true` on exactly one Engine in multi-pod deployments. Only the authority registers the rule catalog to the Gateway (ADR-041). |

If a validator address is unset, that validator is skipped during fan-out. If Abbenay is unreachable, AI remediation is skipped (Tier 1 deterministic fixes still run).

#### Native

| Variable | Default | Description |
|----------|---------|-------------|
| `APME_NATIVE_VALIDATOR_LISTEN` | `0.0.0.0:50055` | gRPC listen address |

#### OPA

| Variable | Default | Description |
|----------|---------|-------------|
| `APME_OPA_VALIDATOR_LISTEN` | `0.0.0.0:50054` | gRPC listen address |

The OPA validator evaluates Rego policies by invoking ``opa eval`` via subprocess
(see ``opa_validator_server.py``). A local OPA REST server on ``:8181`` may still
start in the container image for readiness probes, but application code does not
proxy gRPC requests to it.

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
| `APME_DATABASE_URL` | *(unset)* | Optional SQLAlchemy URL for PostgreSQL (e.g. `postgresql+asyncpg://user:pass@host:5432/apme`). When set, takes precedence over SQLite. |
| `APME_DB_PATH` | `/data/apme.db` | Path to the SQLite database when `APME_DATABASE_URL` is unset (stores activity, sessions, rule catalog, and rule overrides) |
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

Abbenay uses `${XDG_CACHE_HOME:-$HOME/.cache}/apme/abbenay/config/` as a
**writable** hostPath mount (`abbenay-config` → `/home/abbenay/.config/abbenay`).
`up.sh` seeds that cache dir from `containers/abbenay/config/` (or legacy
files) and never chowns the git checkout. The config defines LLM providers and
models. Deploy-time API keys are injected from environment variables — never
committed to the config file. Runtime file-store keys (Abbenay ≥ v2026.8.6,
`secretStore: file`) are written to `secrets.json` on this same cache
directory (treat as secret material). On macOS, virtiofs cannot give
container UID 1001 access to `secrets.json` without world-opening it; file
store is unsupported there until [#562](https://github.com/ansible/apme/issues/562)
(use env or memory). `tox -e down` leaves that file in
place; `tox -e wipe` deletes it. To add providers or models, edit the cache
`config.yaml` or POST via the Gateway admin proxy
(`/api/v1/ai/provider/{id}/configure`); writes survive Abbenay restarts.

**Local Podman dev UI:** `tox -e up` publishes Abbenay HTTP admin on
`http://127.0.0.1:8787` (`hostPort` with `hostIP: 127.0.0.1` only — not LAN).
`pod.yaml` sets `ABBENAY_HTTP_AUTH=0` so the dashboard loads without a Bearer
token on that localhost bind. The operator deployment keeps Abbenay HTTP/gRPC on
loopback within the pod (ADR-070). gRPC for Engine/Gateway remains the shared
Unix socket.

The Abbenay daemon still binds leftover gRPC TCP on `127.0.0.1:50057`. Engine AI RPCs, Gateway `/health`, and health probes use `APME_ABBENAY_ADDR=unix:///tmp/abbenay-run/abbenay/daemon.sock` (shared `emptyDir`) because `abbenay-client` ≥ 2026.8.7 rejects consumer tokens on plaintext TCP.

#### UI

The UI container has no environment variables. It serves the React SPA via nginx and proxies `/api/` requests to the Gateway at `127.0.0.1:8080` (same pod network namespace).

The Settings page (`/settings`) provides a model picker that queries available AI models from Abbenay via the gateway. The selected model is stored in the browser's `localStorage`. The Rules page (`/rules`) displays the rule catalog with enable/disable toggles, severity overrides, and category/source filters (ADR-041).

### Volumes

| Name | Host Path | Container Mount | Services | Access |
|------|-----------|-----------------|----------|--------|
| `sessions` | `apme-sessions/` | `/sessions` | Engine (rw), Ansible, Collection Health, Dep Audit (ro) | rw / ro |
| `proxy-cache` | `<cache>/proxy/` | `/cache` | Galaxy Proxy | rw |
| `gateway-data` | `<cache>/gateway/` | `/data` | Gateway | rw |
| `abbenay-config` | `<cache>/abbenay/config/` | `/home/abbenay/.config/abbenay` | Abbenay | rw |
| `abbenay-run` | emptyDir | `/tmp/abbenay-run` | Engine + Gateway + Abbenay | rw |
| `workspace` | CWD (CLI only) | `/workspace` | CLI | rw |

#### Observability (Podman pod only)

The reference Podman pod also runs an **OpenTelemetry Collector** (ADR-067) that
receives OTLP on `:4318` and exposes Prometheus metrics on `:8889`. This is not
included in the operator deployment by default.

## OPA container details

The OPA container uses a multi-stage Dockerfile:

1. **Stage 1**: Copies the `opa` binary from `docker.io/openpolicyagent/opa:1.17.1`
2. **Stage 2**: Python 3.12 UBI10 base image with `grpcio`, project code, and the Rego bundle

At runtime, `entrypoint.sh` may start OPA as a REST server for readiness
(`opa run --server --addr :8181 /bundle`), then starts the Python gRPC validator
(`apme-opa-validator`), which evaluates policies via ``opa eval`` subprocess —
not via the REST API.

The Rego bundle is baked into the image at build time (no volume mount needed).

### Ansible container details

The Ansible container receives session-scoped venvs via the `/sessions` volume (read-only). The Engine builds and manages these venvs using `VenvSessionManager`; the Ansible validator simply uses the `venv_path` provided in each `ValidateRequest`.

Collections are installed into the venv's `site-packages/ansible_collections/` directory by `uv pip install` through the Galaxy Proxy — they're on the Python path natively (no `ANSIBLE_COLLECTIONS_PATH` or `ansible.cfg` needed).

The Ansible validator requires a `venv_path` from the Engine. If none is provided (e.g., standalone testing without Engine), the validator returns an infrastructure error and skips validation.

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
in the Engine container — the venv manager reads this at runtime and passes
it as the `--index-strategy` argument to `uv pip install`.

## Local development (daemon mode)

For development and testing without the Podman pod, the CLI can start a
local daemon that runs Engine, Native, OPA, and Ansible as localhost gRPC
servers, Galaxy Proxy as an HTTP service, and Gateway HTTP plus Reporting
gRPC per ADR-049. Optional validators (Gitleaks, Collection Health, Dep Audit)
start only when `include_optional=True`.

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

**Daemon mode** starts a local Engine with Native, OPA, and Ansible validators as in-process gRPC servers, Galaxy Proxy as an HTTP service (uvicorn), and Gateway HTTP plus Reporting gRPC (ADR-049). Optional validators (Gitleaks, Collection Health, Dep Audit) start only when `include_optional=True`. The OPA validator gRPC server is always started; policy evaluation uses Podman by default or a local `opa` binary when `OPA_USE_PODMAN=0`. OPA infrastructure failures surface as validator R902 errors so `check` and `remediate` cannot return silently incomplete results.

## Troubleshooting

See [PODMAN_OPA_ISSUES.md](PODMAN_OPA_ISSUES.md) for common Podman rootless issues:

- `/run/libpod: permission denied` — run in a real login shell, enable linger
- Short-name resolution — use fully qualified image names (`docker.io/...`)
- `/bundle: permission denied` — use `--userns=keep-id` and `:z` volume suffix

## Port Map quick reference

| Port | Service | Listen Variable |
|------|---------|-----------------|
| 50051 | Engine | `APME_ENGINE_LISTEN` |
| 50053 | Ansible | `APME_ANSIBLE_VALIDATOR_LISTEN` |
| 50054 | OPA | `APME_OPA_VALIDATOR_LISTEN` |
| 50055 | Native | `APME_NATIVE_VALIDATOR_LISTEN` |
| 50056 | Gitleaks | `APME_GITLEAKS_VALIDATOR_LISTEN` |
| 50057 | Abbenay AI gRPC (pod-local) | `--grpc-host 127.0.0.1 --grpc-port` (no hostPort) |
| 50058 | Collection Health | `APME_COLLECTION_HEALTH_VALIDATOR_LISTEN` |
| 50059 | Dep Audit | `APME_DEP_AUDIT_VALIDATOR_LISTEN` |
| 50060 | Gateway (gRPC) | `APME_GATEWAY_GRPC_LISTEN` |
| 8080 | Gateway (HTTP) | `APME_GATEWAY_HTTP_PORT` |
| 8081 | UI (nginx) | — |
| 8765 | Galaxy Proxy | `APME_GALAXY_PROXY_URL` |
| 8787 | Abbenay HTTP admin UI (Podman `hostPort`, `hostIP: 127.0.0.1`) | `--host 0.0.0.0 --port` |

## Related Documents

- [CLI Guide](CLI.md) — CLI installation, commands, and limitations
- [bootc full guide](../../deploy/bootc/README.md) — Complete bootc VM documentation
- [APME Operator](https://github.com/ansible/apme-operator) — Kubernetes/OpenShift deployment
- [ADR-006](../../.sdlc/adrs/ADR-006-ephemeral-venvs.md) — Ephemeral venvs for Ansible (superseded by ADR-022/ADR-031)
- [ADR-054](../../.sdlc/adrs/ADR-054-production-deployment.md) — Production Deployment (operator + bootc)

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

## Kubernetes / OpenShift

Deploy APME on Kubernetes or OpenShift using the **APME Operator** in the
[`ansible/apme-operator`](https://github.com/ansible/apme-operator) repository.
The operator manages APME custom resources and reconciles the full service
stack (engine, validators, Galaxy Proxy, Gateway, UI, and optional Abbenay)
using the same localhost co-located pod model as the Podman reference deployment
(ADR-005, ADR-012).

**Highlights:**

- Operator-managed lifecycle (install, upgrade, reconcile)
- All-in-one pod topology: engine stack + Gateway + UI + optional Abbenay on localhost
- Ingress/Route support for external REST and UI access
- PVCs for sessions, Gateway database, and Galaxy Proxy cache
- Published container images are multi-arch (`linux/amd64` + `linux/arm64`) per ADR-063

### Quick start

See the operator repository for installation prerequisites, CRD definitions,
and example manifests:

- Repository: [https://github.com/ansible/apme-operator](https://github.com/ansible/apme-operator)
- Install the operator (OLM or manual — follow the operator README)
- Create an `APME` (or equivalent) custom resource with your desired configuration

For Abbenay AI provider setup, secrets, and troubleshooting on Kubernetes, see
[ABBENAY_AI.md](ABBENAY_AI.md) (Podman/operator sections) and the operator
documentation.

### Container images

Application images published by CI after
[ADR-063](../../.sdlc/adrs/ADR-063-multi-platform-container-images.md) are
**multi-arch** (`linux/amd64` and `linux/arm64`) under the same registry tags.
The operator references these images; pin tags explicitly for production
environments.
