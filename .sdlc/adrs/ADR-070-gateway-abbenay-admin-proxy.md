# ADR-070: Gateway HTTP Proxy to In-Pod Abbenay Admin (Simple Model)

## Status

Accepted

## Date

2026-08-03

## Context

Portal and other Gateway clients need to **configure** Abbenay (providers,
models, API keys) at runtime — not only select a model for remediation.
Today APME ships Abbenay with **deploy-time** `config.yaml` + env/Kubernetes secrets
and exposes only **gRPC** (`:50057`) for Engine inference (`list_models` /
`chat`). There is no Gateway path for Abbenay admin.

Abbenay already provides an HTTP admin/config API (`/api/config`,
`/api/providers`, `POST /api/provider/:id/configure`, …) on port **8787** when
its web/serve surface is running. APME does not start that HTTP listener today.

Constraints and drivers:

- **Simple topology** — The operator, Podman, and bootc co-locate engine + Gateway +
  optional Abbenay in one pod on localhost. Podman and bootc likewise share the
  network namespace. Loopback is the natural admin hop.
- **ADR-046** rejected Gateway → Abbenay for **LLM inference** (Engine remains
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
| 2. gRPC between backend services | Consistent — admin uses Abbenay’s existing HTTP API; inference stays gRPC via Engine |
| 5. Stateless engine / persistence at Gateway | Consistent — Abbenay remains config and secrets SoT; Gateway does not persist Abbenay config or provider keys |
| 11. Engine never queries out | Consistent — Gateway (not engine) reaches Abbenay admin |
| 16. Operator / Podman localhost | Consistent |
| 17. REST versioning (ADR-060) | Additive `/api/v1/ai/*` proxy mount |

**Amends ADR-046** Alternative 2 notes: rejection applies to **inference**, not
to Gateway HTTP reverse-proxy of Abbenay **admin**.

## Decision

**1. Simple model: Abbenay is in the APME pod.**  
For operator, Podman, and aligned local daemon layouts,
Abbenay is co-located with Gateway. Admin traffic uses localhost.

**2. Gateway reverse-proxies an allowlisted Abbenay HTTP admin API.**  
Mount additive routes under `/api/v1/ai/...` that forward **only** admin
operations to Abbenay `/api/...` (same method, query, and body for allowlisted
paths). Do **not** reimplement Abbenay admin schemas in Gateway Python/gRPC
clients. Do **not** proxy chat, sessions, OpenAI-compat `/v1`, or arbitrary
Abbenay surfaces (ADR-046 inference stays Engine). Reject path traversal
(`..` / encoded forms). Abbenay remains the source of truth for config.

Allowlist: `GET/POST /config`, `GET /engines`, `GET /providers`,
`POST /provider/{id}/configure`, `DELETE /provider/{id}`,
`GET/POST /secrets`, `DELETE /secrets/{key}`.

**3. Enable Abbenay HTTP on loopback when Abbenay is enabled.**  
In addition to gRPC `:50057` (Engine), start Abbenay’s HTTP admin surface on
**`127.0.0.1:8787`** (default Abbenay port). Do not publish a cluster Service
or hostPort for 8787 in the operator deployment.

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

**Caller trust (Simple):** Path allowlisting alone does not authenticate the
caller. That is intentional under ADR-048: every container in the
Simple pod (operator and Podman) shares one network namespace and is treated as a
trusted co-tenant of the product runtime. Evidence:

- **Operator** ([ansible/apme-operator](https://github.com/ansible/apme-operator)): Engine, Gateway, UI, optional
  Abbenay, and validators co-locate in one Pod; Abbenay HTTP
  has no Service or hostPort; only in-pod processes reach `127.0.0.1:8787`
  or Gateway `:8080` without an outer Ingress/NetworkPolicy edge.
- **Podman** (`containers/podman/pod.yaml`): same shared-netns all-in-one
  pod. For local dev, Abbenay HTTP may publish `hostPort: 8787` with
  `hostIP: 127.0.0.1` only (`http://127.0.0.1:8787`) with
  `ABBENAY_HTTP_AUTH=0` for passwordless dashboard access on localhost.
  Gateway `:8080` remains the sole published product REST edge for non-local
  access. The operator deployment has no hostPort for 8787.

A compromised sidecar that can already talk to Gateway can hit
`/api/v1/ai/*` and trigger the Gateway→Abbenay token rewrite — the same
privilege any co-located process has against other `/api/v1` routes. App-level
caller authorization on these routes is out of scope for Simple; introduce it
only with a new ADR that changes ADR-048.

**Transport (Simple):** Abbenay admin uses plain HTTP on
`http://127.0.0.1:8787`. Loopback is not transport confidentiality against a
compromised co-located process; it is a binding constraint so the token never
crosses a pod boundary. Deployment evidence: no cluster Service/hostPort for
8787 in the operator deployment; Podman may use `hostIP: 127.0.0.1` hostPort for localhost dev UI
only. Abbenay `--host 127.0.0.1` in operator/K8s; Podman dev uses `--host 0.0.0.0` with
localhost-only host binding. Gateway default
`APME_ABBENAY_HTTP_URL=http://127.0.0.1:8787`. Hardened hops (HTTPS+mTLS or a
permission-protected Unix socket) require a separate ADR; they are not part of
the accepted Simple design.

**5. Inference unchanged.**  
`GET /api/v1/ai/models` (Engine `ListAIModels`) and remediate `enable_ai` /
`ai_model` continue via Engine → Abbenay gRPC. The proxy does not replace that
path and rejects other methods on `models`.

**6. Config durability (implemented — [#498](https://github.com/ansible/apme/issues/498)).**  
Deploy-time providers seed a writable Abbenay config directory; runtime HTTP
admin writes persist there as the source of truth after first configure:

- **Operator**: ConfigMap or CR seed is mounted read-only. Init copies seed into the writable
  volume **once** (only if the file is absent). Default volume is `emptyDir`
  (pod lifetime). Optional PVC survives restarts. Abbenay mounts the writable dir at `/etc/abbenay-config`.
- **Podman**: Writable hostPath is
  `${XDG_CACHE_HOME:-$HOME/.cache}/apme/abbenay/config/` (override via
  `APME_CACHE_HOST_PATH`), mounted at `/home/abbenay/.config/abbenay`.
  `up.sh` seeds `config.yaml` from `containers/abbenay/config/` (or legacy
  `config.yaml` / `.example`) when the cache file is absent, applies
  `0700`/`0600`, then grants container UID 1001 access on the **cache copy**
  (rootful: chown; rootless: POSIX ACL so the host user can still edit). The
  git checkout is never chowned.

After the first successful configure, the writable file is SoT — operator CR /
ConfigMap changes do not overwrite an existing runtime config.

**7. Secrets source of truth remains Abbenay (not Gateway).**  
Runtime API keys injected via `POST /api/v1/ai/secrets` are stored by
Abbenay. Gateway reverse-proxies the secrets API and does **not** persist
provider keys. Durable keys in containers use Abbenay's filesystem store
(`secretStore: "file"`, Abbenay ≥ v2026.8.6), which writes
`<configDir>/secrets.json` (mode `0600` as written by Abbenay) on the same
writable volume as `config.yaml`. On macOS Podman Machine, virtiofs cannot
give container UID 1001 access without world-opening the file; `up.sh` does
not chmod `secrets.json`. File store on Darwin hostPath is unsupported until
[#562](https://github.com/ansible/apme/issues/562) (named volume or keep-id);
use env or memory. File-store keys survive a restart
**only** when that volume is durable:

- **Operator**: optional PVC for Abbenay config. Default is
  `emptyDir` — file-store keys then last for the **pod** lifetime only
  (survive Abbenay container restart; lost on pod recycle, drain, and upgrade).
- **Podman (Linux)**: RW host cache (survives `tox -e down`; `tox -e wipe`
  removes `secrets.json`). **macOS**: file store on the virtiofs hostPath is
  unsupported until [#562](https://github.com/ansible/apme/issues/562); use
  env or memory.

The process-lifetime `memory` store remains available. Deploy-time Kubernetes
Secrets / env (`secret_store: env`) are unchanged. DELETE must pass
`?secretStore=` (Abbenay defaults omitted store to **keychain**).

Rejected: a Gateway `ai_providers` database table as source of truth with
push-into-Abbenay-memory
([#560](https://github.com/ansible/apme/pull/560)). That inverts this
ADR's "Abbenay remains config SoT" (invariant 5) and makes Gateway a
secrets vault it is not designed to be. This amendment does **not**
implement #560's Portal CRUD / push-before-scan UX; operators who need
runtime keys to survive a pod recycle must enable the Abbenay PVC
(or keep using env / Kubernetes Secrets).

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

**Description**: Operators edit operator CR / `config.yaml` and redeploy; no
runtime admin API.

**Pros**:
- No new surface; secrets stay in cluster Secret workflows

**Cons**:
- Blocks Portal Quality-settings / admin UX for AI providers
- Slow feedback loop for EAP demos and day-2 model changes

**Why not chosen**: Product needs runtime admin through the Gateway edge.

### Alternative 4: Gateway database as secrets SoT, push to Abbenay memory

**Description**: Persist providers and API keys in Gateway DB; push into
Abbenay `secretStore: memory` before AI-enabled scans (proposed in
[#560](https://github.com/ansible/apme/pull/560)).

**Pros**:
- Survives Abbenay restart without a PVC
- Portal CRUD can live next to other Gateway settings

**Cons**:
- Gateway becomes a secrets vault (the Gateway database is not designed for that)
- Two sources of truth; push-before-scan races and restart windows
- Breaks "Abbenay remains config SoT" (this ADR / invariant 5)
- Memory store is still ephemeral in Abbenay; durability is only in Gateway

**Why not chosen**: Durable keys belong in Abbenay's file store on a
**durable** config volume (operator PVC / Podman cache), not in the Gateway database.
Gateway stays a proxy. Default operator `emptyDir` is still ephemeral — enable
`persistence.abbenay.enabled` when file-store keys must survive pod recycle.

## Consequences

### Positive

- Portal (and other clients) can configure Abbenay through existing Gateway
  reachability without a second public Service.
- Abbenay-native paths/bodies minimize future client churn if Abbenay moves out
  of the pod.
- Clear split: **admin** = Gateway HTTP proxy; **inference** = Engine gRPC
  (ADR-025 / ADR-046).

### Negative

- Must run Abbenay HTTP in the pod (image/args/config) in addition to gRPC —
  larger attack surface if mis-bound off loopback.
- Gateway must hold Abbenay HTTP token and keep proxy behavior correct
  (streaming, errors, path strip).
- OpenAPI will document a proxy mount rather than a fully owned schema
  (link to Abbenay config docs).

### Neutral

- `GET /api/v1/ai/models` stays Engine-mediated; Abbenay `/api/models` may exist
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
- **Deploy** (image ≥ v2026.8.0; Operator has no Service/hostPort for HTTP or gRPC):
  - **Operator**: `abbenay web --host 127.0.0.1 --port 8787 --grpc-host
    127.0.0.1 --grpc-port 50057` — see
    [apme-operator](https://github.com/ansible/apme-operator).
  - **Podman**: `abbenay web --host 0.0.0.0 --port 8787 --grpc-host 127.0.0.1
    --grpc-port 50057` with `hostIP: 127.0.0.1` hostPort so the host can reach
    the listener while the published port stays localhost-only.
- **Conflict**: `GET /api/v1/ai/models` remains Engine-backed; proxy excludes
  `models` for all methods. Register main router before the proxy mount.
- **OpenAPI**: proxy routes `include_in_schema=False` (Abbenay owns schemas);
  Gateway `info.description` references ADR-070.
- **Tests**: path rewrite, Bearer inject, Cookie strip, 502, models/chat not
  proxied, traversal rejected, missing token 503, Set-Cookie stripped; secrets
  GET/POST/DELETE proxy tests; operator manifest asserts ordered
  `--host`/`127.0.0.1`/`--port`/`8787` and Gateway HTTP URL.
- **Portal UI**: out of scope for the first implementation PR; catalog proxy +
  Quality settings follow in a later change.
- **Config durability** ([#498](https://github.com/ansible/apme/issues/498)):
  implemented — seed ConfigMap → writable emptyDir (default) / optional PVC
  (`persistence.abbenay`); Podman RW cache
  (`${XDG_CACHE_HOME:-$HOME/.cache}/apme/abbenay/config/`); seed-once;
  runtime SoT after first configure.
- **Secrets durability** (Abbenay ≥ v2026.8.6): `secretStore: "file"` writes
  `<configDir>/secrets.json` on that same volume. Durable only with operator
  `persistence.abbenay.enabled=true` or the Podman RW cache. Gateway does
  not parse `secretStore`. Do not persist provider keys in the Gateway database.
  DELETE requires `?secretStore=` (Abbenay defaults to keychain).

## Related Decisions

- [ADR-025](ADR-025-ai-provider-protocol.md): `AIProvider` / Engine-only
  `abbenay_grpc` for inference
- [ADR-046](ADR-046-ai-assisted-report-generation.md): Amended — Gateway must
  not call Abbenay for **chat/inference**; admin HTTP proxy is allowed
- [ADR-048](ADR-048-pod-internal-admin-endpoints.md): Network isolation for
  pod-internal admin surfaces
- [ADR-054](ADR-054-production-deployment.md) (ADR-069 superseded):
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
| 2026-08-04 | bthornto | §4: document Simple caller-trust + loopback token threat model (operator/Podman evidence) |
| 2026-08-13 | bthornto | Amended allowlist: added `GET/POST /secrets`, `DELETE /secrets/{key}` for Abbenay ≥ v2026.8.5 memory secret store |
| 2026-08-14 | bthornto | §7 secrets remain Abbenay SoT: file store (`secretStore: "file"`, ≥ v2026.8.6) on a durable config volume (operator PVC / Podman cache); Gateway stays proxy-only (rejects Gateway DB SoT, #560) |
