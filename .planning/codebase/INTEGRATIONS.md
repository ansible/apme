# External Integrations

**Analysis Date:** 2026-03-25

## APIs & External Services

**Ansible Galaxy (Public/Automation Hub/Private):**
- Public Galaxy - https://galaxy.ansible.com (default)
- Automation Hub - Private instance support via credentials
- Custom instances - Any Galaxy-compatible API server
- SDK/Client: `galaxy_proxy/galaxy_client.py` uses async `httpx.AsyncClient`
  - Default URL: `https://galaxy.ansible.com`
  - Auth: Optional token via `Authorization: Token <value>` header (per-server configuration)
  - Purpose: Collection discovery, version resolution, tarball download (PEP 503 proxy per ADR-031)

**Abbenay (AI Remediation):**
- Service: `ghcr.io/redhat-developer/abbenay:2026.3.8-alpha`
- gRPC Daemon Address: `127.0.0.1:50057` (internal pod networking; discoverable via socket or env var)
- SDK/Client: `abbenay_grpc.AbbenayClient` (lazy-imported in `src/apme_engine/remediation/abbenay_provider.py`)
  - Connection modes: Unix socket (socket_path) or TCP (host:port)
  - Auto-discovery: Searches `$XDG_RUNTIME_DIR/abbenay/daemon.sock` → `/run/user/{uid}/abbenay/daemon.sock` → `/tmp/abbenay/daemon.sock`
- Auth: Token-based via `APME_ABBENAY_TOKEN` environment variable ("apme-dev-token" in dev)
- Model override: Configurable via `APME_AI_MODEL` env var (passed to daemon)
- Purpose: LLM-driven code patch generation for tier-2/tier-3 violations
- Downstream: Abbenay consumes `OPENROUTER_API_KEY` for LLM API access (configured in `containers/abbenay/config.yaml`)

**Ansible Core/Plugins:**
- Vendored ARI (Ansible Rule Infrastructure) engine - NOT a pip dependency (per ADR-003)
  - Location: Built into `apme_engine` package
  - Used by: Native validator for builtin module annotation and rule evaluation
- Ansible Galaxy collections - Dynamically downloaded and installed in session venvs
  - Managed by: Primary orchestrator via `venv_manager`
  - Cached in: `/sessions` volume (read-write for Primary, read-only for other validators)

## Data Storage

**Databases:**
- SQLite 3.30+
  - Driver: `aiosqlite` (async SQLite client via `sqlite+aiosqlite://` URL)
  - Path: `${APME_DB_PATH}` env var (default `/data/apme.db`)
  - Location: `src/apme_gateway/db/`
  - ORM: SQLAlchemy 2.0 with async session factory
  - Persistence: Host mount at `/data` volume in `containers/podman/pod.yaml`
  - Tables: projects, sessions, scans, violations, proposals, scan_logs (per ADR-020, ADR-029)

**File Storage:**
- Local filesystem only - No external blob storage
  - Session venvs: `/sessions` volume (shared between Primary and validators)
  - Gateway data: `/data` volume (SQLite database + temporary files)
  - Proxy cache: `/cache` volume (collection tarballs for PEP 503 serving)

**Caching:**
- None (external). In-memory caching within Python processes:
  - Gateway API: SWR (stale-while-revalidate) on frontend for scan data
  - Galaxy Proxy: Local filesystem cache at `/cache` (collection wheels)
  - ARI engine: Cached YAML parsing via joblib in-process

## Authentication & Identity

**Auth Provider:**
- None - Custom token-based auth for Abbenay only
  - Implementation: Manual token header injection in `abbenay_provider.py`
  - Token source: `APME_ABBENAY_TOKEN` env var or constructor parameter
- Gateway REST API: No authentication layer (intended for internal/trusted networks)
- gRPC services: No authentication (TCP/socket level only)

## Monitoring & Observability

**Error Tracking:**
- None (external). Errors logged locally:
  - Python: `logging` module (INFO, WARNING, ERROR, EXCEPTION levels)
  - Diagnostic capture: ScanDiagnostics serialized to `diagnostics_json` in database

**Logs:**
- Approach: Structured logging to stdout/stderr (container logs)
  - Python: Standard library `logging` with configuration per service
  - Gateway: REST API logs via FastAPI/uvicorn
  - Schema: `phase` field in `scan_logs` table distinguishes subsystem (engine, native, opa, etc.)
- No external log aggregation; logs captured by container runtime

**Health Checks:**
- gRPC Health: `Primary.Health()` RPC endpoint
- Abbenay: `AbbenayClient.health_check()` async method in `abbenay_provider.py`

## CI/CD & Deployment

**Hosting:**
- Podman Pods (primary)
- OpenShift (Podman-based)
- Docker (compatible via mapping)

**CI Pipeline:**
- None detected (no GitHub Actions, GitLab CI, Jenkins config in codebase)
- Pre-commit hooks: gitleaks (secrets detection), bandit (security), pytest (tests)

**Build:**
- Container images built via `containers/podman/build.sh`
- Multi-stage Dockerfiles in `containers/*/`
- Dependency caching: uv cache mounts in base layer

**Deployment:**
- `containers/podman/up.sh` - Start pod with `podman play kube pod.yaml`
- `containers/podman/down.sh` - Stop pod
- Pod manifest: `containers/podman/pod.yaml` (Podman-native; K8s conversion needed)

## Environment Configuration

**Required env vars:**
- `APME_AI_MODEL` - LLM model for Abbenay (e.g., "openai/gpt-4o")
- `APME_ABBENAY_TOKEN` - Auth token for Abbenay daemon
- `OPENROUTER_API_KEY` - Abbenay's downstream LLM API key (in Abbenay config, not APME env)
- `APME_DB_PATH` - SQLite database file path (default `/data/apme.db`)
- `APME_PRIMARY_LISTEN` - gRPC listen address (default `0.0.0.0:50051`)
- `APME_GALAXY_PROXY_URL` - Galaxy Proxy URL for collection resolution (default `http://127.0.0.1:8765`)
- `APME_REPORTING_ENDPOINT` - Gateway gRPC address for scan result reporting (default `127.0.0.1:50060`)
- Validator listen addresses: `APME_NATIVE_VALIDATOR_LISTEN`, `APME_OPA_VALIDATOR_LISTEN`, `APME_ANSIBLE_VALIDATOR_LISTEN`, `APME_GITLEAKS_VALIDATOR_LISTEN`
- `APME_GATEWAY_GRPC_LISTEN` - Gateway gRPC server address (default `0.0.0.0:50060`)
- `APME_GATEWAY_HTTP_HOST` / `APME_GATEWAY_HTTP_PORT` - REST API binding (default `0.0.0.0:8080`)
- `APME_COLLECTION_CACHE` - Session venv cache directory (default `/sessions`)

**Secrets location:**
- `.env` file (local development; NOT committed to git)
- Podman secrets or environment injection in `pod.yaml` (production)
- `containers/abbenay/config.yaml` mounted as file volume for Abbenay daemon

## Webhooks & Callbacks

**Incoming:**
- None - APME is request/response only (no push/event handlers)

**Outgoing:**
- None - APME queries external services; does not call back

**gRPC Streams:**
- Bidirectional: `Primary.FixSession()` - Client streams session commands; server streams events
- Server streams: `Primary.ScanStream()`, `Primary.FormatStream()` - Server sends progress/results
- Reporting: `Reporting.ReportScanStarted()`, `Reporting.ReportScanResult()`, `Reporting.ReportProposal()` - Primary → Gateway gRPC

## Service-to-Service Communication

**All inter-service communication uses gRPC (ADR-001):**

| Service | Port | Protocol | Purpose |
|---------|------|----------|---------|
| Primary | 50051 | gRPC | Orchestrator; client entry point |
| Native Validator | 50055 | gRPC | Python/YAML linting rules |
| OPA Validator | 50054 | gRPC | Rego policy rules |
| Ansible Validator | 50053 | gRPC | Ansible-specific rules |
| Gitleaks Validator | 50056 | gRPC | Secret/credential detection |
| Abbenay | 50057 | gRPC | AI remediation |
| Gateway | 50060 | gRPC | Reporting receiver (from Primary) |
| Gateway | 8080 | HTTP/REST | REST API (frontend + CLI) |
| Galaxy Proxy | 8765 | HTTP | PEP 503 wheel index (to Ansible Validator) |
| UI | 8081 | HTTP | React SPA (served by nginx) |

**Service Discovery:**
- Internal: Environment variables in pod.yaml (hostnames: `127.0.0.1`)
- External (CLI): Primary daemon at `localhost:50051` (default)

---

*Integration audit: 2026-03-25*
