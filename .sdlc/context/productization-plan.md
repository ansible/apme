# APME Productization Plan — Pre-Read

**Prepared:** 2026-06-24  
**Updated:** 2026-06-25 (Portal integration architecture aligned with [PR #352](https://github.com/ansible/apme/pull/352) v2)  
**Status:** Draft for review

This document captures the productization strategy for APME across two deployment tracks: **Portal-integrated product** (Track A) and **upstream standalone** (Track B). It consolidates architecture, authentication, work streams, open decisions, PostgreSQL-only persistence (SQLite removed), and Portal integration requirements.

Companion research: [PR #352](https://github.com/ansible/apme/pull/352) (`.sdlc/research/portal-integration/00-integration-requirements.md`), [ansible-rhdh-plugins PR #676](https://github.com/ansible/ansible-rhdh-plugins/pull/676), [ANSTRAT-2222](https://issues.redhat.com/browse/ANSTRAT-2222).

---

## Section 1 — Architecture Overview

APME is a multi-service system that automates policy enforcement and modernization of Ansible content for AAP 2.5+. Services communicate via gRPC (ADR-001); the engine is stateless; persistence lives at the Gateway edge (ADR-020, ADR-029).

### Three-Tier Topology

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           APME Engine Tier                                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │ Primary  │ │ Native   │ │ OPA      │ │ Ansible  │ │ Gitleaks │ (optional)│
│  │ :50051   │ │ :50055   │ │ :50054   │ │ :50053   │ │ :50056   │           │
│  └────┬─────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘           │
│       │  ┌──────────────────┐ ┌──────────────────┐ ┌──────────┐               │
│       │  │ Collection Health│ │ Dep Audit        │ │ Abbenay  │               │
│       │  │ :50058 (required)│ │ :50059 (required)│ │ :50057   │               │
│       │  └──────────────────┘ └──────────────────┘ └──────────┘               │
│  ┌────┴─────────────────────────────────────┐                                │
│  │ Galaxy Proxy :8765 (PEP 503)             │                                │
│  └──────────────────────────────────────────┘                                │
└──────────────────────────────────────────────────────────────────────────────┘
                                     │ gRPC
                                     ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                           Gateway Tier                                        │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │ Gateway :8080 (REST) / :50060 (gRPC Reporting)                         │  │
│  │ PostgreSQL only — bundled or external (shared instance in Track A)   │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────┘
                                     │ REST + WebSocket
                                     ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                           UI Tier (Track B only)                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │ UI :8081 (nginx SPA) — REST + WebSocket /api/v1/ws/session             │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Tier Summary

| Tier | Components | Notes |
|------|------------|-------|
| **Engine** | Primary, Native, OPA, Ansible, Galaxy Proxy, **Collection Health**, **Dep Audit**, Abbenay; Gitleaks optional | Collection Health and Dep Audit are **required** for productized engine runtime. Gitleaks is **optional** (external binary). |
| **Gateway** | REST API, Reporting gRPC, PostgreSQL persistence | **Same full Gateway** on both tracks. **PostgreSQL only** (bundled or external). Track A connects to **Portal's shared PostgreSQL instance**; APME owns `apme_*` tables, Portal owns `backstage_*`. |
| **UI** | React SPA (PatternFly) | **Track B:** standalone UI via REST and WebSocket `/api/v1/ws/session`. **Track A:** no APME UI — Portal provides UX. |

### Engine-Core vs Product Validator Scope

| Service | Product / Podman | CLI daemon today | CI publish today |
|---------|------------------|------------------|------------------|
| Primary, Native, OPA, Ansible, Galaxy Proxy | Required | Required | ✓ |
| Collection Health | Required | Optional (`include_optional`) | Local build only |
| Dep Audit | Required | Optional (`include_optional`) | Local build only |
| Gitleaks | Optional | Optional | ✓ |
| Abbenay | Pod-level (AI) | Not in daemon | External image |

### Implementation Gap

`launcher.py` still lists `collection_health` and `dep_audit` in `_OPTIONAL_SERVICES` alongside `gitleaks`. They start only when `include_optional=True`. **Productization work stream 4.10** must align launcher defaults, Helm values, Podman pod spec, bootc image, and CI with the required-validator scope.

```33:37:src/apme_engine/daemon/launcher.py
_OPTIONAL_SERVICES = {
    "gitleaks": 50056,
    "collection_health": 50058,
    "dep_audit": 50059,
}
```

---

## Section 2 — Dual Deployment Tracks

APME ships on two parallel tracks with different packaging, persistence, and UX surfaces.

### Track A — Product (Portal)

| Attribute | Value |
|-----------|-------|
| **Distribution** | APME as **Portal Helm subchart** or bundled in Portal bootc image (single install/upgrade with Portal) |
| **Gateway** | **Full Gateway service** — same codebase and REST API as Track B; not a stateless proxy |
| **Database** | **Shared PostgreSQL instance** with Portal — APME owns `apme_*` tables (Alembic); Portal owns `backstage_*`; Portal manages the instance, APME manages its schema |
| **UI** | Portal UX only; APME standalone UI container **not deployed** |
| **Portal role** | **Thin proxy client** — RBAC, optional credential injection, forward to APME API; no APME data layer in Portal |
| **Auth** | Portal Auth/RBAC (user-facing) + Bearer service token (Portal backend → Gateway) |
| **Credentials** | **Optional per-request** — Portal injects SCM/Galaxy tokens when available; APME falls back to its own configured settings (A3) |
| **Target users** | AAP customers consuming APME via Ansible Automation Portal |

### Track B — Upstream / Dev

| Attribute | Value |
|-----------|-------|
| **Distribution** | Standalone Helm (`deploy/helm/apme/`), Podman pod (`tox -e up`), bootc VM, CLI daemon; same PostgreSQL requirement for downstream/production (Konflux, Red Hat catalog) |
| **Gateway** | **Full Gateway service** — REST, WebSocket, Reporting gRPC, persistence |
| **Database** | **PostgreSQL only** — bundled container or external URL; SQLite removed entirely |
| **UI** | Standalone APME UI (:8081) |
| **Auth** | Bearer tokens per ADR-038 (Proposed) |
| **Target users** | Open-source consumers, integrators, developers |

**One codebase, one API:** Track A and Track B use the same Gateway — no `full` vs `proxy` mode split. Difference is packaging, DB connection target (shared Portal instance vs bundled/external), and which UI is deployed.

### Open Packaging Decision Table

| Decision | Option A | Option B | Option C | Status |
|----------|----------|----------|----------|--------|
| **Image registry** | `ghcr.io` (current CI) | Red Hat catalog | Customer mirror | **Open** |
| **Chart packaging** | OCI artifact standalone | Subchart of Portal Operator | Helm only, no Operator | **Open** |
| **Version coupling** | Lockstep with Portal release | Independent semver | LTS branches | **Open** |
| **CLI distribution** | PyPI + `apme-cli` container | RPM in bootc only | Bundled in Portal image | **Open** (CLI packaging gap — see 4.1) |
| **Engine image layout** | Per-service images (current) | Single fat engine image | Hybrid | **Open** |
| **Abbenay sourcing** | External `ghcr.io/redhat-developer/abbenay` | Vendor in product chart | Optional feature flag | **Open** |

---

## Section 3 — Authentication

Authentication differs by deployment track. **Podman, bootc, and CLI daemon paths need token-based auth**, not AAP-header-only or auth-disabled defaults suitable only for local dev.

### Track A — Portal Product

| Layer | Mechanism |
|-------|-----------|
| **User → Portal** | Portal Auth (OIDC/SSO) + Portal RBAC |
| **Portal → Gateway** | Bearer service token (`APME_SERVICE_TOKENS`); Portal backend injects from K8s Secret |
| **Gateway → Engine** | Pod-local gRPC; no end-user identity in engine |
| **Galaxy credentials** | Optional per-request on operations (A3); APME falls back to own Galaxy config |

**A1 (PR #352):** Gateway auth middleware with `APME_AUTH_DISABLED` escape hatch for backward-compatible CLI daemon dev only.

### Track B — Upstream / Dev

| Layer | Mechanism |
|-------|-----------|
| **User → Gateway/UI** | Bearer token (ADR-038 — status: Proposed) |
| **CLI → Primary** | Local daemon trust boundary today; production needs bearer or mTLS |
| **Machine consumers** | ADR-038 pull model: `Authorization: Bearer` on `/api/v1/*` |
| **Gateway → Engine** | Pod-local gRPC inside engine pod |

### Gaps

| Gap | Impact | Resolution |
|-----|--------|------------|
| ADR-038 not fully implemented | No stable machine-auth contract | Phase 1 auth ADR + middleware |
| Podman/bootc Helm values lack auth secrets | Open deployments | Document required `APME_SERVICE_TOKENS` |
| CLI daemon has no Gateway auth today | `apme sbom` etc. need Gateway (ADR-049) | Separate from Portal track — see ADR-049 |
| Portal proxy service-token rotation | Ops burden | ADR for token lifecycle (D-01) |

---

## Section 4 — Productization Work Streams

### 4.1 — Container Images & CI

**Current state — CI (`container-images.yml`):** publishes **10 images** to GHCR:

1. `apme-base`
2. `apme-primary`
3. `apme-native`
4. `apme-opa`
5. `apme-ansible`
6. `apme-galaxy-proxy`
7. `apme-gitleaks`
8. `apme-gateway`
9. `apme-cli`
10. `apme-ui`

**Local Podman build (`tox -e build`):** additionally builds `apme-collection-health` and `apme-dep-audit` (11 application images + base). Abbenay is pulled from `ghcr.io/redhat-developer/abbenay`.

**Target state — CI:** **12 service images** in the publish matrix by adding:

- `apme-collection-health` (`containers/collection-health/Dockerfile`)
- `apme-dep-audit` (`containers/dep-audit/Dockerfile`)

| Image | Track A | Track B | CI today | CI target |
|-------|---------|---------|----------|-----------|
| primary | ✓ | ✓ | ✓ | ✓ |
| native | ✓ | ✓ | ✓ | ✓ |
| opa | ✓ | ✓ | ✓ | ✓ |
| ansible | ✓ | ✓ | ✓ | ✓ |
| galaxy-proxy | ✓ | ✓ | ✓ | ✓ |
| collection-health | ✓ | ✓ | ✗ | ✓ |
| dep-audit | ✓ | ✓ | ✗ | ✓ |
| gitleaks | opt | opt | ✓ | opt |
| gateway | ✓ | ✓ | ✓ | ✓ |
| ui | ✗ | ✓ | ✓ | ✓ (Track B only) |
| cli | ✓ | ✓ | ✓ | ✓ |
| abbenay | ✓ | ✓ | external | external |

**CLI packaging gap:** PyPI package `apme-engine` and `apme-cli` container exist, but there is no productized RPM/bootc artifact path aligned with Portal Operator delivery. Track A needs a defined CLI artifact policy (packaging table, Section 2).

### 4.2 — Security Hardening

| Item | Status | Action |
|------|--------|--------|
| Non-root containers (`USER` in Dockerfiles) | **Missing** — no `USER` directive in any `containers/*/Dockerfile` | A12 / Phase 2 — add non-root user to all images |
| Secret redaction in logs | Partial | Audit all services for `[REDACTED]` compliance |
| SBOM generation | CI | Extend to collection-health and dep-audit images |
| Image signing | Open | Cosign/sigstore on release tags |
| Dependency scanning | CI | `tox -e security` / lean-ci gates |
| Network policies (K8s) | Helm | Document NetworkPolicy for Track B |
| Rate limiting | Not implemented | A11 — `APME_RATE_LIMIT` on Gateway |

### 4.3 — Authentication

- **Track A:** Implement A1 service-token middleware; Helm secret injection; disable auth only via explicit `APME_AUTH_DISABLED` for dev.
- **Track B:** Implement ADR-038 bearer validation for `/api/v1/*` machine consumers.
- **Deliverables:** ADR for Portal service-token model (D-01); ADR-038 implementation tasks.

### 4.4 — API Versioning

- Adopt [RFC 9745](https://www.rfc-editor.org/rfc/rfc9745.html) (Deprecation header) and [RFC 8594](https://www.rfc-editor.org/rfc/rfc8594.html) (Sunset header) as FastAPI middleware on Gateway REST.
- Version prefix: `/api/v1/` (current); sunset headers on breaking changes.
- WebSocket session endpoint `/api/v1/ws/session` versioned with REST (Track B).
- Versioning applies to full Gateway REST + WebSocket surface used by Portal and Track B.
- **Deliverable:** API contract document; cross-ref PR #351 / this plan §4.4.

### 4.5 — PostgreSQL (SQLite Removal)

**Decision:** All deployments where the APME Gateway persists data use **PostgreSQL only** — upstream/dev (Track B), downstream/production (Konflux, customer Helm, bootc), and future CLI daemon Gateway (ADR-049). **SQLite is removed entirely** — no dev exception, no dual-mode fallback. See Section 9.

| Item | Requirement |
|------|-------------|
| **Alembic** | Required — schema migrations versioned in repo |
| **Multi-replica Gateway** | Enabled with PostgreSQL (remove SQLite single-replica guard) |
| **Connection pooling** | SQLAlchemy async + pool config in Helm |
| **Bundled vs external PG** | Config file / Helm values: bundled PostgreSQL container **or** external `APME_DATABASE_URL` |
| **Track A shared instance** | APME connects to Portal PostgreSQL; owns `apme_*` only; connection pooling required |
| **Current code gap** | `GatewayConfig.db_path` / `APME_DB_PATH` / `aiosqlite` must be removed — `APME_DATABASE_URL` + `asyncpg` required |

### 4.6 — Ownership Model

**Status: PROPOSED — not decided**

| Role | Proposed owner | Notes |
|------|----------------|-------|
| Engine validators | APME platform engineering | |
| Gateway/API | APME platform engineering | |
| Portal integration | Portal team (ansible-rhdh-plugins) | ANSTRAT-2222 |
| UI (Track B) | APME community / platform | Retained for upstream |
| Helm chart / Operator subchart | Integration / release engineering | |
| Security response | Shared on-call | |
| Custom rules (Phase 2) | Joint — Portal git policy + APME OPA merge | ADR required |

Requires governance ADR or RACI in `.sdlc/context/`.

### 4.7 — Dependency Management

- Pin production dependencies in `pyproject.toml` / lockfile.
- Vendored ARI engine (ADR-003) — no pip drift.
- Galaxy Proxy collection versions pinned per release.
- Document upgrade policy for Ansible-core compatibility.
- Air-gapped mode (A6): disable PyPI fallback, optional dep-audit skip — **proposed**, requires ADR.

### 4.8 — Repository Hardening

- Branch protection; required `tox -e lint` + `tox -e unit` on PRs.
- Signed commits encouraged; provenance attestations on container images.
- `.github/workflows/` review per lean-ci skill.
- Secret scanning (Gitleaks in CI meta).
- prek hooks: ruff, mypy, pydoclint on commit.

### 4.9 — Craig Requirements (P0 Alignment)

Portal stakeholder requirements traced to PR #352 / ANSTRAT-2222:

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| CR-1 | Shared PostgreSQL instance; APME owns `apme_*` schema | P0 | Spec §8 A2 |
| CR-2 | Optional credentials with APME settings fallback | P0 | Spec §8 A3 |
| CR-3 | Optional Galaxy credentials per-request (A3) | P0 | Spec §8 |
| CR-4 | Git-backed collections (phase 1: Hub only) | P0 | Spec §8 |
| CR-5 | Service-token auth on Gateway | P0 | Spec §8 A1 |
| CR-6 | Validator scope (CH + DA required) | P0 | **Decided** §4.10 |
| CR-7 | Non-root containers | P0 | Spec §8 A12 |
| CR-8 | Observability `/metrics` | P1 | Spec §8 A10 |

Full Craig P0 scope sign-off remains **open** (D-06).

### 4.10 — Validator Scope Alignment Checklist

Align code, deployment manifests, and docs with **Collection Health + Dep Audit required; Gitleaks optional**.

- [ ] Remove `collection_health` and `dep_audit` from `_OPTIONAL_SERVICES` in `launcher.py` (or promote to `_DEFAULT_PORTS` / required set)
- [ ] Update CLI daemon default: start CH + DA without `include_optional`
- [ ] Helm chart: CH + DA enabled by default; Gitleaks behind `optional.enabled`
- [ ] Podman `pod.yaml`: CH + DA already present — verify Primary env vars always set
- [ ] bootc image: include CH + DA units
- [ ] CI: add `collection-health` and `dep-audit` to `container-images.yml` matrix
- [ ] Health-check aggregation in Primary includes CH + DA endpoints
- [ ] `apme health-check` reports CH + DA as required services
- [ ] Documentation: `architecture.md`, `deployment.md`, `CLAUDE.md` invariant 12
- [ ] ADR-012 note: engine pod replicates as unit including CH + DA

---

## Section 5 — Additional Considerations

### ADR-042 — Third-Party Plugin Services

Custom/organization-specific rules ship via the **Plugin service** container (ADR-042), not volume-mounted rules into built-in validators (invariant 14).

- Portal Phase 2 custom rules (PR #352) may use **ephemeral Rego merge** at scan time — **proposed**; requires ADR distinct from ADR-042 plugin sidecar.
- Built-in validator bundles remain closed; Plugin sidecar is optional enterprise path.
- Plan Plugin image in CI when Portal custom-rules phase 2 lands.

### ADR-049 — Gateway Embedded in Local Daemon

ADR-049 status: **Accepted** (2026-04-01). **Implementation: not done** — `launcher.py` has no Gateway HTTP/gRPC startup; Gateway remains pod-level only.

- `apme sbom` and future REST-backed CLI commands require Gateway reachability.
- **Separate work stream from Portal Track A** — uses PostgreSQL when implemented.
- Deliverable: embed FastAPI + ReportingServicer + PostgreSQL in daemon per ADR-049 (bundled or external PG — no SQLite).

### Configuration (Environment / Helm Values)

| Setting | Purpose | Values |
|---------|---------|--------|
| `APME_DATABASE_URL` | PostgreSQL DSN (**required**) | `postgresql+asyncpg://...` |
| `APME_DATABASE_MODE` | DB topology | `bundled`, `external`, `shared` (Track A Portal instance) |
| `gateway.database.poolSize` | Connection pool | e.g. `10` (important when sharing Portal PG) |
| `APME_DB_PATH` | *(removed)* | **Dropped** — SQLite no longer supported |
| `APME_AUTH_DISABLED` | Skip Gateway auth | `true` dev only — not for production |
| `APME_SERVICE_TOKENS` | Portal→Gateway auth | Comma-separated bearer tokens (A1) |
| `APME_AIR_GAPPED` | Air-gapped deployments | `true` / `false` (A6, proposed) |
| `APME_RATE_LIMIT` | Gateway throttling | e.g. `100/minute` (A11, proposed) |

No `APME_GATEWAY_MODE` — same full Gateway on both tracks.

---

## Section 6 — Decision Matrix

### Open Decisions

| ID | Topic | Options | Blocker for |
|----|-------|---------|-------------|
| D-01 | **Auth model (Portal)** | Service token list vs mTLS vs OAuth client credentials | Track A GA |
| D-02 | **Packaging** | See Section 2 table | Release pipeline |
| D-03 | **Shared PG pooling** | Pool size / max connections when APME shares Portal instance | Track A stability |
| D-04 | **PostgreSQL topology** | Bundled subchart vs external RDS vs customer-managed | All Gateway persistence deployments |
| D-05 | **One-time SQLite data migration** | Export/import tool for early adopters with existing `apme.db` files vs fresh-install only | Pre-release adopters only |
| D-06 | **Craig P0 scope** | Full A1–A12 vs phased MVP (A1,A3,A12,A2) | Portal milestone |
| D-07 | **Governance / ownership** | Section 4.6 RACI | Support model |
| D-08 | **DevTools** | In-cluster vs local-only CLI | Developer UX |
| D-09 | **Alembic ownership** | Gateway team vs shared platform | Schema change velocity |
| D-10 | **API contract** | OpenAPI publish location, deprecation policy, consumer SLA | Integrators |

### Decided

| ID | Decision | Date | Reference |
|----|----------|------|-----------|
| DC-01 | **Validator scope:** Collection Health + Dep Audit required; Gitleaks optional | 2026-06-24 | §4.10 |
| DC-02 | **Gateway persistence:** PostgreSQL only (upstream, downstream/production, dev); SQLite removed entirely | 2026-06-25 | §9, ADR-029 amendment |
| DC-03 | **Dual tracks:** Portal product (Track A) vs upstream standalone (Track B) | 2026-06-24 | §2 |
| DC-04 | **Portal integration:** Shared PostgreSQL instance; APME owns `apme_*`; thin Portal proxy | 2026-06-25 | §8, PR #352 v2 |

---

## Section 7 — Priority Sequence

### Phase 1 — Foundation (weeks 1–4)

1. Validator scope alignment (4.10) — launcher, Helm, Podman, health-check
2. SQLite removal + Alembic + PostgreSQL (upstream, downstream/production, dev — bundled or external PG) (4.5, §9)
3. Auth ADR — bearer for Track B (ADR-038); draft Portal service-token ADR (4.3, D-01)

### Phase 2 — Hardening (weeks 5–8)

4. Security — `USER` in all Dockerfiles (4.2, A12)
5. CI images — collection-health + dep-audit in `container-images.yml` (4.1)
6. Dependency and repo hardening (4.7, 4.8)

### Phase 3 — Product Integration (weeks 9–12)

7. Portal A1–A4 — auth, PostgreSQL+Alembic, optional credentials, admin/db APIs
8. Portal Helm subchart packaging, upgrade hooks, image pins (D-02)
9. API contract + RFC 9745/8594 middleware (4.4, A9)

### Phase 4 — Scale & Handoff (weeks 13+)

11. HA Gateway — multi-replica after PostgreSQL (4.5)
12. Load testing — engine pod at Portal scale (100+ repos, A5 PVC sizing)
13. Observability — Prometheus `/metrics`, structured logging (A10)
14. Rate limiting (A11), air-gapped mode (A6)
15. Ownership/governance sign-off (4.6) and operator handoff documentation
16. ADR-049 daemon Gateway embedding (CLI track — parallel, not Portal-blocking)

---

## Section 8 — Portal Integration Requirements (A1–A12)

Source: [PR #352](https://github.com/ansible/apme/pull/352) (updated 2026-06-25 — shared-DB-instance architecture), [ansible-rhdh-plugins PR #676](https://github.com/ansible/ansible-rhdh-plugins/pull/676), [ANSTRAT-2222](https://issues.redhat.com/browse/ANSTRAT-2222).

### Core Architecture: Shared DB Instance, Separate Ownership

1. **APME keeps its database** — Gateway maintains `apme_*` tables via Alembic; stores scan results, rules, projects
2. **Shared PostgreSQL instance** — Track A: APME connects to Portal's PostgreSQL; each service manages its own tables
3. **Portal is a thin proxy client** — RBAC + optional credential injection + forward to APME API; no APME data layer in Portal
4. **Optional credentials per-request** — Portal injects tokens when available; APME falls back to own settings (A3)
5. **Same API for standalone and integrated** — no Gateway mode split; ~90% of REST surface already exists
6. **Day 0/Day 2 management APIs** — APME exposes DB migrate/status/backup/restore; Portal orchestrates (A4)

### Why This Design Over Alternatives

| Alternative | Problem | This Design |
| --- | --- | --- |
| **Separate APME DB** | Two databases for operators — double provisioning, backup, HA | Single PostgreSQL instance; Portal manages instance, APME manages `apme_*` tables |
| **Portal owns data (stateless Gateway)** | Portal must understand APME schema; breaks standalone; requires gutting Gateway | APME owns schema; Portal is thin proxy; works standalone and integrated |

> **Supersedes:** Earlier PR #352 draft (stateless Gateway, tarball `POST /scan`, Portal-owned `apme_*` tables). That model is **rejected**.

### C4 Context — APME in the Portal Ecosystem

```
                     ┌───────────────────────┐
                     │  Ansible Automation   │
                     │       Portal          │
                     │  (thin proxy client)  │
                     └───────────┬───────────┘
                                 │
                    REST API (optional credentials)
                                 │
                     ┌───────────▼───────────┐
                     │   APME Gateway        │
                     │   Full service        │
                     │   Owns apme_* tables  │
                     │   REST :8080          │
                     └───────────┬───────────┘
                                 │ gRPC
                     ┌───────────▼───────────┐
                     │   APME Engine Pod     │
                     │  Primary + Validators │
                     │  + Galaxy Proxy       │
                     └───────────┬───────────┘
                                 │
                     ┌───────────▼───────────┐
                     │  Shared PostgreSQL    │
                     │  backstage_* (Portal) │
                     │  apme_* (APME)        │
                     └───────────────────────┘
```

### Gap Table (A1–A12)

| ID | Requirement | Priority | Current | Gap | Effort |
|----|-------------|----------|---------|-----|--------|
| **A1** | Gateway auth middleware (Bearer service token) | Critical | No auth middleware | `verify_service_token`; Helm secret | Small |
| **A2** | PostgreSQL + Alembic | Critical | SQLite only (`APME_DB_PATH`) | `APME_DATABASE_URL`, `asyncpg`, Alembic migrations | Medium (1–2 sprints) |
| **A3** | Optional credential acceptance | High | Partial | `scm_token`, `galaxy_servers` optional on operations; fallback to APME settings | Small–Medium |
| **A4** | Day 0/Day 2 management APIs | High | Not implemented | `/admin/db/migrate`, status, health, vacuum, backup/restore | Medium (1 sprint) |
| **A5** | PVC sizing for scale | Medium | 10Gi defaults | sessions 50Gi, proxy-cache 20Gi; no Gateway DB PVC (shared PG) | Small |
| **A6** | Air-gapped mode flag | Medium | Not implemented | `APME_AIR_GAPPED=true` | Small (proposed ADR) |
| **A7** | *(reserved / Portal-side)* | — | — | Portal backend plugin | — |
| **A8** | *(reserved / Portal-side)* | — | — | Portal plugin UX | — |
| **A9** | API versioning (RFC 9745/8594) | High | Not implemented | Deprecation + Sunset middleware; published OpenAPI | Medium |
| **A10** | Observability (Prometheus + structured logging) | High | Limited | `/metrics`, JSON logs | Medium |
| **A11** | Rate limiting | Medium | Not implemented | `APME_RATE_LIMIT` | Small |
| **A12** | Non-root containers | High | Documented only | `USER` in all Dockerfiles | Small |

### A2 — PostgreSQL + Alembic

APME is not yet released — clean PostgreSQL implementation, no SQLite dual-mode.

**Required:**

- `asyncpg` dependency; configure via `APME_DATABASE_URL`
- Track A: `postgresql+asyncpg://user:pass@portal-db:5432/apme` (shared instance)
- Track B: bundled PostgreSQL subchart or external URL
- Alembic migrations for all `apme_*` tables
- Connection pooling (pool size limits when sharing Portal's PostgreSQL)

```yaml
gateway:
  database:
    url: ""           # PostgreSQL URI; empty = error (SQLite removed)
    poolSize: 10
    maxOverflow: 5
```

### A3 — Optional Credential Acceptance

All scan/operation endpoints accept optional credentials. If Portal provides them, use them; otherwise fall back to APME's configured project/Galaxy settings.

```python
class OperateRequest(BaseModel):
    action: Literal["check", "remediate"]
    scm_token: Optional[str] = None
    galaxy_servers: Optional[list] = None
    options: Optional[dict] = None
```

Works for Portal-integrated (Portal injects tokens), standalone (APME settings), and CI/CD (caller passes tokens).

### A4 — Day 0 / Day 2 Management APIs

Portal manages the PostgreSQL **instance** but not APME's schema. APME exposes admin endpoints (service-token protected):

```
POST /api/v1/admin/db/migrate     → Alembic migrations on apme_* tables
GET  /api/v1/admin/db/status      → schema version, table count, needs_migration
GET  /api/v1/admin/db/health      → connection + pool stats
POST /api/v1/admin/db/vacuum      → retention cleanup
POST /api/v1/admin/db/backup      → pg_dump apme_* tables only
POST /api/v1/admin/db/restore     → restore apme_* from backup
GET  /api/v1/admin/db/backups     → list backups
```

Portal Helm post-install/post-upgrade hook calls `POST /admin/db/migrate`. Instance-level full backup (Portal + APME) remains Portal's responsibility.

### API Surface — Existing Endpoints (~90% Ready)

| Endpoint Group | Portal Need | Status |
| --- | --- | --- |
| Projects CRUD | Manage scanned repos | Ready |
| Operations (scan trigger) | Trigger scans with optional credentials | Needs A3 |
| Operations (SSE progress) | Real-time progress | Ready |
| Operations (approve) | HITL proposal approval | Ready |
| Operations (create PR) | Open PR with fixes | Ready (optional `scm_token`) |
| Violations / trend / dashboard | Metrics and history | Ready |
| Rule catalog | List/configure rules | Ready |
| Galaxy server config | Manage Galaxy credentials | Ready |
| Health / dependencies / SBOM | Monitoring and deps | Ready |
| **Admin/DB management** | Day 0/Day 2 ops | **Needs A4** |

No new tarball `POST /scan` endpoint required — existing `operation_router` + project model cover Portal needs.

### Galaxy / Automation Hub Credential Flow

```
Portal (optional per-request)              APME (own settings)
┌─────────────────────────┐                ┌─────────────────────────┐
│ ansible.rhaap.baseUrl   │                │ Gateway Galaxy config   │
│ ansible.rhaap.token     │  operations  │ (apme_galaxy_servers)   │
│ Injected as optional    │  ──────────► │ If request has tokens   │
│ galaxy_servers          │              │ → use them              │
└─────────────────────────┘              │ Else → own config     │
                                         └─────────────────────────┘
```

In AAP 2.5+, Hub is behind AAP Gateway: `ansible.rhaap.baseUrl` + `ansible.rhaap.token` (AAP admin token, org-scoped).

### Git-Based Collection Dependencies

| Phase | Scope |
|-------|-------|
| **Phase 1** | Collections on Galaxy or Automation Hub only; `type: git` in `requirements.yml` not supported in Portal scans |
| **Phase 2** | Optional admin SCM token per-request, or Portal pre-builds git collections |

### Custom Rules — Phase 2 (Proposed — Requires ADR)

Policy-as-code from git; Portal passes optional `custom_policies` at scan time. Phase 1 uses existing `rule_configs` in `ScanOptions`.

### A5 — PVC Sizing (Portal Scale)

```yaml
persistence:
  sessions:
    size: 50Gi
  proxyCache:
    size: 20Gi
  # No Gateway DB PVC — shared PostgreSQL instance
```

### Upgrade Strategy (Track A)

APME ships as Portal Helm subchart. `helm upgrade` updates Portal + APME together:

1. Engine pods RollingUpdate (sessions/cache PVCs preserved)
2. Gateway pods RollingUpdate; post-upgrade hook: `POST /admin/db/migrate`
3. Portal pods updated with new APME plugin
4. Rollback: `helm rollback`; Alembic supports downgrade to target version

**bootc:** `portal-post-upgrade.service` calls `POST /admin/db/migrate` after reboot.

### Implementation Order (Portal)

1. **A1** — Gateway auth
2. **A2** — PostgreSQL + Alembic (enables shared-instance architecture)
3. **A3** — Optional credentials
4. **A4** — Day 0/Day 2 management APIs
5. **A12** — Non-root containers
6. **A9** — API versioning contract
7. **A10** — Observability
8. **A5** — PVC sizing
9. **A6** — Air-gapped mode
10. **A11** — Rate limiting

### What Stays Unchanged

- Primary gRPC / `FixSession` protocol
- All validators (read-only)
- Galaxy Proxy PEP 503 caching
- Engine pod architecture (ADR-012)
- Remediation engine (3-tier model)
- Rule ID conventions (ADR-008)
- **Gateway REST API** — existing endpoints preserved; admin/db and optional credentials added
- **Track B** standalone UI and full Gateway path

## Section 9 — PostgreSQL Only (SQLite Removal)

### Decision

**PostgreSQL is the sole APME Gateway database** wherever the Gateway persists data:

- **Upstream/dev (Track B):** Helm, Podman pod, bootc, CLI daemon (ADR-049) — bundled PostgreSQL container **or** external `APME_DATABASE_URL`
- **Downstream/production:** Konflux-built images, customer OpenShift/K8s, Red Hat catalog deployments — same PostgreSQL requirement; no SQLite fallback
- **Local development:** bundled PostgreSQL sidecar/container (e.g. Podman compose, Helm subchart) — **not** SQLite files

**SQLite is removed entirely** from APME: delete `APME_DB_PATH`, `aiosqlite`, SQLite-specific pragmas/migrations, and Gateway SQLite PVCs. Early adopters with existing `apme.db` files get a one-time export/import path (open decision D-05).

**Portal Track A:** connect to shared Portal PostgreSQL instance via `APME_DATABASE_URL`; APME runs Alembic on `apme_*` tables only (A4). Same implementation as Track B — different connection target.

### Configuration Example

```yaml
# apme-gateway-config.yaml
gateway:
  database:
    mode: bundled          # bundled | external | shared
    # bundled: deploy PostgreSQL subchart (Track B standalone)
    # external: customer-managed PostgreSQL
    # shared: Portal PostgreSQL instance (Track A)
    url: ""                # required — postgresql+asyncpg://...
    pool_size: 10
    max_overflow: 5
    echo: false
```

### Environment Variables

| Variable | Description | Status |
|----------|-------------|--------|
| `APME_DATABASE_URL` | PostgreSQL DSN (`postgresql+asyncpg://user:pass@host:5432/apme`) | **Required** when Gateway persists data |
| `APME_DATABASE_MODE` | `bundled`, `external`, or `shared` | **Required** |
| `APME_DB_PATH` | *(removed)* | **Dropped** — SQLite no longer supported |

### Track A Shared Instance Note

When `APME_DATABASE_MODE=shared`, Gateway connects to Portal's PostgreSQL. Use conservative pool sizing. Portal post-upgrade hooks call `POST /api/v1/admin/db/migrate` (A4).

### Implementation Checklist

- [ ] `src/apme_gateway/db/` — async engine factory supporting PostgreSQL via `APME_DATABASE_URL`
- [ ] `src/apme_gateway/config.py` — add `database_url`, `database_mode`, `gateway_mode`; **remove** `db_path`
- [ ] `pyproject.toml` — add `asyncpg`, `alembic`; **remove** `aiosqlite` from gateway deps
- [ ] `src/apme_gateway/db/` — **delete** SQLite engine factory, pragmas, ad-hoc column migrations
- [ ] `alembic/` — initial revision from current ORM models; forward-only migrations
- [ ] `alembic.ini` + env.py wired to `GatewayConfig`
- [ ] Helm — PostgreSQL subchart **or** `externalDatabase.*` values; Secret for `APME_DATABASE_URL`
- [ ] Helm — Gate multi-replica Gateway on `database.mode != none`
- [ ] Podman — document external PostgreSQL or optional compose sidecar for integration tests
- [ ] bootc — external PostgreSQL connection or bundled PG unit
- [ ] **ADR-029 amendment** — PostgreSQL only; SQLite removed (supersedes SQLite-as-V1)
- [ ] One-time migration guide — export existing `apme.db` → PostgreSQL import (addresses D-05; pre-release adopters only)
- [ ] Gateway `/health` — DB ping when `database.mode != none`
- [ ] Remove `APME_DB_PATH`, Gateway SQLite PVC, and all SQLite references from Helm, Podman, bootc, and docs
- [ ] CI integration test job with PostgreSQL service container

---

## Section 10 — Follow-Up ADRs and Tasks

| Artifact | Type | Title | Trigger | Phase |
|----------|------|-------|---------|-------|
| ADR-055 (proposed) | ADR | Shared PostgreSQL instance integration (Track A) | §8 architecture | 3 |
| ADR-056 (proposed) | ADR | Portal service-token authentication | D-01 resolution | 1 |
| ADR-029-amend | ADR | PostgreSQL only — SQLite removal | §9 checklist | 1 |
| ADR-057 (proposed) | ADR | Required validator set (Collection Health + Dep Audit) | 4.10 completion | 1 |
| ADR-058 (proposed) | ADR | API versioning — RFC 9745 / RFC 8594 | 4.4 / A9 | 3 |
| ADR-059 (proposed) | ADR | Portal ephemeral custom Rego policies (Phase 2) | Custom rules scope | 4+ |
| ADR-049-impl | TASK | Embed Gateway in CLI daemon | ADR-049 accepted, not coded | 4 (parallel) |
| REQ-005 (proposed) | REQ | Portal product integration (ANSTRAT-2222) | §8 | 3 |
| TASK-005-01 | TASK | Alembic initial migration + PG engine factory | §9 | 1 |
| TASK-005-02 | TASK | Promote CH/DA to required in launcher + Helm | 4.10 | 1 |
| TASK-005-03 | TASK | CI publish collection-health + dep-audit images | 4.1 | 2 |
| TASK-005-04 | TASK | Gateway auth middleware (A1) | §8 | 3 |
| TASK-005-05 | TASK | Optional credential acceptance on operations (A3) | §8 | 3 |
| TASK-005-08 | TASK | Day 0/Day 2 admin/db APIs (A4) | §8 | 3 |
| TASK-005-06 | TASK | `USER` directive all Dockerfiles (A12) | 4.2 | 2 |
| TASK-005-07 | TASK | Prometheus `/metrics` on Gateway (A10) | §8 | 4 |
| DR-xxx | DR | Packaging registry / Operator ownership | D-02 | 3 |
| DR-xxx | DR | One-time SQLite data export for pre-release adopters | D-05 | 1 |

---

## References

| Reference | Location / Link |
|-----------|-----------------|
| Architecture | [`.sdlc/context/architecture.md`](architecture.md) |
| Deployment | [`.sdlc/context/deployment.md`](deployment.md) |
| ADR-001 gRPC | [`.sdlc/adrs/ADR-001-grpc-communication.md`](../adrs/ADR-001-grpc-communication.md) |
| ADR-012 Scale pods | [`.sdlc/adrs/ADR-012-scale-pods-not-services.md`](../adrs/ADR-012-scale-pods-not-services.md) |
| ADR-020 Stateless engine | [`.sdlc/adrs/ADR-020-stateless-engine.md`](../adrs/ADR-020-stateless-engine.md) |
| ADR-029 Web Gateway | [`.sdlc/adrs/ADR-029-web-gateway-architecture.md`](../adrs/ADR-029-web-gateway-architecture.md) |
| ADR-037 Project-centric UI | [`.sdlc/adrs/ADR-037-project-centric-ui-model.md`](../adrs/ADR-037-project-centric-ui-model.md) |
| ADR-038 Public data API | [`.sdlc/adrs/ADR-038-public-data-api.md`](../adrs/ADR-038-public-data-api.md) |
| ADR-042 Plugin services | [`.sdlc/adrs/ADR-042-third-party-plugin-services.md`](../adrs/ADR-042-third-party-plugin-services.md) |
| ADR-049 Gateway in daemon | [`.sdlc/adrs/ADR-049-gateway-in-daemon.md`](../adrs/ADR-049-gateway-in-daemon.md) |
| ADR-054 Production deploy | [`.sdlc/adrs/ADR-054-production-deployment.md`](../adrs/ADR-054-production-deployment.md) |
| DR-008 Data persistence | [`.sdlc/decisions/closed/decided/DR-008-data-persistence.md`](../decisions/closed/decided/DR-008-data-persistence.md) |
| Portal integration research | [PR #352](https://github.com/ansible/apme/pull/352) |
| Portal plugins PR 676 | https://github.com/ansible/ansible-rhdh-plugins/pull/676 |
| ANSTRAT-2222 | https://issues.redhat.com/browse/ANSTRAT-2222 |
| CI container workflow | [`.github/workflows/container-images.yml`](../../.github/workflows/container-images.yml) |
| Helm chart | [`deploy/helm/apme/`](../../deploy/helm/apme/) |
| Launcher | [`src/apme_engine/daemon/launcher.py`](../../src/apme_engine/daemon/launcher.py) |
| Gateway config | [`src/apme_gateway/config.py`](../../src/apme_gateway/config.py) |
| RFC 9745 API Versioning | https://www.rfc-editor.org/rfc/rfc9745.html |
| RFC 8594 Deprecation | https://www.rfc-editor.org/rfc/rfc8594.html |
| Project constitution | [`CLAUDE.md`](../../CLAUDE.md) |
| Agent invariants | [`AGENTS.md`](../../AGENTS.md) |
| User deployment guide | [`docs/guides/DEPLOYMENT.md`](../../docs/guides/DEPLOYMENT.md) |

---

*End of productization plan — draft for review.*
