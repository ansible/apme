# 17 — Scaling and Deployment Topology

> Previous: [16 — Diagnostics Instrumentation](16-diagnostics.md) | Next: [18 — Shared UI Workflow and Host Integration](18-shared-ui-workflow-and-hosts.md)

## Purpose

APME deploys as a single Podman pod containing all services. This
document covers the pod topology, volume mounts, port assignments, and
horizontal scaling strategy.

## Pod Topology

All containers in the pod share `localhost`. Addresses are fixed by
convention — there is no service discovery, no message queue.

```
┌────────────────────────────── apme-pod ───────────────────────────────┐
│                                                                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐ │
│  │ Engine  │  │  Native  │  │   OPA    │  │ Ansible  │  │ Gitleaks │ │
│  │  :50051  │  │  :50055  │  │  :50054  │  │  :50053  │  │  :50056  │ │
│  │          │  │          │  │          │  │          │  │          │ │
│  │ engine + │  │ Python   │  │ OPA bin  │  │ ansible- │  │ gitleaks │ │
│  │ orchestr │  │ rules on │  │ + gRPC   │  │ core     │  │ + gRPC   │ │
│  │ session  │  │ graph    │  │ wrapper  │  │ venvs    │  │ wrapper  │ │
│  │  venvs   │  │          │  │          │  │ (ro)     │  │          │ │
│  └────┬─────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘ │
│                                                                       │
│  ┌──────────────────┐  ┌──────────────────┐                           │
│  │ Collection Health │  │    Dep Audit     │                           │
│  │     :50058        │  │     :50059       │                           │
│  │ collection lint   │  │ pip-audit CVEs   │                           │
│  │ (sessions ro)     │  │ (sessions ro)    │                           │
│  └──────────────────┘  └──────────────────┘                           │
│       │                                                               │
│  ┌────┴─────────────────────────────────────┐                         │
│  │      Galaxy Proxy :8765 (PEP 503)        │                         │
│  └──────────────────────────────────────────┘                         │
│                                                                       │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐     │
│  │ PostgreSQL :5432 │  │ Gateway :8080    │  │ UI :8081 (nginx) │     │
│  │ sidecar          │──►│ REST API +       │◄─┤ React SPA        │     │
│  │ postgres-data vol│  │ gRPC Reporting   │  │ /api/ → Gateway  │     │
│  │                  │  │ :50060           │  │                  │     │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘     │
│  ┌──────────────────┐                                                   │
│  │ Abbenay :50057   │                                                   │
│  │ AI inference     │                                                   │
│  │ gateway          │                                                   │
│  │ (optional)       │                                                   │
│  └──────────────────┘                                                   │
└───────────────────────────────────────────────────────────────────────┘

     ┌──────────┐
     │   CLI    │  podman run --rm --pod apme-pod
     │ (on-the  │  -v $(pwd):/workspace:ro,Z
     │  -fly)   │  apme-cli:latest apme check .
     └──────────┘
```

## Port Map

| Port | Service | Protocol | Purpose |
|------|---------|----------|---------|
| 50051 | Engine | gRPC | Engine orchestrator — sole client API surface |
| 50053 | Ansible | gRPC | Ansible-runtime validator |
| 50054 | OPA | gRPC | OPA policy validator (subprocess wrapper) |
| 50055 | Native | gRPC | Python graph rules validator |
| 50056 | Gitleaks | gRPC | Secrets scanner (subprocess wrapper) |
| 50057 | Abbenay | gRPC | AI inference gateway (optional) |
| 50058 | Collection Health | gRPC | Installed collection health scanner (optional) |
| 50059 | Dep Audit | gRPC | Python CVE scanner via pip-audit (optional) |
| 50060 | Gateway | gRPC | Reporting service (receives engine events) |
| 8080 | Gateway | HTTP | REST API for UI and external consumers |
| 8081 | UI | HTTP | nginx-served React SPA (proxies `/api/` to Gateway) |
| 8765 | Galaxy Proxy | HTTP | PEP 503 simple repository API for collection wheels |
| 8889 | OTel Collector | HTTP | Prometheus metrics scrape (`/metrics`) — ADR-067 |

## Volume Mounts

| Volume | Mount path | Services | Access | Purpose |
|--------|-----------|----------|--------|---------|
| `sessions` | `/sessions` | Engine (rw), Ansible, Collection Health, Dep Audit (ro) | Named volume | Session-scoped venvs with ansible-core + installed collections |
| `workspace` | `/workspace` | CLI (ro) | Bind mount from host CWD | Project being scanned |

### Sessions Volume

Engine is the single writer to `/sessions` (ADR-022). Each session gets
a directory keyed by `session_id`, with sub-directories per
`ansible_core_version`. The Ansible validator mounts this volume
read-only to access the resolved venv for runtime checks.

### Workspace Volume

The CLI container bind-mounts the user's current working directory at
`/workspace` (`:Z` for SELinux). The CLI reads files from this mount and sends
their bytes through `FixSession`; Engine receives the uploaded file bytes rather
than reading the mount directly.

## Horizontal Scaling

**Scale pods, not individual services within a pod.** The engine runtime
is a unit: Engine + all validators + Galaxy Proxy. Each pod can process
a scan request end-to-end.

```
                    ┌─────────────┐
  FixSession ─────► │ Load        │
                    │ Balancer    │
                    │ (K8s Svc)   │
                    └──┬──┬──┬────┘
                       │  │  │
              ┌────────┘  │  └────────┐
              ▼           ▼           ▼
         ┌─────────┐ ┌─────────┐ ┌─────────┐
         │ Pod 1   │ │ Pod 2   │ │ Pod 3   │
         │ (full   │ │ (full   │ │ (full   │
         │  stack) │ │  stack) │ │  stack) │
         └─────────┘ └─────────┘ └─────────┘
```

### Why pod-level scaling

Within a pod, containers share `localhost` — no configuration change is
needed when replicating. If a single validator is the bottleneck, the
fix is parallelism *inside* that validator (e.g., increasing
`maximum_concurrent_rpcs`, task-level concurrency), not extracting it
into a separate deployment.

This follows architectural invariant #6 from `AGENTS.md`: the engine
runtime is replicated as a unit. Do not extract individual validators
into separate deployments.

### Galaxy Proxy extraction

The Galaxy Proxy could be extracted to a shared service across pods to
share a single wheel cache. For single-pod deployments this is
unnecessary. The proxy's internal cache handles repeat installs within
a pod.

### Gateway and UI

Gateway, UI, and Abbenay are pod-level / enterprise services. They are
not part of the engine scaling unit. In a multi-pod deployment:

- A single Gateway instance receives events from all engine pods
- The UI connects to one Gateway
- Abbenay can be shared or per-pod depending on AI capacity needs

## CLI Deployment Modes

The CLI operates in two modes:

### Daemon mode (default)

The CLI auto-starts a local daemon process that runs Engine + required
validators + Galaxy Proxy. Optional validators (Gitleaks, Collection Health,
Dep Audit) start when `include_optional=True`. The daemon persists across CLI
invocations for session reuse.

### Pod mode (`--pod`)

The CLI runs as an ephemeral container in the Podman pod:

```bash
podman run --rm --pod apme-pod \
  -v $(pwd):/workspace:ro,Z \
  apme-cli:latest apme check .
```

The CLI connects to the Engine at `127.0.0.1:50051` within the pod
network.

## Container Images

| Image | Base | Contents |
|-------|------|----------|
| `apme-engine` | Python 3.12 UBI10 | Engine, Engine server, VenvSessionManager |
| `apme-native` | Python 3.12 UBI10 | Native validator server |
| `apme-opa` | Python 3.12 UBI10 + OPA binary | OPA validator server + Rego bundle |
| `apme-ansible` | Python 3.12 UBI10 | Ansible validator server |
| `apme-gitleaks` | Python 3.12 UBI10 + gitleaks binary | Gitleaks validator server |
| `apme-collection-health` | Python 3.12 UBI10 | Collection health validator server |
| `apme-dep-audit` | Python 3.12 UBI10 + pip-audit | Python CVE scanner server |
| `apme-galaxy-proxy` | Python 3.12 UBI10 | PEP 503 proxy server |
| `apme-gateway` | Python 3.12 UBI10 | FastAPI + gRPC Reporting |
| `apme-ui` | nginx UBI10 | React SPA static files |
| `apme-abbenay` | Python 3.12 slim | AI inference gateway (external image) |
| `apme-cli` | Python 3.12 UBI10 | CLI tools only |

All containers run as non-root (see `SECURITY.md`).

## Build and Lifecycle

All container operations use tox (ADR-047):

| Command | Purpose |
|---------|---------|
| `tox -e build` | Build all container images |
| `tox -e up` | Start the Podman pod |
| `tox -e down` | Stop the Podman pod |
| `tox -e cli` | Run the CLI in the pod |

Rebuild is required after modifying: `src/**/*.py`, `proto/**/*.proto`,
`pyproject.toml`, `containers/**`. No rebuild needed for documentation
changes.

## Key Source Files

| File | Role |
|------|------|
| `containers/podman/build.sh` | Image build script (invoked by `tox -e build`) |
| `containers/podman/up.sh` | Pod start script (invoked by `tox -e up`) |
| `containers/podman/down.sh` | Pod stop script (invoked by `tox -e down`) |
| `containers/podman/run-cli.sh` | CLI container script (invoked by `tox -e cli`) |

## Related ADRs

- **ADR-012** — Scale pods, not individual services
- **ADR-022** — Engine is sole venv writer (`/sessions` volume)
- **ADR-047** — tox is the sole orchestration tool

---

> Previous: [16 — Diagnostics Instrumentation](16-diagnostics.md) | Next: [18 — Shared UI Workflow and Host Integration](18-shared-ui-workflow-and-hosts.md)
