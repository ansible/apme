# APME Productization Plan — Pre-Read

**Prepared:** 2026-06-24  
**Updated:** 2026-06-25  
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
│  │ Gateway :8080 (REST) / :50060 (gRPC Reporting — Track B full mode only) │  │
│  │ PostgreSQL only (bundled/external) │ Track A proxy: no APME database │  │
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
| **Gateway** | REST API, Reporting gRPC (full mode), persistence (full mode) | **PostgreSQL only** (bundled or external) wherever Gateway persists data. **Portal proxy mode (Track A):** no APME database — Portal owns persistence. |
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
| **Distribution** | Bundled with Portal Operator / bootc product image |
| **Gateway mode** | Proxy mode (`APME_GATEWAY_MODE=proxy`) — stateless REST↔gRPC proxy |
| **Database** | Portal PostgreSQL (`apme_*` tables); **no** APME Gateway DB or PVC |
| **UI** | Portal UX only; **no** standalone APME UI container |
| **Auth** | Portal Auth/RBAC + service token for Portal→Gateway |
| **Content ingress** | Portal clones SCM, sends tarballs — APME never holds SCM credentials |
| **Target users** | AAP customers consuming APME via Ansible Automation Portal |

### Track B — Upstream / Dev

| Attribute | Value |
|-----------|-------|
| **Distribution** | Standalone Helm (`deploy/helm/apme/`), Podman pod (`tox -e up`), bootc VM, CLI daemon; same PostgreSQL requirement applies to downstream/production (Konflux, Red Hat catalog) |
| **Gateway mode** | Full mode (`APME_GATEWAY_MODE=full`) — owns REST, reporting, persistence |
| **Database** | **PostgreSQL only** — bundled container or external URL; SQLite removed entirely |
| **UI** | Standalone APME UI (:8081) — **not removed** for upstream |
| **Auth** | Bearer tokens per ADR-038 (Proposed) |
| **Target users** | Open-source consumers, integrators, developers |

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
| **Galaxy credentials** | Per-request in `POST /scan` body — never persisted by APME |

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
| gateway | proxy | full | ✓ | ✓ |
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
- Portal proxy mode exposes a reduced surface (`POST /scan`, `GET /scan/{id}/events`, etc.) — versioning applies to both modes.
- **Deliverable:** API contract document; cross-ref PR #351 / this plan §4.4.

### 4.5 — PostgreSQL (SQLite Removal)

**Decision:** All deployments where the APME Gateway persists data use **PostgreSQL only** — upstream/dev (Track B), downstream/production (Konflux, customer Helm, bootc), and future CLI daemon Gateway (ADR-049). **SQLite is removed entirely** — no dev exception, no dual-mode fallback. See Section 9.

| Item | Requirement |
|------|-------------|
| **Alembic** | Required — schema migrations versioned in repo |
| **Multi-replica Gateway** | Enabled with PostgreSQL (remove SQLite single-replica guard) |
| **Connection pooling** | SQLAlchemy async + pool config in Helm |
| **Bundled vs external PG** | Config file / Helm values: bundled PostgreSQL container **or** external `APME_DATABASE_URL` |
| **Portal proxy mode (Track A)** | No APME database — Gateway is stateless; Portal PostgreSQL owns persistence |
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
- Air-gapped mode (A5): disable PyPI fallback, optional dep-audit skip — **proposed**, requires ADR.

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
| CR-1 | Gateway proxy mode without APME DB | P0 | Spec §8 A2 |
| CR-2 | Tarball scan — no SCM credentials in APME | P0 | Spec §8 A3 |
| CR-3 | Galaxy credentials per-request from Portal | P0 | Spec §8 Galaxy flow |
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
- **Separate work stream from Portal Track A** — does not block proxy mode.
- Deliverable: embed FastAPI + ReportingServicer + PostgreSQL in daemon per ADR-049 (bundled or external PG — no SQLite).

### Feature Flags

| Flag | Purpose | Values |
|------|---------|--------|
| `APME_GATEWAY_MODE` | Gateway operating mode | `full` (Track B default), `proxy` (Track A) |
| `APME_DATABASE_MODE` | Persistence backend | `bundled`, `external`, `none` (proxy) |
| `APME_DATABASE_URL` | PostgreSQL DSN (required when Gateway persists data) | `postgresql+asyncpg://...` |
| `APME_DB_PATH` | *(removed)* | **Dropped** — SQLite no longer supported; remove from code and docs |
| `APME_AUTH_DISABLED` | Skip Gateway auth | `true` dev only — not for production |
| `APME_AIR_GAPPED` | Air-gapped deployments | `true` / `false` (A5, proposed) |
| `APME_RATE_LIMIT` | Gateway throttling | e.g. `100/minute` (A11, proposed) |

Gateway reads `APME_GATEWAY_MODE` at startup to enable/disable local SQLAlchemy init, Reporting gRPC server, full REST surface, and Portal proxy routes.

---

## Section 6 — Decision Matrix

### Open Decisions

| ID | Topic | Options | Blocker for |
|----|-------|---------|-------------|
| D-01 | **Auth model (Portal)** | Service token list vs mTLS vs OAuth client credentials | Track A GA |
| D-02 | **Packaging** | See Section 2 table | Release pipeline |
| D-03 | **Gateway modes** | Runtime flag only vs separate proxy image build | Helm/CI complexity |
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
| DC-04 | **Portal persistence:** No APME DB in proxy mode — Portal PostgreSQL | 2026-06-24 | §8 A2, PR #352 |

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

7. Portal A1–A3 — auth middleware, tarball `POST /scan`, Galaxy per-request credentials
8. Portal A2 — Gateway proxy mode behind `APME_GATEWAY_MODE=proxy`
9. Packaging decisions (D-02) — Operator subchart, image pins, CLI artifact
10. API contract + RFC 9745/8594 middleware (4.4, A9)

### Phase 4 — Scale & Handoff (weeks 13+)

11. HA Gateway — multi-replica after PostgreSQL (4.5)
12. Load testing — engine pod at Portal scale (100+ repos, A4 PVC sizing)
13. Observability — Prometheus `/metrics`, structured logging (A10)
14. Rate limiting (A11), air-gapped mode (A5), rule catalog API (A6)
15. Ownership/governance sign-off (4.6) and operator handoff documentation
16. ADR-049 daemon Gateway embedding (CLI track — parallel, not Portal-blocking)

---

## Section 8 — Portal Integration Requirements (A1–A12)

Source: [PR #352](https://github.com/ansible/apme/pull/352) — `.sdlc/research/portal-integration/00-integration-requirements.md`, [ansible-rhdh-plugins PR #676](https://github.com/ansible/ansible-rhdh-plugins/pull/676), [ANSTRAT-2222](https://issues.redhat.com/browse/ANSTRAT-2222).

### Corrections (this plan vs PR #352 draft)

| PR #352 statement | Correction |
|-------------------|------------|
| "APME standalone UI is removed" | **UI is removed only for Track A (Portal).** Track B upstream retains standalone UI (:8081). |
| CLI daemon unchanged | **ADR-049 Gateway-in-daemon is separate work** — not implemented; not required for Portal proxy mode. |
| Custom rules / air-gapped / rate limit | Marked **proposed** — each requires ADR before implementation. |

### Core Architectural Decisions (Portal)

1. **Portal owns all scan data** — stored in Portal PostgreSQL (`apme_*` tables).
2. **APME Gateway becomes stateless in proxy mode** — no database, no PVC, thin REST↔gRPC proxy.
3. **Portal clones repos** — tars content, sends to APME via REST; APME never holds SCM credentials.
4. **PR creation is Portal-side** — using user's OAuth token, not APME.
5. **Feature flag:** `APME_GATEWAY_MODE=proxy` (Portal) vs `full` (standalone upstream).

### C4 Context — APME in the Portal Ecosystem

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

### Gap Table (A1–A12)

| ID | Requirement | Priority | Current | Gap | Effort |
|----|-------------|----------|---------|-----|--------|
| **A1** | Gateway auth middleware (Bearer service token) | Critical | No auth middleware | Implement `verify_service_token`; Helm secret | Small |
| **A2** | Gateway stateless proxy mode | High | Full Gateway with PostgreSQL + UI | `APME_GATEWAY_MODE=proxy`; remove persistence paths | Medium (1–2 sprints) |
| **A3** | Tarball scan endpoint `POST /scan` | High | WS upload / project CRUD model | New scan router + tarball service + FixSession proxy | Medium (1 sprint) |
| **A4** | PVC sizing for scale | Medium | 10Gi defaults | sessions 50Gi, proxy-cache 20Gi; no gateway PVC in proxy | Small |
| **A5** | Air-gapped mode flag | Medium | Not implemented | `APME_AIR_GAPPED=true` behavior | Small (proposed ADR) |
| **A6** | Rule catalog API (stateless) | Medium | Partial via Primary | `GET /rules` from in-memory catalog | Small |
| **A7** | *(reserved / Portal-side)* | — | — | Portal backend plugin owns CRUD | — |
| **A8** | *(reserved / Portal-side)* | — | — | Portal SQL for trends, dashboard | — |
| **A9** | API versioning (RFC 9745/8594) | High | Not implemented | Deprecation + Sunset middleware | Medium |
| **A10** | Observability (Prometheus + structured logging) | High | Limited | `/metrics` endpoint, JSON logs | Medium |
| **A11** | Rate limiting | Medium | Not implemented | `APME_RATE_LIMIT` | Small (proposed ADR) |
| **A12** | Non-root containers | High | Documented only | `USER` in all Dockerfiles | Small |

### A2 — Proxy Mode Details

When `APME_GATEWAY_MODE=proxy`:

**Remove / skip:**

- PostgreSQL database initialization, SQLAlchemy models, Alembic migrations (Track B full mode only)
- gRPC Reporting servicer (:50060) — Portal stores results directly
- Persistence-dependent REST: projects CRUD, dashboard, activity, trends, settings, notifications, suppressions
- UI deployment (:8081) — **Track A only**

**Keep (stateless):**

- `GET /health` — probes Primary + validators via gRPC
- `GET /rules` — from Primary in-memory catalog (A6)
- SSE: `GET /scan/{id}/events` — proxies gRPC SessionEvents
- `POST /scan/{id}/approve`, `POST /scan/{id}/cancel`
- Bearer auth middleware (A1)

**Add:**

- `POST /scan` — tarball upload (A3)

**Files (from PR #352):**

- `src/apme_gateway/app.py` — conditional startup
- `src/apme_gateway/api/router.py` — proxy route registration
- `deploy/helm/apme/values.yaml` — `gateway.mode: proxy`
- Remove in proxy chart overlay: `gateway-pvc.yaml`, reporting service, `ui-deployment.yaml`

### A3 — Tarball Scan Endpoint

**Endpoint:** `POST /scan`

| Field | Type | Description |
|-------|------|-------------|
| `content` | `UploadFile` | tar.gz of Ansible project |
| `options` | JSON form string | `ansible_core_version`, `collection_specs`, `rule_configs` |
| `galaxy_servers` | JSON form string | `[{name, url, token, auth_url}]` per-scan |
| `action` | form string | `check` or `remediate` |

**Flow:**

1. Extract tarball to temp directory
2. Discover Ansible files
3. Configure Galaxy Proxy with per-request credentials
4. Run `FixSession` via Primary gRPC
5. Cleanup temp dir
6. Return violations, proposals, patches, diagnostics, summary

**SSE progress:** `GET /scan/{scan_id}/events`  
**Approvals:** `POST /scan/{scan_id}/approve`

**New modules (proposed):**

- `src/apme_gateway/api/scan_router.py`
- `src/apme_gateway/services/tarball_service.py`
- `src/apme_gateway/services/scan_proxy.py`

### API Surface — Before vs After (Proxy Mode)

**Removed (Portal backend plugin owns):**

| Endpoint | Previous purpose | New owner |
|----------|------------------|-----------|
| CRUD `/projects` | Project management | Portal `apme_projects` |
| `GET /dashboard/*` | Aggregate metrics | Portal SQL |
| `GET /activity/*` | Scan history | Portal `apme_scans` |
| `GET /violations/top` | Top rules | Portal SQL |
| `GET /stats/*` | Remediation rates | Portal SQL |
| CRUD `/settings/galaxy-servers` | Galaxy config | Portal `apme_galaxy_servers` |
| `POST /suppressions` | Rule suppressions | Portal `apme_rule_overrides` |
| `GET /notifications/*` | Notifications | Portal notification service |
| `GET /projects/{id}/trend` | Trends | Portal SQL |
| `GET /projects/{id}/dependencies` | SBOM | Portal (post-scan store) |

**Kept (stateless proxy):**

| Endpoint | Purpose |
|----------|---------|
| `POST /scan` | **NEW** — tarball upload, trigger scan |
| `GET /scan/{id}/events` | SSE — proxy SessionEvents |
| `POST /scan/{id}/approve` | Forward proposal approvals |
| `POST /scan/{id}/cancel` | Cancel running scan |
| `GET /rules` | Rule catalog (A6) |
| `GET /health` | Health check |

**Track B full mode retains** `/api/v1/ws/session`, project CRUD, dashboard, and full persistence API.

### A4 — PVC Sizing (Portal Scale)

```yaml
persistence:
  sessions:
    size: 50Gi        # up from 10Gi — 100+ repos
  proxyCache:
    size: 20Gi        # up from 10Gi
  # gateway PVC REMOVED in proxy mode
```

### Galaxy / Automation Hub Credential Flow

```
Portal (owns credentials)                    APME (stateless)
┌─────────────────────────┐                 ┌─────────────────────────┐
│ apme_galaxy_servers     │                 │ Gateway (no DB)         │
│ + K8s Secrets for tokens│  POST /scan     │                         │
│                         │  galaxy_servers │  → Galaxy Proxy admin   │
│ ansible.rhaap.baseUrl   │  ────────────►  │  temp ansible.cfg       │
│ ansible.rhaap.token     │                 │  ephemeral per-scan     │
└─────────────────────────┘                 └─────────────────────────┘
```

**Sources in Portal:**

1. Existing Portal config: `ansible.rhaap.baseUrl` + `ansible.rhaap.token` (AAP admin token, org-scoped)
2. Optional additional servers → `apme_galaxy_servers` table
3. Default community Galaxy fallback (no token)

**Key points:**

- Tokens in K8s Secrets, not DB plaintext
- Portal injects tokens per scan request
- APME never persists Galaxy credentials
- Galaxy Proxy writes temp `ansible.cfg`, downloads collections, deletes temp file

### Git-Based Collection Dependencies

| Phase | Scope |
|-------|-------|
| **Phase 1** | Collections must be on Galaxy or Automation Hub. `type: git` in `requirements.yml` **not supported** in Portal scans. Aligns with AAP 2.5+ Hub model. |
| **Phase 2** | Portal passes admin SCM token in scan request (`GH_TOKEN` for `ansible-galaxy`), **or** Portal pre-builds git collections and includes tarball in upload. APME stays credential-free in preferred path. |

### Custom Rules — Phase 2 (Proposed — Requires ADR)

- Policy-as-code in git; Portal clones policy repo, passes `custom_policies: [{name, rule_id, rego_source}]` in `POST /scan`
- Gateway writes temp Rego alongside built-in OPA bundle; OPA loads merged bundle
- Custom rule IDs: `P100+` prefix (distinct from built-in `P001–P004`)
- **Phase 1:** per-project `.apme/rules.yml` and `rule_configs` in `ScanOptions` already supported — no git policy merge yet

### Helm Chart — Proxy Mode Overlay

```yaml
gateway:
  mode: proxy
  replicas: 2                      # stateless — scale freely
  resources:
    requests: { cpu: 250m, memory: 256Mi }
    limits: { cpu: 500m, memory: 512Mi }
  auth:
    enabled: true
    serviceTokenSecret: apme-service-token

# Remove in proxy overlay:
# - gateway-pvc.yaml
# - gateway-reporting-service.yaml (:50060)
# - ui-deployment.yaml / ui-service.yaml
```

### bootc (Proxy / Product)

APME container volumes:

- `/sessions` — venv session storage
- `/cache` — Galaxy Proxy wheel cache
- **No Gateway DB volume**

### Implementation Order (Portal — from PR #352)

1. **A1** — Gateway auth (security prerequisite)
2. **A3** — Tarball scan endpoint
3. **A12** — Non-root containers
4. **A2** — Gateway stateless refactor (`APME_GATEWAY_MODE=proxy`)
5. **A9** — API versioning contract
6. **A10** — Observability
7. **A4** — PVC sizing
8. **A5** — Air-gapped mode
9. **A6** — Rule catalog API
10. **A11** — Rate limiting

### What Stays Unchanged

- Primary gRPC / `FixSession` protocol
- All validators (read-only)
- Galaxy Proxy PEP 503 caching
- Engine pod architecture (ADR-012 — scale pods, not services)
- Remediation engine (3-tier model)
- Rule ID conventions (ADR-008)
- **Track B** standalone UI, full Gateway, PostgreSQL-only path (upstream + downstream/production)

---

## Section 9 — PostgreSQL Only (SQLite Removal)

### Decision

**PostgreSQL is the sole APME Gateway database** wherever the Gateway persists data:

- **Upstream/dev (Track B):** Helm, Podman pod, bootc, CLI daemon (ADR-049) — bundled PostgreSQL container **or** external `APME_DATABASE_URL`
- **Downstream/production:** Konflux-built images, customer OpenShift/K8s, Red Hat catalog deployments — same PostgreSQL requirement; no SQLite fallback
- **Local development:** bundled PostgreSQL sidecar/container (e.g. Podman compose, Helm subchart) — **not** SQLite files

**SQLite is removed entirely** from APME: delete `APME_DB_PATH`, `aiosqlite`, SQLite-specific pragmas/migrations, and Gateway SQLite PVCs. Early adopters with existing `apme.db` files get a one-time export/import path (open decision D-05).

**Portal Track A (proxy mode):** no APME database — skip all Gateway DB items below; Portal PostgreSQL owns persistence.

### Configuration Example

```yaml
# apme-gateway-config.yaml (Track B full mode)
gateway:
  mode: full

database:
  mode: bundled          # bundled | external | none
  # bundled: deploy PostgreSQL subchart, auto-wire URL
  # external: customer supplies APME_DATABASE_URL
  # none: proxy mode only (Track A)
  url: ""                # required when mode=external
  pool_size: 10
  echo: false
```

### Environment Variables

| Variable | Description | Status |
|----------|-------------|--------|
| `APME_DATABASE_URL` | PostgreSQL DSN (`postgresql+asyncpg://user:pass@host:5432/apme`) | **Required** when Gateway persists data |
| `APME_DATABASE_MODE` | `bundled`, `external`, or `none` | **Required** — `none` only in proxy mode |
| `APME_GATEWAY_MODE` | `full` or `proxy` | **Required** |
| `APME_DB_PATH` | *(removed)* | **Dropped** — SQLite no longer supported |

### Portal Proxy Mode Note

When `APME_GATEWAY_MODE=proxy`, set `APME_DATABASE_MODE=none`. Gateway must not create, migrate, or connect to any APME schema. Portal owns `apme_projects`, `apme_scans`, `apme_galaxy_servers`, etc.

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
| ADR-055 (proposed) | ADR | Portal Gateway proxy mode (`APME_GATEWAY_MODE`) | A2 implementation | 3 |
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
| TASK-005-05 | TASK | Tarball `POST /scan` endpoint (A3) | §8 | 3 |
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
