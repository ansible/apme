# APME Productization Plan — Pre-Read

**Prepared:** 2026-06-24  
**Target discussion:** Friday architecture review  
**Status:** Draft for review

---

## 1. Architecture Overview

APME is a multi-service system that automates policy enforcement and
modernization of Ansible content for AAP 2.5+. The architecture separates
concerns into three tiers:

```
┌──────────────────────────────── apme-pod ─────────────────────────────┐
│ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│ │ Primary  │ │ Native   │ │   OPA    │ │ Ansible  │ │ Gitleaks │ │
│ │  :50051  │ │  :50055  │ │  :50054  │ │  :50053  │ │  :50056  │ │
│ └────┬─────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ │
│      ┌────┴─────────────────────────────────────┐  ┌──────────┐     │
│      │    Galaxy Proxy :8765 (PEP 503)          │  │ Abbenay  │     │
│      └──────────────────────────────────────────┘  │  :50057  │     │
│        ┌──────────────────────┐  ┌──────────┐      └──────────┘     │
│        │ Gateway :50060/:8080 │  │ UI :8081 │                       │
│        │  REST + gRPC + DB   │  │ (nginx)  │                       │
│        └──────────────────────┘  └──────────┘                       │
└──────────────────────────────────────────────────────────────────────┘
```

- **Engine tier:** Primary orchestrator + validators (Native, OPA, Ansible,
  Gitleaks, Collection Health, Dep Audit) + Galaxy Proxy. Scales as a unit
  (ADR-012). Stateless — no database code.
- **Gateway tier:** FastAPI REST + gRPC Reporting server + SQLAlchemy/SQLite
  persistence. Bridges HTTP/WebSocket clients to the engine's gRPC interface.
  Owns all persistence and context enrichment (ADR-020, ADR-029).
- **UI tier:** React SPA served by nginx. Talks exclusively to the Gateway
  REST API.

All inter-service communication uses gRPC (ADR-001). The only HTTP endpoints
are Gateway REST (:8080), Galaxy Proxy PEP 503 (:8765), and the UI (:8081).

---

## 2. Deployment: Portal-First, Then Expand

The same services run in every deployment target — only the orchestration
and networking differ.

| Target | Method | Status |
|--------|--------|--------|
| **Portal / OpenShift** (primary) | Helm chart (`deploy/helm/apme/`) | Structurally ready (ADR-054); needs auth, ingress/route, network policy |
| Podman pod (dev, standalone, GitHub) | `tox -e up` | Working today |
| bootc VM (air-gapped / edge) | Systemd quadlets (`deploy/bootc/`) | Prototype exists |
| CLI daemon (quick eval / CI) | `apme daemon start` | Working; embedded Gateway (ADR-049) |

### Helm on OpenShift (Portal path)

The Helm chart produces separate Deployments:

| Deployment | Contents | Scaling |
|------------|----------|---------|
| `engine` | Primary + all validators + Galaxy Proxy (sidecars) | HPA optional (1–5 replicas) |
| `gateway` | REST + gRPC Reporting + SQLite | Fixed replicas |
| `ui` | nginx + React SPA | Fixed replicas |
| `abbenay` | AI provider | Optional, 1 replica |

Templates for Ingress, OpenShift Route, NetworkPolicy, and PodDisruptionBudget
exist but are disabled by default — enable for production.

### Why one architecture serves all use cases

A Podman pod is the same services on localhost. It satisfies the standalone
web UI and GitHub-integrated use cases without a separate product SKU or
different code path. The CLI daemon embeds the Gateway for zero-dependency
local evaluation.

---

## 3. Authentication — Open Decision Required

**There is no ADR that implements inbound API authentication.** This is the
single largest productization gap.

### What exists today

| ADR | Auth relevance | Status |
|-----|----------------|--------|
| ADR-029 | Standalone V1: no auth. Enterprise: trust identity headers (`X-User`, `X-Org`) from AAP Gateway proxy | Implemented (no auth code) |
| ADR-038 | Three modes: no auth, Bearer token (machine), enterprise via AAP headers | Proposed only |
| ADR-048 | Pod-internal admin endpoints rely on network isolation | Accepted |
| ADR-054 | K8s Secrets for SCM/AI tokens (outbound creds, not user auth) | Accepted |

### Open question: AAP Gateway proxy vs self-contained auth

**Option A — AAP Gateway proxy (ADR-029 sketch):**
APME sits behind the AAP Gateway, which handles OAuth2/OIDC/RBAC. APME
trusts `X-User` / `X-Org` headers. Minimal APME code change; depends on
AAP Gateway being in the request path.

**Option B — OAuth2/OIDC middleware in APME Gateway:**
APME validates JWTs directly (e.g., from platform Keycloak). Self-contained
but duplicates auth infrastructure.

**Option C — Platform auth token validation:**
Portal provides a signed JWT; APME validates it against a shared JWKS
endpoint. Middle ground — APME verifies tokens but doesn't own the IdP.

**Recommendation:** Write an ADR committing to one approach before any
multi-user deployment. Option A aligns with existing ADR-029 direction and
avoids APME owning identity management.

---

## 4. Productization Work Streams

### 4.1 Downstream Build & Release

| Item | Current State | Work Needed |
|------|---------------|-------------|
| Container images | 13 images on `ghcr.io`; git-SHA tags | Konflux pipelines; `registry.redhat.io` target; evaluate UBI base |
| Versioning | `0.1.0` in pyproject; chart `0.1.0`; no unified semver | Align pyproject, Helm `appVersion`, and image tags to single semver |
| Release SBOM | Per-project CycloneDX via Gateway API (runtime) | Release-artifact SBOM via syft/cyclonedx-bom in pipeline |
| Image signing | Not implemented | cosign in Konflux pipeline |
| Base image | `astral-sh/uv:python3.12-bookworm-slim` | Evaluate UBI 9 for downstream; uv still usable on UBI |

### 4.2 Security & Compliance

| Item | Current State | Work Needed |
|------|---------------|-------------|
| SAST | Manual bandit; no CodeQL | CodeQL or Snyk in CI; gitleaks + pip-audit in pipeline |
| Container scanning | Manual Trivy/Grype (documented, not automated) | Konflux Clair/Trivy gate on image build |
| Non-root containers | Documented in SECURITY.md; `USER` directive missing from Dockerfiles | Add non-root USER to all Containerfiles |
| Dependency governance | ADR-019 checklist; Dependabot weekly | Formal dep review for legal (license audit) |
| TLS | Insecure channels in dev; TLS noted for prod | mTLS or cert-manager for K8s |
| Network policy | Helm template exists, disabled by default | Enable by default for production values |

### 4.3 Authentication & Authorization

See [Section 3](#3-authentication--open-decision-required). Requires ADR
decision. Work includes:

- Middleware implementation in Gateway (`FastAPI Depends()`)
- RBAC model if multi-tenant (or defer to AAP Gateway)
- API key management for machine consumers (ADR-038)

### 4.4 Scaling & Performance

| Item | Current State | Work Needed |
|------|---------------|-------------|
| Load testing | No infrastructure | k6 or locust suite against Gateway REST + FixSession gRPC |
| Baseline metrics | Per-scan diagnostics (`engine_total_ms`, per-rule timing) | Establish SLA baselines (e.g., 1000-task project < 30s) |
| HPA | Helm template exists (disabled) | Enable and tune with load test data |
| Horizontal scaling | ADR-012: scale engine pods as unit | Validate with concurrent scan load |
| Gateway DB | SQLite (single-writer) | PostgreSQL path (ADR-029) needed for HA / multi-replica |

### 4.5 Ownership Model

#### DevTools team — platform engineering

The DevTools team will own the APME platform codebase: engine, gateway,
validators (framework and execution), deployment artifacts, CI/CD, and
release process. They own the **how** — the machinery that loads projects,
runs validators, serves results, and deploys.

| Item | Work Needed |
|------|-------------|
| Codebase walkthrough | Architecture, service boundaries, ADR index, test strategy |
| Bug triage integration | APME components in team's Jira/Bugzilla; integrate into existing triage cadence |
| CODEOWNERS | Assign DevTools as owners for `src/apme_engine/`, `src/apme_gateway/`, validators (framework), proto, `deploy/`, CI |
| CI ownership | Team understands and can modify GitHub Actions workflows, tox environments |
| Development workflow | Team comfortable with branch strategy, PR process, conventional commits |
| Contribution ramp | First bugs/features assigned to build familiarity before full ownership |
| Release process | Team owns version bumps, CHANGELOG, tagging, image publishing |

#### Rules & policy — community of practice

The prescriptive ruleset (what APME checks and why) must be owned
collectively by people closer to the users — the community of practice,
the business unit, and the broader Ansible engineering team. DevTools
maintains the validator framework and rule execution machinery, but does
not unilaterally author policy rules.

This is a pattern we've used before and it needs a formal governance model:

| Item | Work Needed |
|------|-------------|
| Rule governance model | Define who can propose, review, approve, and merge new rules |
| Rule ownership by category | Map L/M/R/P/SEC categories to responsible groups (e.g., Risk/Policy rules owned by platform team, Lint/Modernize by community) |
| Contribution workflow | External rule contributors follow a defined process: proposal → review by domain experts → implementation by contributor or DevTools → merge |
| Rule review board | Standing group (community of practice + BU + Ansible engineering) that reviews rule proposals, severity assignments, and deprecations |
| Plugin boundary (ADR-042) | Third-party/org-specific rules go through the Plugin service — not into the built-in ruleset — giving teams autonomy without gating on DevTools |
| Rule catalog maintenance | Published catalog with rationale, references, and ownership metadata per rule |
| Feedback loop | Portal/CLI users can flag false positives or request new rules; routed to rule owners, not DevTools backlog |

### 4.6 Dependency Review

| Category | Current Dependencies | Review Action |
|----------|---------------------|---------------|
| Python runtime | grpcio, protobuf, fastapi, uvicorn, ruamel.yaml, httpx, ansible-core | License audit; legal review |
| External binaries | OPA 1.17.1, Gitleaks 8.30.1 | License compatibility; upstream support lifecycle |
| AI provider | Abbenay (`ghcr.io/redhat-developer/abbenay`) | Internal dependency; version pinning strategy |
| Frontend | React, PatternFly, Vite, Node 22 | Standard UI stack; confirm PatternFly version |
| Container base | Debian bookworm-slim | UBI migration path for downstream |

### 4.7 Repo & Process Hardening

| Item | Current State | Work Needed |
|------|---------------|-------------|
| CI security gates | Unit + integration + lint; no SAST/container scan | Add CodeQL, pip-audit, container scan to CI |
| Pre-commit hooks | ruff, mypy, pydoclint, uv-lock | Add gitleaks to pre-commit (currently manual) |
| Branch protection | Single-branch `main` (ADR-016) | Verify CODEOWNERS, required reviews, status checks |
| Vulnerability disclosure | SECURITY.md with private reporting | Confirm GitHub Security Advisories enabled |
| Industry gap analysis | `.sdlc/research/industry-gap-analysis.md` exists | Use as checklist; close gaps systematically |

### 4.8 Product Requirements from Craig

Craig should author a formal requirement covering:

- What "APME in Portal" means from a product owner perspective
- User personas and workflows in Portal context
- Which APME capabilities are P0 vs P1 for Portal launch
- Integration points with existing Portal services
- Success metrics and acceptance criteria
- Use `/req-new` or `/prd-import` to formalize as SDLC artifact

---

## 5. Additional Considerations

Items not in the original list but important for productization:

1. **Observability** — No Prometheus metrics endpoint, no OpenTelemetry
   traces, no structured log aggregation. Production needs `/metrics` on
   Gateway and engine.

2. **Rate limiting** — Gateway has no request throttling. If exposed through
   Portal, needs abuse protection.

3. **Multi-tenancy / data isolation** — SQLite is single-tenant. Portal
   implies multiple users/orgs. Decision needed: one APME instance per
   tenant, or shared instance with row-level isolation (requires
   PostgreSQL).

4. **Database migrations** — No Alembic or migration tooling. Schema changes
   will break existing databases without a migration path.

5. **Operator documentation** — Admin guide, configuration reference,
   troubleshooting guide. Current docs are developer-focused.

6. **Feature flags** — No feature flag system. Portal rollout likely needs
   staged enablement.

7. **Graceful degradation** — What happens when APME engine is down? Portal
   needs a degraded UX, not a 500.

8. **API versioning contract** — REST API is `/api/v1` but no formal
   stability guarantee or deprecation policy. Portal consumers need this.

9. **Accessibility (a11y)** — PatternFly helps, but no a11y audit has been
   performed.

10. **Telemetry** — No opt-in usage analytics for understanding adoption.

---

## 6. Open Decisions

| # | Decision | Owner | Blocker for |
|---|----------|-------|-------------|
| 1 | Auth model: AAP proxy vs self-contained vs platform JWT | Architect + Craig | Any multi-user deployment |
| 2 | Multi-tenancy: per-tenant instance vs shared + row isolation | Architect | Portal deployment |
| 3 | Base image: stay Debian slim or migrate to UBI | DevTools / Konflux | Downstream build |
| 4 | PostgreSQL: required for production or SQLite sufficient? | Architect | HA / multi-replica Gateway |
| 5 | Craig's P0 feature set for Portal launch | Craig | Scope and timeline |
| 6 | Rule governance: who proposes, reviews, approves prescriptive rules | Community of practice + BU + Ansible eng | Rule quality and relevance |
| 7 | DevTools ownership scope: engine + gateway + deploy + CI + release | DevTools lead + current team | Codebase handoff |

---

## 7. Suggested Priority Sequence

```
Phase 1 — Security & Auth Foundation
├── Auth ADR decision
├── Non-root containers
├── CI security gates (CodeQL, pip-audit, container scan)
├── Dependency / license audit
└── TLS for production

Phase 2 — Downstream Build
├── UBI base image evaluation
├── Konflux pipeline setup
├── Unified semver strategy
├── Release SBOM + cosign
└── Image publish to registry.redhat.io

Phase 3 — Portal Integration
├── Auth middleware implementation
├── Ingress / Route configuration
├── Network policy defaults
├── PostgreSQL support (if multi-tenant)
├── Craig's P0 feature set
└── API stability contract

Phase 4 — Production Readiness & Handoff
├── Load test suite (k6/locust)
├── SLA baselines and HPA tuning
├── Prometheus metrics + Grafana
├── a11y audit
├── DevTools codebase walkthrough and first contributions
├── CODEOWNERS + triage integration for DevTools ownership
└── Rule governance model: community of practice + BU + Ansible eng
```

---

## References

| Document | Path |
|----------|------|
| Architecture | `.sdlc/context/architecture.md` |
| Deployment | `.sdlc/context/deployment.md` |
| Helm chart | `deploy/helm/apme/` |
| ADR index | `.sdlc/adrs/README.md` |
| Security policy | `SECURITY.md` |
| Operating procedures | `SOP.md` |
| Industry gap analysis | `.sdlc/research/industry-gap-analysis.md` |
