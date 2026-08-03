# ADR-070: Gateway HTTP Proxy to In-Pod Abbenay Admin (Simple Model)

## Status

Accepted

## Date

2026-08-03

## Context

Portal and other Gateway clients need to **configure** Abbenay (providers,
models, API keys) at runtime — not only select a model for remediation.
Today APME ships Abbenay with **deploy-time** `config.yaml` + env/Helm secrets
and exposes only **gRPC** (`:50057`) for Primary inference (`list_models` /
`chat`). There is no Gateway path for Abbenay admin.

Abbenay already provides an HTTP admin/config API (`/api/config`,
`/api/providers`, `POST /api/provider/:id/configure`, …) on port **8787** when
its web/serve surface is running. APME does not start that HTTP listener today.

Constraints and drivers:

- **ADR-069 Simple topology** — Helm EAP/upstream co-locates engine + Gateway +
  optional Abbenay in one pod on localhost. Podman and bootc likewise share the
  network namespace. Loopback is the natural admin hop.
- **ADR-046** rejected Gateway → Abbenay for **LLM inference** (Primary remains
  the sole `abbenay_grpc` chat / `ListAIModels` path). That rejection must not
  block a separate **admin** concern.
- **ADR-060** — Gateway REST under `/api/v1` is a versioned public contract;
  new admin access must be additive.
- **Future optionality** — Abbenay may later run outside the APME pod as a
  shared Portal resource. Prefer Abbenay-native HTTP shapes so a client can
  later change `baseUrl` without DTO rewrites. That future topology is **out of
  scope** for this ADR; this decision covers the Simple in-pod model only.
- **Security** — Abbenay HTTP must not be cluster-exposed. Gateway is the
  ingress; Abbenay binds loopback; NetworkPolicy / no Service port for 8787.

### Invariant consistency check

| Invariant | Status |
|-----------|--------|
| 1. Validators read-only | Consistent |
| 2. gRPC between backend services | Consistent — admin uses Abbenay’s existing HTTP API; inference stays gRPC via Primary |
| 5. Stateless engine / persistence at Gateway | Consistent — Abbenay remains config SoT; Gateway does not persist Abbenay config |
| 11. Engine never queries out | Consistent — Gateway (not engine) reaches Abbenay admin |
| 16. Helm Simple / Podman localhost | Consistent with ADR-069 |
| 17. REST versioning (ADR-060) | Additive `/api/v1/ai/*` proxy mount |

**Amends ADR-046** Alternative 2 notes: rejection applies to **inference**, not
to Gateway HTTP reverse-proxy of Abbenay **admin**.

## Decision

**1. Simple model: Abbenay is in the APME pod.**  
For EAP/upstream Helm (ADR-069), Podman, and aligned local daemon layouts,
Abbenay is co-located with Gateway. Admin traffic uses localhost.

**2. Gateway reverse-proxies an allowlisted Abbenay HTTP admin API.**  
Mount additive routes under `/api/v1/ai/...` that forward **only** admin
operations to Abbenay `/api/...` (same method, query, and body for allowlisted
paths). Do **not** reimplement Abbenay admin schemas in Gateway Python/gRPC
clients. Do **not** proxy chat, sessions, OpenAI-compat `/v1`, or arbitrary
Abbenay surfaces (ADR-046 inference stays Primary). Reject path traversal
(`..` / encoded forms). Abbenay remains the source of truth for config.

Allowlist (initial): `GET/POST /config`, `GET /providers`,
`POST /provider/{id}/configure`, `DELETE /provider/{id}`.

**3. Enable Abbenay HTTP on loopback when Abbenay is enabled.**  
In addition to gRPC `:50057` (Primary), start Abbenay’s HTTP admin surface on
**`127.0.0.1:8787`** (default Abbenay port). Do not publish a cluster Service
or hostPort for 8787 in the Simple chart.

**4. Auth rewrite at the Gateway.**  
Outbound to Abbenay, Gateway injects Abbenay’s HTTP Bearer token
(`ABBENAY_API_TOKEN` / configured server token). Strip inbound
`Authorization` and `Cookie`; do not forward `Set-Cookie`. Fail closed (503)
when no admin token is configured.

Gateway REST itself remains **network-isolation auth** (ADR-048 / no
app-level middleware on `:8080`) — the same trust model as other `/api/v1`
routes. Portal deployments put Backstage/catalog auth in front of the
Gateway. Elevating Abbenay admin onto that edge is intentional for Simple
EAP; operators must not expose Gateway `:8080` without an outer auth layer.

**5. Inference unchanged.**  
`GET /api/v1/ai/models` (Primary `ListAIModels`) and remediate `enable_ai` /
`ai_model` continue via Primary → Abbenay gRPC. The proxy does not replace that
path and rejects other methods on `models`.

**6. Config durability (acknowledged gap).**  
Helm ConfigMap / Podman hostPath seed deploy-time providers (often read-only).
Runtime HTTP admin writes need a writable Abbenay config directory. **Durable
writable persistence (emptyDir seed + PVC) is a follow-up** — tracked as a
GitHub issue — not a blocker for the allowlisted proxy plumbing.

**We will use an allowlisted HTTP reverse-proxy on the Gateway for in-pod
Abbenay admin, not a catch-all façade and not Gateway→Abbenay gRPC for chat.**

## Alternatives Considered

### Alternative 1: Gateway reimplements Abbenay admin via gRPC

**Description**: Gateway imports admin RPCs (`GetConfig`, `UpdateConfig`,
`ConfigureProvider`, …) and exposes hand-written REST DTOs.

**Pros**:
- No Abbenay HTTP listener required in the pod
- Gateway owns OpenAPI schemas explicitly

**Cons**:
- High drift vs Abbenay’s real HTTP API
- Second admin client stack (`abbenay_grpc` admin stubs) in Gateway
- Breaks cheap future Portal → Abbenay `baseUrl` swap

**Why not chosen**: Proxy preserves Abbenay shapes with far less code and drift.

### Alternative 2: Portal talks to Abbenay HTTP directly (even in-pod)

**Description**: Portal/browser or catalog backend reaches Abbenay `:8787`
without Gateway.

**Pros**:
- No Gateway proxy code

**Cons**:
- Exposes or tunnels Abbenay admin outside the APME auth boundary
- Conflicts with Simple “Gateway is the product REST edge” model
- Harder RBAC / audit at one place

**Why not chosen**: For the in-pod Simple model, Gateway remains the only
external admin ingress. Direct Portal → Abbenay is a **future** option when
Abbenay is a shared platform service (separate ADR).

### Alternative 3: Keep deploy-time-only Abbenay config (status quo)

**Description**: Operators edit Helm values / `config.yaml` and redeploy; no
runtime admin API.

**Pros**:
- No new surface; secrets stay in cluster Secret workflows

**Cons**:
- Blocks Portal Quality-settings / admin UX for AI providers
- Slow feedback loop for EAP demos and day-2 model changes

**Why not chosen**: Product needs runtime admin through the Gateway edge.

## Consequences

### Positive

- Portal (and other clients) can configure Abbenay through existing Gateway
  reachability without a second public Service.
- Abbenay-native paths/bodies minimize future client churn if Abbenay moves out
  of the pod.
- Clear split: **admin** = Gateway HTTP proxy; **inference** = Primary gRPC
  (ADR-025 / ADR-046).

### Negative

- Must run Abbenay HTTP in the pod (image/args/config) in addition to gRPC —
  larger attack surface if mis-bound off loopback.
- Gateway must hold Abbenay HTTP token and keep proxy behavior correct
  (streaming, errors, path strip).
- OpenAPI will document a proxy mount rather than a fully owned schema
  (link to Abbenay config docs).

### Neutral

- `GET /api/v1/ai/models` stays Primary-mediated; Abbenay `/api/models` may exist
  behind the proxy but is not the remediate UI contract unless explicitly
  switched later.
- External / multi-tenant Abbenay is deferred; this ADR does not design that
  topology.

## Implementation Notes

- **Path map (allowlisted)**: `/api/v1/ai/{path}` → `http://127.0.0.1:8787/api/{path}`  
  Examples: `/api/v1/ai/config` → `/api/config`;  
  `/api/v1/ai/provider/foo/configure` → `/api/provider/foo/configure`.  
  Reject unknown paths (including `chat`) and encoded `..` traversal.
- **Env**: e.g. `APME_ABBENAY_HTTP_URL` default `http://127.0.0.1:8787`;  
  `APME_ABBENAY_HTTP_TOKEN` (or shared secret with Abbenay `server.api_token_env`).
- **Deploy**: Helm Simple sidecar + Podman — `abbenay web --host 127.0.0.1
  --port 8787` plus gRPC flags; no Service port 8787; chart README notes ADR-070.
- **Conflict**: `GET /api/v1/ai/models` remains Primary-backed; proxy excludes
  `models` for all methods. Register main router before the proxy mount.
- **OpenAPI**: proxy routes `include_in_schema=False` (Abbenay owns schemas);
  Gateway `info.description` references ADR-070.
- **Tests**: path rewrite, Bearer inject, 502, models/chat not proxied,
  traversal rejected, missing token 503, Set-Cookie stripped; helm asserts
  `web` / `8787` / Gateway HTTP URL.
- **Portal UI**: out of scope for the first implementation PR; catalog proxy +
  Quality settings follow in a later change.
- **Follow-up**: writable Abbenay config volume (seed ConfigMap → emptyDir/PVC)
  so runtime admin survives restart —
  [#498](https://github.com/ansible/apme/issues/498).

## Related Decisions

- [ADR-025](ADR-025-ai-provider-protocol.md): `AIProvider` / Primary-only
  `abbenay_grpc` for inference
- [ADR-046](ADR-046-ai-assisted-report-generation.md): Amended — Gateway must
  not call Abbenay for **chat/inference**; admin HTTP proxy is allowed
- [ADR-048](ADR-048-pod-internal-admin-endpoints.md): Network isolation for
  pod-internal admin surfaces
- [ADR-054](ADR-054-production-deployment.md) / [ADR-069](ADR-069-helm-simple-all-in-one.md):
  Simple all-in-one localhost topology
- [ADR-060](ADR-060-rest-api-versioning-contract.md): Additive `/api/v1` routes

## References

- Abbenay configuration / HTTP API: upstream Abbenay `docs/CONFIGURATION.md`
  (`GET/POST /api/config`, providers, Bearer `ABBENAY_API_TOKEN`, port 8787)
- Portal follow-up: Quality settings Abbenay config UI (deferred)

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-08-03 | cidrblock | Accepted — Simple in-pod Abbenay; Gateway HTTP admin proxy |
