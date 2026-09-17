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
- Gateway → Proxy admin HTTP remains pod-internal (ADR-048).
- New Gateway REST endpoints must be additive under `/api/v1` (ADR-060).
- Credential storage in SQLite may remain plaintext initially (same follow-up
  as ADR-045 token encryption).
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
   indexes (name, URL, optional auth). On startup and after CRUD, Gateway
   pushes the list to Galaxy Proxy (new admin endpoint, e.g.
   `POST /admin/pip-indexes`), fire-and-forget like `_galaxy_proxy_sync`.
3. **Proxy passthrough** — For non-collection packages, the proxy queries
   configured upstreams in order (and applies credentials). Empty list →
   keep today's default passthrough to `https://pypi.org`.
4. **Runtime application** — Proxy applies upstream config in-process for
   HTTP passthrough and injects env where subprocesses need credentials
   (same idea as `_inject_galaxy_env` for Galaxy).
5. **No air-gap mode** — Do not add `APME_AIRGAP` (or equivalent). Operators
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
- Plaintext tokens in SQLite inherit the ADR-045 encryption follow-up.

### Neutral

- Empty pip-index config preserves today's public PyPI behavior via proxy
  passthrough (behaviorally similar for online defaults; path differs).
- Collection download path (ansible-galaxy via ADR-045) is unchanged;
  only Python-package passthrough and Engine install flags change.

## Implementation Notes

- Gateway: table + REST CRUD analogous to `galaxy_servers` (ordered
  `priority`/`position` field). Additive `/api/v1` routes only (ADR-060).
- Gateway: extend `_galaxy_proxy_sync` (or sibling module) to push pip
  indexes on startup and after CRUD.
- Galaxy Proxy: admin endpoint to replace in-memory upstream list;
  multi-index passthrough for `/simple/{pkg}/` (ordered try; document
  merge vs first-hit in implementation TASK).
- Engine `venv_manager.session._run_pip_install`: switch to `--index-url`
  pointing at `APME_GALAXY_PROXY_URL/simple/`; drop reliance on implicit
  public PyPI as primary index.
- Update `docs/guides/DEPLOYMENT.md` index-strategy section after the
  sibling-index model is gone.
- Helm / Podman: no new air-gap flag; document configuring pip indexes via
  Gateway API / UI when implemented.
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
