# APME Changes Required for Portal Integration

**Date:** 2026-06-25
**Status:** Research / Design Proposal
**Tracking:** ANSTRAT-2222

## Overview

This document outlines the changes required in APME to support integration with the Ansible Automation Portal.

### Core Architecture: Shared DB Instance, Separate Ownership

1. **APME keeps its database** — Gateway maintains apme\_\* tables, stores scan results, rules, projects
2. **Shared PostgreSQL instance** — APME connects to Portal's PostgreSQL; each manages its own tables
3. **Portal is a thin proxy client** — RBAC + credential injection + forward to APME API; no APME data layer in Portal
4. **Optional credentials per-request** — Portal injects tokens if available; APME falls back to own settings
5. **APME works standalone AND integrated** — same API, same codebase, no special modes
6. **Day 0/Day 2 management APIs** — APME exposes endpoints for migrations, health, cleanup; Portal orchestrates

### Why This Design Over Alternatives

| Alternative | Problem | This Design |
| --- | --- | --- |
| **Option A: Separate APME DB** | Two databases for operators — double provisioning, backup, HA, monitoring | Single PostgreSQL instance; Portal manages instance, APME manages its tables |
| **Option B: Portal owns data (stateless Gateway)** | Portal must understand APME's data model; Knex migrations for tables Portal doesn't own; breaks standalone; requires gutting Gateway | APME manages its own schema; Portal is thin proxy; APME works standalone and integrated |

**APME is not yet released** — no migration tooling debt or backward compatibility concerns. PostgreSQL support will be implemented cleanly from the start with Alembic for schema versioning.

## C4 Context

```
                     ┌───────────────────────┐
                     │  Ansible Automation   │
                     │       Portal          │
                     │  (thin proxy client)  │
                     └───────────┬───────────┘
                                 │
                    REST API (with optional credentials)
                                 │
                     ┌───────────▼───────────┐
                     │   APME Gateway        │
                     │   Full service        │
                     │   Owns apme_* tables  │
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
                                 │
                     ┌───────────▼───────────┐
                     │  Shared PostgreSQL    │
                     │  backstage_* (Portal) │
                     │  apme_* (APME)        │
                     └───────────────────────┘
```

## Required Changes

### A1: Gateway Authentication Middleware

**Priority:** Critical
**Effort:** Small

```python
async def verify_service_token(request: Request):
    if os.getenv("APME_AUTH_DISABLED", "false").lower() == "true":
        return  # Skip for standalone/CLI mode

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Missing service token")

    token = auth[7:]
    valid_tokens = os.getenv("APME_SERVICE_TOKENS", "").split(",")
    if token not in valid_tokens:
        raise HTTPException(403, "Invalid service token")
```

**Files to modify:**

- `src/apme_gateway/app.py` — add middleware
- `deploy/helm/apme/values.yaml` — add auth config

### A2: PostgreSQL Support + Alembic Migrations

**Priority:** Critical
**Effort:** Medium (1-2 sprints)

Add PostgreSQL as a database backend alongside SQLite. APME is not yet released, so this is a clean implementation — no backward compatibility concerns.

**Required:**

- Add `asyncpg` dependency for async PostgreSQL
- Configure via `APME_DATABASE_URL` environment variable:
  - `postgresql://user:pass@portal-db:5432/apme` for Portal integration
  - `sqlite:///path/to/apme.db` for standalone mode (default)
- Implement Alembic migrations for all apme\_\* tables
- Connection pooling configuration (size limits to share PostgreSQL safely)
- SQLAlchemy already abstracts dialects — the switch is straightforward

**Helm values:**

```yaml
gateway:
  database:
    url: "" # Set to PostgreSQL URI when deployed with Portal
    # Empty = SQLite (standalone mode)
    poolSize: 10
    maxOverflow: 5
```

### A3: Optional Credential Acceptance in API

**Priority:** High
**Effort:** Small-Medium

All scan/operation endpoints accept optional credentials. If provided by Portal, use them. If not, fall back to APME's own configured settings.

```python
class OperateRequest(BaseModel):
    action: Literal["check", "remediate"]
    scm_token: Optional[str] = None       # Optional — Portal provides
    galaxy_servers: Optional[list] = None  # Optional — Portal provides
    options: Optional[dict] = None

# Resolution logic:
# 1. If request has scm_token → use it
# 2. Else if project has stored scm_token → use it
# 3. Else → error: no credentials

# 1. If request has galaxy_servers → use them
# 2. Else if APME has configured galaxy servers → use them
# 3. Else → community Galaxy only
```

This means the same API works for:

- **Portal-integrated:** Portal injects AAP admin token + user's SCM token per-request
- **Standalone:** APME uses its own configured Galaxy servers + project SCM settings
- **CI/CD:** Caller passes tokens explicitly

### A4: Day 0 / Day 2 Management APIs

**Priority:** High
**Effort:** Medium (1 sprint)

Portal manages the PostgreSQL instance but doesn't know APME's schema. APME exposes management endpoints:

```
Day 0 (Initial Setup / Upgrades):
  POST /api/v1/admin/db/migrate
    → APME runs Alembic migrations on apme_* tables
    → Returns: { applied: ["001_initial", "002_rules"], current: "002" }

  Portal Helm post-install hook:
    kubectl exec apme-gateway -- curl -X POST localhost:8080/api/v1/admin/db/migrate

Day 2 (Operations):
  GET  /api/v1/admin/db/status
    → { version: "002", tables: 9, size_mb: 145, needs_migration: false }

  GET  /api/v1/admin/db/health
    → { connected: true, latency_ms: 2, pool: { active: 3, idle: 7 } }

  POST /api/v1/admin/db/vacuum
    → Cleanup old scans based on retention policy
```

Protected by service token (admin-only).

### A5: PVC Sizing for Scale

**Priority:** Medium
**Effort:** Small

```yaml
persistence:
  sessions:
    size: 50Gi # Up from 10Gi
  proxyCache:
    size: 20Gi # Up from 10Gi
  # No Gateway DB PVC when using shared PostgreSQL
```

### A6: Air-Gapped Mode Flag

**Priority:** Medium
**Effort:** Small

`APME_AIR_GAPPED=true` — disable PyPI fallback, skip dep-audit, only serve cached wheels.

### A9: API Versioning Contract

**Priority:** High
**Effort:** Medium

Implement RFC 9745 (Deprecation header) and RFC 8594 (Sunset header) as FastAPI middleware. Publish versioned OpenAPI spec as release artifact.

Reference: [ansible/apme#351](https://github.com/ansible/apme/pull/351) Section 4.4.

### A10: Observability

**Priority:** High
**Effort:** Medium

Add `/metrics` Prometheus endpoint to Gateway. Structured JSON logging for production.

### A11: Rate Limiting

**Priority:** Medium
**Effort:** Small

Request throttling on Gateway REST API. Configurable via `APME_RATE_LIMIT`.

### A12: Non-Root Containers

**Priority:** High
**Effort:** Small

Add `USER` directive to all Containerfiles.

## Galaxy / Automation Hub Credential Flow

APME needs Automation Hub credentials to download collections at scan time. Credentials can come from two sources:

```
Portal (optional per-request)             APME (own settings)
┌─────────────────────────┐              ┌─────────────────────────┐
│ ansible.rhaap.baseUrl   │              │ APME gateway Galaxy     │
│ ansible.rhaap.token     │              │ server config           │
│ (existing Portal config)│              │ (apme_galaxy_servers    │
│                         │  POST /scan  │  table or env vars)     │
│ Injected as optional    │  ──────────► │                         │
│ galaxy_servers in       │  galaxy_     │ If request has tokens   │
│ scan request            │  servers:    │ → use them              │
│                         │  [{...}]     │ If not → use own config │
└─────────────────────────┘              └─────────────────────────┘

In AAP 2.5+, Hub is behind AAP Gateway:
  ansible.rhaap.baseUrl serves as Hub endpoint
  ansible.automationHub.baseUrl is optional override (pre-2.5)
  ansible.rhaap.token (AAP admin token) works for Hub access
```

## Git-Based Collection Dependencies

APME's Galaxy Proxy downloads collections via `ansible-galaxy collection download` (Galaxy API). It does not currently support cloning collections from Git repos.

**Phase 1:** Collections must be published to Galaxy or Automation Hub. This aligns with the AAP 2.5+ model.

**Phase 2 (if needed):** Portal can pass admin SCM token alongside Galaxy credentials. APME sets `GH_TOKEN` for `ansible-galaxy` when git-based collection sources are detected. Same ephemeral credential model.

**Alternative:** Portal pre-builds git-based collections (clone → `ansible-galaxy collection build` → include tarball in scan upload). APME stays credential-free for SCM.

## Future: Custom Rules via REST (Phase 2)

Custom OPA/Rego policies live in a git repository (policy-as-code). Portal clones the policy repo, reads `.rego` files, and passes them to APME at scan time via `POST /scan`.

**APME changes needed (Phase 2):**

- `POST /scan` accepts optional `custom_policies` field: `[{name, rule_id, rego_source}]`
- Gateway writes custom policies to a temp directory alongside built-in OPA bundle
- OPA validator loads merged bundle (built-in + custom)
- Custom rule IDs use `P` prefix (P100+)
- Temp policies cleaned up after scan

## API Surface — No Changes Needed

APME Gateway's existing REST API already covers all Portal needs:

| Endpoint Group | Portal Need | Current State |
| --- | --- | --- |
| Projects CRUD | Manage scanned repos | Ready |
| Operations (scan trigger) | Trigger scans with optional credentials | Needs A3 (optional credential acceptance) |
| Operations (SSE progress) | Real-time scan progress | Ready |
| Operations (approve) | Approve AI proposals | Ready |
| Operations (create PR) | Open PR with fixes | Ready (needs optional scm\_token) |
| Violations query | List/filter violations | Ready |
| Trend analysis | Violation trends over time | Ready |
| Dashboard summary | Aggregate metrics | Ready |
| Rule catalog | List/configure rules | Ready |
| Galaxy server config | Manage Galaxy credentials | Ready |
| Health check | Monitor APME availability | Ready |
| Dependencies/SBOM | Collection + package info | Ready |
| **Admin/DB management** | Day 0/Day 2 operations | **Needs A4** |

**Overall:** ~90% of the API surface is ready. Key gaps are PostgreSQL support (A2), optional credentials (A3), and management APIs (A4).

## Implementation Order

1. **A1: Gateway auth** — security prerequisite
2. **A2: PostgreSQL + Alembic** — enables shared instance architecture
3. **A3: Optional credentials** — enables Portal credential injection
4. **A4: Day 0/Day 2 management APIs** — enables Portal to orchestrate APME DB
5. **A12: Non-root containers** — security hardening
6. **A9: API versioning contract** — consumer stability
7. **A10: Observability** — production monitoring
8. **A5: PVC sizing** — operational requirement
9. **A6: Air-gapped mode** — deployment requirement
10. **A11: Rate limiting** — abuse protection

## What Stays Unchanged

- **CLI daemon mode** (`apme daemon start`) — uses SQLite, no Gateway changes needed
- **Primary gRPC service** — unchanged
- **All validators** — unchanged, read-only scanning
- **Galaxy Proxy** — unchanged, PEP 503 wheel caching
- **Engine pod architecture** — unchanged, scale pods not services (ADR-012)
- **Remediation engine** — unchanged, 3-tier model
- **Rule ID conventions** — unchanged (ADR-008)
- **Gateway REST API** — all existing endpoints preserved; new endpoints added (admin/db, optional credentials)
