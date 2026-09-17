# ADR-071: Pip Index Egress via Galaxy Proxy

## Status

Proposed

## Date

2026-09-16

## Context

Session venvs install Ansible collections through the Galaxy Proxy (ADR-031)
using `uv pip install --extra-index-url http://galaxy-proxy:8765/simple/`.
Transitive **Python** dependencies still resolve against the default public
PyPI index because `uv`/`pip` treat PyPI as the primary index whenever only
`--extra-index-url` is set.

Galaxy Proxy already supports a single `--pypi-url` passthrough for
non-collection Simple API requests, and Gateway already persists Galaxy /
Automation Hub servers and pushes them to the proxy (ADR-045,
`POST /admin/galaxy-config`, ADR-048). There is no equivalent for pip
indexes, and the Engine still has a direct egress path to public PyPI.

Enterprise and air-gapped deployments need configurable, possibly multiple,
pip mirrors (Artifactory, Nexus, internal simple indexes) without inventing
a product-level "air-gap mode." Isolation must be configuration: if nothing
is configured, attempt the public default; if the network cannot reach it,
installs fail naturally.

### Forces

- One pod egress for package downloads is easier to reason about (CA trust,
  logging, allowlists) than Engine→PyPI plus Proxy→Galaxy/PyPI.
- Gateway already owns durable config for Galaxy servers; pip indexes should
  follow the same lifecycle.
- Architectural invariant 11: the engine never queries external systems; the
  Galaxy Proxy is already the designated outbound download service for
  collections.
- ADR-031's `--extra-index-url` + `unsafe-best-match` strategy was chosen
  because transitive deps lived on PyPI while collection wheels lived only
  on the proxy — that split goes away if the proxy is the sole index.

### Constraints

- Engine must not import Gateway or read the Gateway DB.
- Pip-index admin sync requires Gateway co-located in the same pod as
  Galaxy Proxy (ADR-048, ADR-069 Simple all-in-one). ADR-029's cross-pod
  Gateway placement has no route to pod-local proxy admin endpoints
  (`127.0.0.1:8765`); split or external Gateway topologies are
  **unsupported** for `/admin/pip-indexes` until a future ADR defines
  cross-pod admin routing, authentication, and encryption.
- Gateway → Proxy admin traffic remains unauthenticated HTTP on the pod
  localhost network (ADR-048), including credential-bearing pushes to
  `/admin/galaxy-config` and `/admin/pip-indexes`. Cleartext in-transit
  within the pod is an accepted trade-off under the current co-located
  topology; TLS/mTLS or shared-secret auth is required before any topology
  change that exposes those endpoints beyond localhost (ADR-048). At-rest
  credential encryption follows the ADR-045 follow-up.
- New Gateway REST endpoints must be additive under `/api/v1` (ADR-060).
- Credential storage in the Gateway DB may remain plaintext initially (same
  follow-up as ADR-045 token encryption).
- Upstream URLs that include credentials must use `https://`. Gateway CRUD
  and the proxy admin endpoint reject credentialed `http://` URLs. Unauthenticated
  `http://` upstreams are allowed only for explicitly trusted-local targets
  (e.g. loopback); credentials are prohibited on those exceptions.
- Credentialed pip upstream passthrough must not leak credentials across
  redirects: follow redirects only while scheme stays `https://` and origin
  is unchanged from the configured upstream URL; strip credentials before any
  scheme or origin change and fail closed rather than send them cross-origin.
  Unauthenticated pip upstreams may retain today's redirect behavior. Galaxy
  collection download redirects remain unchanged (ADR-031/045).
- No dedicated air-gap feature flag.

## Decision

**We will make Galaxy Proxy the Engine's sole pip Simple API index, and
manage ordered pip index upstreams (URLs + optional credentials) in the
Gateway DB with the same push-to-proxy pattern used for Galaxy servers.**

Specifically:

1. **Engine install path** — `uv pip install` / `pip install` uses
   `--index-url http://<galaxy-proxy>/simple/` only (not
   `--extra-index-url` against an implicit public PyPI). Collections and
   Python packages both resolve through the proxy.
2. **Multiple upstream indexes** — Gateway persists an ordered list of pip
   indexes (name, URL, optional auth). On startup and after CRUD, the
   co-located Gateway pushes the list to Galaxy Proxy (new admin endpoint,
   e.g. `POST /admin/pip-indexes`) and requires an acknowledgment. Failed
   or unavailable pushes are retried with backoff. Gateway tracks
   **unsynchronized** state separately from an **explicitly empty**
   synchronized configuration; only the latter uses public PyPI passthrough.
   While Gateway holds a **non-empty** desired list that is
   **unsynchronized**, Galaxy Proxy serves only the last **acknowledged**
   non-empty list if one exists; otherwise Proxy reports not ready and
   Engine venv installs fail closed — public PyPI passthrough is prohibited
   in this interval. Proxy restarts trigger re-push on the next successful
   health/retry cycle.
3. **Proxy passthrough** — For non-collection packages, the proxy queries
   configured upstreams in **first-hit** order (try each upstream in
   priority order; use the first successful Simple API response; do not
   merge listings across upstreams) and applies credentials per upstream.
   Credentialed upstream requests follow the redirect constraint above;
   Galaxy collection download redirects are out of scope and unchanged.
   Empty list → keep today's default passthrough to `https://pypi.org`.
4. **Runtime application** — Proxy applies upstream config in-process for
   HTTP passthrough and injects env where subprocesses need credentials
   (same idea as `_inject_galaxy_env` for Galaxy).
5. **Transport security** — Enforce the HTTPS constraint above in Gateway
   CRUD validation and the proxy admin endpoint before persisting or
   applying upstream config.
6. **No air-gap mode** — Do not add `APME_AIRGAP` (or equivalent). Operators
   who need isolation configure private indexes and private Galaxy/AH only;
   unreachable defaults simply fail installs.

This amends the Engine install invocation described in ADR-031 (primary
index becomes the proxy). It does not supersede ADR-031's collection
wheel/cache model, ADR-045's Galaxy auth delegation, or ADR-048's
pod-internal admin posture.

## Alternatives Considered

### Alternative 1: Engine-side multi `--index-url` / `--extra-index-url` env vars

**Description**: Configure pip indexes on the Engine via environment
variables; Galaxy Proxy remains collections-only; Python deps bypass the
proxy.

**Pros**:
- Smaller proxy change
- Familiar pip/uv knobs

**Cons**:
- Two egress paths (Engine→indexes and Proxy→Galaxy/PyPI)
- Duplicates Gateway-owned Galaxy config pattern
- Weaker fit for invariant 11

**Why not chosen**: Leaves uncontrolled Engine egress and splits config
ownership.

### Alternative 2: Explicit air-gap mode flag

**Description**: `APME_AIRGAP=true` disables public defaults and fails
closed unless private indexes are configured.

**Pros**:
- Obvious operator signal

**Cons**:
- Redundant with "configure private indexes only"
- Extra branching in health checks and docs

**Why not chosen**: Isolation is configuration, not a product mode.

### Alternative 3: Do nothing

**Description**: Keep implicit public PyPI + proxy `--extra-index-url`.

**Pros**:
- No work

**Cons**:
- Blocks mirror-only and air-gapped deployments
- Preserves dependency-confusion surface of sibling indexes

**Why not chosen**: Does not meet the requirement.

## Consequences

### Positive

- Single pip egress point (Galaxy Proxy) for session venv installs.
- Multiple private mirrors and Hub-only topologies use one config model.
- Strengthens invariant 11 for package resolution.
- Reuses Gateway DB → proxy admin sync (ADR-045/048).

### Negative

- Proxy must implement multi-upstream Simple API passthrough (ordering,
  auth headers, caching) correctly.
- Index-strategy defaults change once PyPI is no longer a sibling Engine
  index — revisit `APME_UV_INDEX_STRATEGY` / `unsafe-best-match` guidance.
- Plaintext tokens in the Gateway DB inherit the ADR-045 encryption follow-up.

### Neutral

- Empty pip-index config preserves today's public PyPI behavior via proxy
  passthrough (behaviorally similar for online defaults; path differs).
- Unsynchronized proxy state is visible in health/readiness until the proxy
  acknowledges the latest push; non-empty unsynchronized intervals fail
  closed or serve the last acknowledged private list — never silent PyPI
  fallback.
- Collection download path (ansible-galaxy via ADR-045) is unchanged;
  only Python-package passthrough and Engine install flags change.

## Implementation Notes

- Gateway: table + REST CRUD analogous to `galaxy_servers` (ordered
  `priority`/`position` field). Additive `/api/v1` routes only (ADR-060).
- Gateway: extend `_galaxy_proxy_sync` (or sibling module) to push pip
  indexes on startup and after CRUD with acknowledgment, retry/backoff,
  and unsynchronized vs empty-config tracking. Credential payloads use the
  same pod-local HTTP admin channel as Galaxy server tokens (ADR-048).
- Gateway / proxy validation: reject credentialed `http://` upstream URLs;
  allow unauthenticated `http://` only for trusted-local targets.
- Galaxy Proxy: admin endpoint to replace in-memory upstream list;
  first-hit multi-index passthrough for `/simple/{pkg}/` (ordered try per
  upstream priority; no cross-upstream merge). For credentialed upstreams,
  enforce same-origin HTTPS redirect handling per the Constraints section;
  do not reuse `PyPIPassthrough`'s unconditional `follow_redirects=True`
  for authenticated requests.
- Engine `venv_manager.session._run_pip_install`: switch to `--index-url`
  pointing at `APME_GALAXY_PROXY_URL/simple/`; drop reliance on implicit
  public PyPI as primary index.
- Update `docs/guides/DEPLOYMENT.md` index-strategy section after the
  sibling-index model is gone.
- Helm / Podman: no new air-gap flag; document configuring pip indexes via
  Gateway API / UI when implemented. Co-located Gateway is required (ADR-069
  Simple all-in-one); split Gateway topologies defer pip-index sync.
- Follow-up REQ/TASK for implementation (not in DR-022 action scope).

## Related Decisions

- ADR-031: Unified Collection Cache (amended: Engine install uses proxy as
  `--index-url`)
- ADR-045: Galaxy auth delegation; Gateway DB → proxy config push
- ADR-048: Pod-internal admin endpoints
- ADR-029: Web gateway architecture (persistence at edge)
- ADR-060: REST API versioning contract (additive endpoints)
- DR-022: Pip Index Egress via Galaxy Proxy (decided)

## References

- [.sdlc/decisions/closed/decided/DR-022-pip-index-egress.md](../decisions/closed/decided/DR-022-pip-index-egress.md)
- [docs/guides/DEPLOYMENT.md](../../docs/guides/DEPLOYMENT.md) — Galaxy Proxy index strategy

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-09-16 | User / agent | Initial proposal from DR-022 |
| 2026-09-17 | Agent | HTTPS for credentialed upstreams; recoverable sync; first-hit merge policy |
| 2026-09-17 | Agent | Document credential-bearing admin sync inherits ADR-048 pod-local HTTP |
| 2026-09-17 | Agent | Define credentialed pip upstream redirect handling (same-origin HTTPS) |
| 2026-09-17 | Agent | Scope admin sync to co-located Gateway; define unsynchronized fail-safe |
