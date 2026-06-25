# APME Changes Required for Portal Integration

**Date:** 2026-06-25
**Status:** Research / Design Proposal
**Tracking:** ANSTRAT-2222

## Overview

This document outlines the changes required in APME to support integration with the Ansible Automation Portal. The core architectural decisions are:

1. **Portal owns all scan data** — stored in Portal's PostgreSQL (apme_* tables)
2. **APME Gateway becomes stateless** — no database, no PVC, thin REST↔gRPC proxy
3. **Portal clones repos** — Portal uses admin SCM token (same as repo discovery in git views); tars content and sends to APME via REST
4. **APME never touches SCM credentials** — receives only file content as tarballs; PRs created Portal-side using user's OAuth token
5. **APME standalone UI is removed** — Portal is the only presentation layer

## C4 Context: APME in the Portal Ecosystem

```
                     ┌───────────────────────┐
                     │  Ansible Automation   │
                     │       Portal          │
                     │  (owns all data)      │
                     │  (clones repos)       │
                     └───────────┬───────────┘
                                 │
                    REST: POST /scan (tarball upload)
                    REST: GET /scan/{id}/events (SSE)
                    REST: GET /rules
                    REST: GET /health
                                 │
                     ┌───────────▼───────────┐
                     │   APME Gateway        │
                     │   (STATELESS proxy)   │
                     │   No DB, no PVC       │
                     │   REST :8080          │
                     └───────────┬───────────┘
                                 │
                          gRPC (internal)
                                 │
                     ┌───────────▼───────────┐
                     │   APME Engine Pod     │
                     │  Primary + Validators │
                     │  + Galaxy Proxy       │
                     └───────────────────────┘
```

## Required Changes

### A1: Gateway Authentication Middleware
**Priority:** Critical
**Effort:** Small

Add Bearer token validation to the Gateway REST API.

```python
# apme_gateway/middleware/auth.py
async def verify_service_token(request: Request):
    if os.getenv("APME_AUTH_DISABLED", "false").lower() == "true":
        return  # Skip auth (backward compat for CLI daemon)

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")

    token = auth[7:]
    valid_tokens = os.getenv("APME_SERVICE_TOKENS", "").split(",")
    if token not in valid_tokens:
        raise HTTPException(403, "Invalid service token")
```

**Files to modify:**
- `src/apme_gateway/app.py` — add middleware
- `deploy/helm/apme/values.yaml` — add auth config
- `deploy/helm/apme/templates/gateway-deployment.yaml` — inject secret

### A2: Gateway Refactoring to Stateless Proxy
**Priority:** High
**Effort:** Medium (1-2 sprints)

Remove all persistence from Gateway. This is a significant refactor.

**Remove:**
- SQLite database initialization, SQLAlchemy models, migrations
- gRPC Reporting servicer (port 50060) — Portal stores results directly
- All persistence-dependent REST endpoints:
  - Projects CRUD
  - Dashboard/summary/rankings
  - Activity/trend
  - Settings (Galaxy servers stored in Portal instead)
  - Notifications
  - Suppressions
- UI deployment (port 8081) — Portal is the UI

**Keep (stateless):**
- Health check endpoint (probes Primary + validators via gRPC)
- Rules endpoint (fetches from Primary, serves from in-memory cache)
- SSE event streaming (proxies gRPC SessionEvents to HTTP)
- Bearer token auth middleware (A1)

**Add:**
- POST /scan tarball endpoint (A3)

**Implementation approach:** Feature flag `APME_GATEWAY_MODE=proxy` (default: `full` for backward compat). When `proxy`:
- Skip database initialization
- Skip gRPC Reporting server startup
- Register only stateless endpoints
- Reduce container resource requirements

**Files to modify:**
- `src/apme_gateway/app.py` — conditional startup based on mode
- `src/apme_gateway/api/router.py` — register only proxy routes
- `deploy/helm/apme/values.yaml` — add `gateway.mode: proxy`

### A3: Tarball Scan Endpoint
**Priority:** High
**Effort:** Medium (1 sprint)

New endpoint that accepts a tarball of Ansible content from Portal and returns scan results.

**Endpoint:** `POST /scan`

```python
@app.post("/scan")
async def trigger_scan(
    content: UploadFile,                    # tar.gz of Ansible project
    options: str = Form("{}"),              # JSON: ansible_core_version, collection_specs
    galaxy_servers: str = Form("[]"),       # JSON: [{name, url, token, auth_url}]
    action: str = Form("check"),            # "check" or "remediate"
):
    # 1. Extract tarball to temp directory
    temp_dir = extract_tarball(content)

    # 2. Parse options
    scan_options = json.loads(options)
    servers = json.loads(galaxy_servers)

    # 3. Discover Ansible files
    files = discover_ansible_files(temp_dir)

    # 4. Configure Galaxy Proxy with credentials
    if servers:
        await configure_galaxy_proxy(servers)

    # 5. Send to Primary via gRPC FixSession
    scan_id = str(uuid.uuid4())
    result = await run_fix_session(
        scan_id=scan_id,
        files=files,
        options=scan_options,
        action=action,
    )

    # 6. Cleanup temp directory
    cleanup(temp_dir)

    # 7. Return full results
    return {
        "scan_id": scan_id,
        "violations": result.violations,
        "proposals": result.proposals,
        "patches": result.patches,
        "diagnostics": result.diagnostics,
        "summary": result.summary,
    }
```

**SSE Progress:** `GET /scan/{scan_id}/events`
- Proxies gRPC SessionEvents to HTTP Server-Sent Events
- Portal frontend connects to this for real-time progress display

**Approve Proposals:** `POST /scan/{scan_id}/approve`
- Forwards approval commands to Primary via gRPC
- Returns updated proposals + patches

**Files to create/modify:**
- `src/apme_gateway/api/scan_router.py` — new scan-specific routes
- `src/apme_gateway/services/tarball_service.py` — tarball extraction
- `src/apme_gateway/services/scan_proxy.py` — gRPC FixSession proxy

### A4: PVC Sizing for Scale
**Priority:** Medium
**Effort:** Small

Update Helm chart defaults for Portal-scale deployments.

```yaml
persistence:
  sessions:
    size: 50Gi        # Up from 10Gi
  proxyCache:
    size: 20Gi        # Up from 10Gi
  # gateway section REMOVED — no PVC needed in proxy mode
```

### A5: Air-Gapped Mode Flag
**Priority:** Medium
**Effort:** Small

```bash
APME_AIR_GAPPED=true
# Behavior: disable PyPI fallback, skip dep-audit, only serve cached wheels
```

### A6: Rule Catalog API (Stateless)
**Priority:** Medium
**Effort:** Small

GET /rules endpoint that returns all rules from Primary's in-memory catalog. Portal calls this on startup and periodically to sync its apme_rules table.

```python
@app.get("/rules")
async def list_rules():
    # Fetch from Primary via gRPC (in-memory, no DB)
    rules = await primary_client.list_rules()
    return {"rules": rules}
```

## API Surface: Before vs After

### Removed (handled by Portal backend plugin)

| Endpoint | Previous Purpose | New Owner |
|----------|-----------------|-----------|
| CRUD /projects | Project management | Portal apme_projects table |
| GET /dashboard/* | Aggregate metrics | Portal SQL queries |
| GET /activity/* | Scan history | Portal apme_scans table |
| GET /violations/top | Most violated rules | Portal SQL queries |
| GET /stats/* | Remediation rates | Portal SQL queries |
| CRUD /settings/galaxy-servers | Galaxy config | Portal apme_galaxy_servers table |
| POST /suppressions | Rule suppressions | Portal apme_rule_overrides table |
| GET /notifications/* | User notifications | Portal notification service |
| GET /projects/{id}/trend | Violation trends | Portal SQL queries |
| GET /projects/{id}/dependencies | SBOM/dependencies | Portal (stored after scan) |

### Kept (stateless proxy)

| Endpoint | Purpose |
|----------|---------|
| `POST /scan` | **NEW** — accept tarball, trigger scan, return results |
| `GET /scan/{id}/events` | SSE stream — proxy gRPC SessionEvents |
| `POST /scan/{id}/approve` | Forward proposal approvals to Primary |
| `POST /scan/{id}/cancel` | Cancel running scan |
| `GET /rules` | List registered rules (from Primary, in-memory) |
| `GET /health` | Health check (probe Primary + validators) |

## Deployment Changes

### Helm Chart (proxy mode)

```yaml
gateway:
  mode: proxy                      # NEW: "proxy" (stateless) or "full" (with DB)
  replicas: 2                      # Stateless — scale freely
  resources:
    requests: { cpu: 250m, memory: 256Mi }
    limits: { cpu: 500m, memory: 512Mi }
  auth:
    enabled: true
    serviceTokenSecret: apme-service-token

# Remove from templates:
# - gateway-pvc.yaml
# - gateway-reporting-service.yaml (gRPC :50060)
# - ui-deployment.yaml
# - ui-service.yaml
```

### bootc (no Gateway DB volume)

The APME container in bootc mode only needs:
- `/sessions` volume — venv session storage
- `/cache` volume — Galaxy Proxy wheel cache
- No Gateway DB volume

### A9: API Versioning Contract
**Priority:** High
**Effort:** Medium

Implement RFC 9745 (Deprecation header) and RFC 8594 (Sunset header) as FastAPI middleware. Portal and other consumers must be able to detect deprecated endpoints programmatically.

Reference: [ansible/apme#351](https://github.com/ansible/apme/pull/351) Section 4.4.

### A10: Observability (Prometheus + Structured Logging)
**Priority:** High
**Effort:** Medium

Add `/metrics` Prometheus endpoint to Gateway. Expose: scan count, scan duration histogram, active scans gauge, validator timing, cache hit rates. Add structured JSON logging for production log aggregation.

### A11: Rate Limiting
**Priority:** Medium
**Effort:** Small

Add request throttling to Gateway REST API to prevent abuse when exposed via Portal. Configurable via env var (e.g., `APME_RATE_LIMIT=100/minute`).

### A12: Non-Root Containers
**Priority:** High
**Effort:** Small

Add `USER` directive to all Containerfiles. Currently documented in SECURITY.md but not enforced in images.

## Implementation Order

1. **A1: Gateway auth** — security prerequisite, small effort
2. **A3: Tarball scan endpoint** — enables Portal to send content
3. **A12: Non-root containers** — security hardening
4. **A2: Gateway stateless refactor** — behind `APME_GATEWAY_MODE=proxy` flag
5. **A9: API versioning contract** — consumer stability
6. **A10: Observability** — production monitoring
7. **A4: PVC sizing** — operational requirement
8. **A5: Air-gapped mode** — deployment requirement
9. **A6: Rule catalog API** — needed for Portal admin UI
10. **A11: Rate limiting** — abuse protection

## Galaxy / Automation Hub Credential Flow

APME needs Automation Hub credentials to download collections at scan time. In Portal-integrated mode, these credentials are **never stored in APME** — they are passed per-request from Portal.

```
Portal (owns credentials)                    APME (stateless)
┌─────────────────────────┐                 ┌─────────────────────────┐
│ apme_galaxy_servers     │                 │ Gateway (no DB)         │
│ table in PostgreSQL:    │                 │                         │
│                         │  POST /scan     │                         │
│ - name: certified       │  galaxy_servers │  Forwards to Galaxy     │
│   url: https://hub...   │  ────────────►  │  Proxy via POST         │
│   token: ***            │  [{name, url,   │  /admin/galaxy-config   │
│   auth_url: https://sso │    token,       │                         │
│                         │    auth_url}]   │  Galaxy Proxy writes    │
│ - name: community       │                 │  temp ansible.cfg with  │
│   url: https://galaxy.. │                 │  credentials, downloads │
│   token: (none)         │                 │  collections, then      │
│                         │                 │  deletes temp file      │
└─────────────────────────┘                 └─────────────────────────┘

Sources of Galaxy credentials in Portal:
1. Existing Portal config: `ansible.rhaap.baseUrl` + `ansible.rhaap.token` (AAP admin token, org-scoped — NOT the logged-in user's token). In AAP 2.5+, Hub is behind the AAP Gateway, so `rhaap.baseUrl` serves as the Hub endpoint. If `ansible.automationHub.baseUrl` is explicitly configured, it takes precedence. No separate APME galaxy config needed.
2. Optional additional servers → apme_galaxy_servers table (for orgs with multiple private Galaxy sources)
3. Default community Galaxy → galaxy.ansible.com (no token, added automatically as fallback)

Note: The AAP admin token is configured in Portal's app-config.yaml
(backed by K8s Secret), not derived from the logged-in user's session.
Collection access is an organizational capability, not per-user.
```

**Key points:**
- Portal stores Galaxy server configs in `apme_galaxy_servers` table (name, url, has_token flag)
- Actual tokens stored in Kubernetes Secrets (referenced by name), not in DB
- Portal injects tokens into each scan request as `galaxy_servers` array
- APME Gateway forwards credentials to Galaxy Proxy per-scan
- Galaxy Proxy writes a temp `ansible.cfg` with credentials, uses `ansible-galaxy collection download`, then deletes the temp file
- Credentials are never persisted by APME — fully ephemeral per-scan

## Git-Based Collection Dependencies

APME's Galaxy Proxy downloads collections via `ansible-galaxy collection download` (Galaxy API). It does not currently support cloning collections from Git repos.

**Phase 1:** Collections must be published to Galaxy or Automation Hub. Git-based entries (`type: git` in `requirements.yml`) are not supported in Portal scans. This aligns with the AAP 2.5+ model where Automation Hub is the collection distribution point.

**Phase 2 (if needed):** Portal can pass the admin SCM token alongside Galaxy credentials in the scan request. APME Gateway would set it as `GH_TOKEN` env var for `ansible-galaxy` when git-based collection sources are detected. Same ephemeral credential model as Galaxy tokens — never persisted.

**Alternative:** Portal pre-builds git-based collections (clone → `ansible-galaxy collection build` → include tarball in scan upload). APME stays fully credential-free.

## Future: Custom Rules via REST (Phase 2)

Customers maintain custom OPA/Rego policies in a git repository (policy-as-code). Portal clones the policy repo, reads `.rego` files, and passes them to APME at scan time via the `POST /scan` endpoint.

**APME changes needed (Phase 2):**
- `POST /scan` accepts optional `custom_policies` field: `[{name, rule_id, rego_source}]`
- Gateway writes custom policies to a temp directory alongside the built-in OPA bundle
- OPA validator loads merged bundle (built-in + custom)
- Custom rule IDs use `P` prefix (P100+) — distinguished from built-in P001-P004
- Temp policies cleaned up after scan

**No changes needed for Phase 1:** Per-project `.apme/rules.yml` and admin rule overrides (severity, enable/disable, enforced) are already supported via `rule_configs` in `ScanOptions`.

**Why policies live in git, not uploaded via UI:**
- Policies are code — they need version control, code review, and CI validation
- Git history provides full audit trail; rollback is `git revert`
- Teams can collaborate via PRs before policy changes take effect
- Portal admin UI only configures the policy repo URL — no editing/uploading of policy content

## What Stays Unchanged

- **CLI daemon mode** (`apme daemon start`) — no Gateway, no changes needed
- **Primary gRPC service** — unchanged, still accepts FixSession streams
- **All validators** — unchanged, read-only scanning
- **Galaxy Proxy** — unchanged, still PEP 503 wheel caching
- **Engine pod architecture** — unchanged, scale pods not services (ADR-012)
- **Remediation engine** — unchanged, 3-tier model
- **Rule ID conventions** — unchanged (ADR-008)
