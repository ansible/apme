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
| 5. Stateless engine / persistence at Gateway | Consistent — Abbenay remains config and secrets SoT; Gateway does not persist Abbenay config or provider keys |
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

Allowlist: `GET/POST /config`, `GET /engines`, `GET /providers`,
`POST /provider/{id}/configure`, `DELETE /provider/{id}`,
`GET/POST /secrets`, `DELETE /secrets/{key}`.

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

**6. Config durability (implemented — [#498](https://github.com/ansible/apme/issues/498)).**  
Deploy-time providers seed a writable Abbenay config directory; runtime HTTP
admin writes persist there as the source of truth after first configure:

- **Helm**: ConfigMap (`*-abbenay-config`) is mounted read-only as a seed.
  Init container `init-abbenay-config` copies `config.yaml` into the writable
  volume **once** (only if the file is absent). Default volume is `emptyDir`
  (pod lifetime). Optional PVC via `persistence.abbenay.enabled=true` survives
  restarts. Abbenay mounts the writable dir at `/etc/abbenay-config`.
- **Podman**: Writable hostPath is
  `${XDG_CACHE_HOME:-$HOME/.cache}/apme/abbenay/config/` (override via
  `APME_CACHE_HOST_PATH`), mounted at `/home/abbenay/.config/abbenay`.
  `up.sh` seeds `config.yaml` from `containers/abbenay/config/` (or legacy
  `config.yaml` / `.example`) when the cache file is absent, applies
  `0700`/`0600`, then grants container UID 1001 access on the **cache copy**
  (rootful: chown; rootless: POSIX ACL so the host user can still edit). The
  git checkout is never chowned.

After the first successful configure, the writable file is SoT — Helm value /
ConfigMap changes do not overwrite an existing runtime config.

**7. Secrets source of truth remains Abbenay (not Gateway).**  
Runtime API keys injected via `POST /api/v1/ai/secrets` are stored by
Abbenay. Gateway reverse-proxies the secrets API and does **not** persist
provider keys. Durable keys in containers use Abbenay's filesystem store
(`secretStore: "file"`, Abbenay ≥ v2026.8.6), which writes
`<configDir>/secrets.json` (mode `0600` as written by Abbenay) on the same
writable volume as `config.yaml`. On macOS Podman Machine, `up.sh` may set
the file to `0644` so virtiofs can map container UID 1001 — treat the host
cache directory as secret material. File-store keys survive a restart
**only** when that volume is durable:

- **Helm**: `persistence.abbenay.enabled=true` (PVC). The chart default is
  `emptyDir` — file-store keys then last for the **pod** lifetime only
  (survive Abbenay container restart; lost on pod recycle, drain, and Helm
  upgrade with `Recreate`).
- **Podman**: RW host cache (survives `tox -e down`; `tox -e wipe` removes
  `secrets.json`).

The process-lifetime `memory` store remains available. Deploy-time Helm
Secrets / env (`secret_store: env`) are unchanged. DELETE must pass
`?secretStore=` (Abbenay defaults omitted store to **keychain**).

Rejected: a Gateway `ai_providers` SQLite table as source of truth with
push-into-Abbenay-memory
([#560](https://github.com/ansible/apme/pull/560)). That inverts this
ADR's "Abbenay remains config SoT" (invariant 5) and makes Gateway a
secrets vault it is not designed to be. This amendment does **not**
implement #560's Portal CRUD / push-before-scan UX; operators who need
runtime keys to survive a Helm pod recycle must enable the Abbenay PVC
(or keep using env / Helm Secrets).

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

### Alternative 4: Gateway SQLite as secrets SoT, push to Abbenay memory

**Description**: Persist providers and API keys in Gateway DB; push into
Abbenay `secretStore: memory` before AI-enabled scans (proposed in
[#560](https://github.com/ansible/apme/pull/560)).

**Pros**:
- Survives Abbenay restart without a PVC
- Portal CRUD can live next to other Gateway settings

**Cons**:
- Gateway becomes a secrets vault (SQLite is not designed for that)
- Two sources of truth; push-before-scan races and restart windows
- Breaks "Abbenay remains config SoT" (this ADR / invariant 5)
- Memory store is still ephemeral in Abbenay; durability is only in Gateway

**Why not chosen**: Durable keys belong in Abbenay's file store on a
**durable** config volume (Helm PVC / Podman cache), not in Gateway SQLite.
Gateway stays a proxy. Default Helm `emptyDir` is still ephemeral — enable
`persistence.abbenay.enabled` when file-store keys must survive pod recycle.

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
  --port 8787 --grpc-host 127.0.0.1 --grpc-port 50057` (image ≥ v2026.8.0);
  no Service/hostPort for HTTP or gRPC; chart README notes ADR-070. Both
  topologies bind Abbenay to loopback (pod-shared netns).
- **Conflict**: `GET /api/v1/ai/models` remains Primary-backed; proxy excludes
  `models` for all methods. Register main router before the proxy mount.
- **OpenAPI**: proxy routes `include_in_schema=False` (Abbenay owns schemas);
  Gateway `info.description` references ADR-070.
- **Tests**: path rewrite, Bearer inject, Cookie strip, 502, models/chat not
  proxied, traversal rejected, missing token 503, Set-Cookie stripped; secrets
  GET/POST/DELETE proxy tests; helm asserts ordered
  `--host`/`127.0.0.1`/`--port`/`8787` and Gateway HTTP URL.
- **Portal UI**: out of scope for the first implementation PR; catalog proxy +
  Quality settings follow in a later change.
- **Config durability** ([#498](https://github.com/ansible/apme/issues/498)):
  implemented — seed ConfigMap → writable emptyDir (default) / optional PVC
  (`persistence.abbenay`); Podman RW cache
  (`${XDG_CACHE_HOME:-$HOME/.cache}/apme/abbenay/config/`); seed-once;
  runtime SoT after first configure.
- **Secrets durability** (Abbenay ≥ v2026.8.6): `secretStore: "file"` writes
  `<configDir>/secrets.json` on that same volume. Durable only with Helm
  `persistence.abbenay.enabled=true` or the Podman RW cache. Gateway does
  not parse `secretStore`. Do not persist provider keys in Gateway SQLite.
  DELETE requires `?secretStore=` (Abbenay defaults to keychain).

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
| 2026-08-03 | bthornto | Amended allowlist: added `GET /engines` for read-only engine discovery |
| 2026-08-03 | bthornto | §6 Config durability implemented (#498): seed→RW emptyDir/PVC; Podman RW config dir |
| 2026-08-13 | bthornto | Amended allowlist: added `GET/POST /secrets`, `DELETE /secrets/{key}` for Abbenay ≥ v2026.8.5 memory secret store |
| 2026-08-14 | bthornto | §7 secrets remain Abbenay SoT: file store (`secretStore: "file"`, ≥ v2026.8.6) on a durable config volume (Helm PVC / Podman cache); Gateway stays proxy-only (rejects Gateway DB SoT, #560) |
