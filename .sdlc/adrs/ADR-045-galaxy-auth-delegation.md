# ADR-045: Delegate Galaxy Authentication to ansible-galaxy, Galaxy Config as Scan Metadata

## Status

Proposed

## Date

2026-03-28

## Context

APME's Galaxy Proxy (ADR-031) converts Galaxy collection tarballs to Python
wheels and serves them via PEP 503.  The proxy also acts as the Galaxy V3 REST
API client — it implements `GalaxyClient` with `httpx`, manages per-server auth
headers, paginates version listings, and downloads tarballs directly from
upstream Galaxy / Automation Hub servers.

PR #130 proposes extending the proxy with:

- **SSO offline-token exchange** (`_SSOState`, Keycloak OIDC flow) for
  `console.redhat.com` Automation Hub
- **API root path normalization** for Automation Hub URL variations
- **`GALAXY_SERVER_LIST` env var parsing** (ansible.cfg-style multi-server
  config via environment variables)
- **Auth header forwarding** on tarball downloads
- **`auth_type` field** (token / bearer / sso) on `GalaxyServer`

All of this functionality already exists in `ansible-galaxy`, which is the
authoritative Galaxy API client maintained by the Ansible team:

- `ansible.cfg` `[galaxy_server_list]` with per-server sections
- SSO / offline-token exchange for `console.redhat.com` via `auth_url`
- Multiple auth types (`token`, `auth_url` for SSO)
- Multi-server fallback ordering
- Auth on tarball downloads
- `ansible-galaxy collection download` fetches tarballs without installing

Reimplementing this in the proxy creates a maintenance burden that must track
upstream Galaxy API and SSO endpoint changes indefinitely.  It also ignores the
user's existing `ansible.cfg` configuration — the standard place where Galaxy
server credentials are already configured.

### Forces

- CLI users already have `ansible.cfg` with Galaxy/AH server credentials
- UI users need Galaxy server configuration (global in Gateway DB — per-project
  scoping would create cache ambiguity since the proxy's wheel cache is
  collection-scoped, not credential-scoped)
- The engine is stateless (ADR-020) — it should not store credentials
- Galaxy auth complexity (SSO, token refresh, API path normalization) belongs
  in ansible-galaxy, not in our codebase
- The proxy's core value is tarball-to-wheel conversion and PEP 503 serving,
  not Galaxy API client implementation
- `ansible-core` is already installed in every session venv — `ansible-galaxy`
  is available there

### Constraints

- The Galaxy Proxy container currently does not have `ansible-core` installed
- No gRPC proto fields exist for Galaxy server configuration today
- Credentials must not be persisted by the engine (ADR-020, ADR-029)
- The engine never queries out to external systems; it only emits via
  fire-and-forget event sinks (see AGENTS.md, architectural invariant 11).
  Today the Galaxy Proxy already performs outbound fetches to Galaxy/AH servers
  on behalf of the engine — it is the pod's designated external-facing download
  service.  `ansible-galaxy collection download` should run in the proxy
  container (or a sidecar with outbound access), not in Primary, to preserve
  the invariant.  If implementation requires running it in Primary, this ADR
  proposes a narrowly scoped exception to invariant 11 for Galaxy/AH tarball
  downloads, to be documented in AGENTS.md.

## Decision

**We will delegate Galaxy authentication to ansible-galaxy and flow Galaxy
server configuration as scan-scoped metadata through the gRPC proto.**

The proxy's role narrows to its core value: tarball-to-wheel conversion and
PEP 503 serving.  Galaxy API interaction — authentication, server discovery,
tarball downloading — is delegated to `ansible-galaxy collection download`,
which is the authoritative, maintained implementation.

Galaxy server configuration flows as scan metadata:

```
CLI (reads ansible.cfg)  ──► gRPC ScanOptions.galaxy_servers ──► Primary
UI  (global server defs) ──► Gateway ──► gRPC ScanOptions     ──► Primary
                                                                     │
                                                          galaxy_servers + collection specs
                                                                     │
                                                                     ▼
                                                               Galaxy Proxy
                                                          writes temp ansible.cfg
                                                     ansible-galaxy collection download
                                                                     │
                                                              tarballs on disk
                                                              tarball → wheel
                                                              PEP 503 serving
                                                                     │
                                                              pip/uv install wheel
```

## Alternatives Considered

### Alternative 1: Reimplement Galaxy auth in the proxy (PR #130's approach)

**Description**: The proxy implements its own Galaxy V3 REST API client with SSO
token exchange, API root normalization, multi-server config via env vars, and
auth header forwarding.

**Pros**:
- Self-contained — proxy has no dependency on ansible-core
- Direct HTTP is slightly faster than subprocess

**Cons**:
- Duplicates battle-tested auth logic from ansible-galaxy
- Must track upstream Galaxy API, SSO endpoint, and auth protocol changes
- Ignores the user's existing `ansible.cfg` (requires separate env var config)
- No path for UI-driven per-project credentials (env vars are process-global)
- SSO token refresh, Keycloak quirks, and API path normalization are complex
  and error-prone to reimplement

**Why not chosen**: Reimplementing `ansible-galaxy`'s auth stack creates a
parallel implementation that must track upstream changes to the Galaxy V3 API,
Red Hat SSO endpoints, Keycloak OIDC flows, and Automation Hub URL conventions.
The proxy should focus on what only it can do: format conversion at the boundary.

### Alternative 2: Hybrid — proxy keeps simple token auth, adds ansible-galaxy for SSO

**Description**: The proxy retains its existing `Authorization: Token` auth for
simple Galaxy servers.  For SSO-authenticated servers (Automation Hub), it
delegates to `ansible-galaxy collection download`.

**Pros**:
- Minimal change for simple Galaxy (public, private with token)
- SSO complexity delegated to ansible-galaxy

**Cons**:
- Two auth paths to maintain and reason about
- Still ignores the user's `ansible.cfg` for the token path
- Inconsistent behavior between auth types
- Eventually the simple path will also want ansible.cfg-style config

**Why not chosen**: A hybrid approach creates two code paths for the same
operation (fetching a collection tarball), making the system harder to reason
about and test.  If we are going to use ansible-galaxy for the hard cases, we
should use it for all cases and eliminate the custom client entirely.

## Consequences

### Positive

- **No custom auth code**: SSO token exchange, API root normalization, and
  auth header forwarding are all handled by ansible-galaxy
- **Automatic upstream tracking**: Galaxy API changes, new auth methods, and
  Automation Hub URL conventions are picked up via ansible-core upgrades
- **CLI zero-config**: Users' existing `ansible.cfg` Galaxy server sections
  work without any APME-specific configuration
- **UI credential management**: Global Galaxy server defs stored in Gateway
  DB, injected into all scan requests — enables Automation Hub integration
  from the web UI without per-project duplication
- **Simplified proxy**: `galaxy_client.py` reduces to tarball-to-wheel
  conversion; no `httpx` dependency for Galaxy API calls
- **Security**: Credentials flow as scan-scoped metadata (in-transit on
  pod-local gRPC), never persisted by the engine.  Gateway DB stores
  global credentials; encrypting these at rest (application-layer
  encryption or a secrets manager) is a follow-up requirement

### Negative

- **ansible-core dependency**: `ansible-galaxy collection download` requires
  ansible-core.  Session venvs already have it; the proxy container may need
  it added, or the download step moves to Primary (which has access to session
  venvs)
- **Proto evolution**: New `GalaxyServerDef` message and field on
  `ScanOptions`/`FixOptions` — requires proto regeneration and client updates
- **Subprocess latency**: `ansible-galaxy collection download` is slower than
  direct HTTP for the first fetch (mitigated by proxy wheel cache — subsequent
  requests are instant cache hits)

### Neutral

- PR #130's bug fixes (naming hyphen-to-underscore, pagination via
  `links.next`, inline comment stripping) remain valuable and should be
  cherry-picked independently
- The `remediate.py` type annotation cleanup in PR #130 is unrelated and can
  be merged separately
- ADR-031's core decision (contain Galaxy format at the proxy boundary, serve
  wheels via PEP 503) is unchanged — this ADR narrows the proxy's
  responsibilities, not its purpose

## Implementation Notes

Implementation is three PRs, ordered by dependency:

### PR 1: Proxy — replace Galaxy API client with ansible-galaxy CLI

Remove the custom `GalaxyClient` (httpx-based Galaxy V3 REST client, SSO
token exchange, API root normalization) from the proxy.  Replace with
`ansible-galaxy collection download` to fetch tarballs.

- Preferred: proxy runs `ansible-galaxy collection download` (keeps outbound
  fetches in the pod's designated external-facing service, preserving
  invariant 11).  Primary sends the temp `ansible.cfg` path + collection
  specs to the proxy via gRPC or shared volume.
- Fallback: Primary runs `ansible-galaxy collection download` directly —
  requires documenting a narrow invariant 11 exception in AGENTS.md
- Proxy: convert local tarballs to wheels (endpoint or filesystem watcher)
- Remove `GalaxyClient` upstream fetching from proxy

### PR 2: CLI + proto — Galaxy server config as scan metadata

Add proto fields and wire the CLI to read the user's `ansible.cfg`.

- Add `GalaxyServerDef` message to `common.proto` (`url`, `token`,
  `auth_url`, `name`, `auth_type`)
- Add `repeated GalaxyServerDef galaxy_servers` to `ScanOptions` and
  `FixOptions`
- CLI: parse `ansible.cfg` `[galaxy_server_list]` sections, populate
  `galaxy_servers` on scan requests
- Primary: write temp `ansible.cfg` from `galaxy_servers`, scope to session

### PR 3: Gateway + UI — global Galaxy server management

- Gateway: add global `galaxy_servers` table (encrypted token storage)
- Gateway: inject `galaxy_servers` into all gRPC requests when calling engine
- UI: global Galaxy server configuration (settings page, not per-project)

### Cherry-pick from PR #130

These changes are valuable regardless of the auth delegation decision:

- `naming.py`: hyphen-to-underscore fix in `python_to_fqcn`
- `metadata.py`: inline comment stripping in requirements parsing
- `galaxy_client.py`: pagination via `links.next` URL (if client is retained
  for any fallback role)
- `remediate.py`: type annotation cleanup (`list[object]` → `list[Proposal]`)

## Related Decisions

- [ADR-031](ADR-031-unified-collection-cache.md): Unified Collection Cache —
  this ADR narrows the proxy's role defined there
- [ADR-022](ADR-022-session-scoped-venvs.md): Session-Scoped Venvs — session
  venvs already have ansible-core, providing ansible-galaxy
- [ADR-020](ADR-020-reporting-service.md): Reporting Service — engine
  statelessness; credentials must not be persisted by the engine
- [ADR-029](ADR-029-web-gateway-architecture.md): Web Gateway — Gateway owns
  persistence, including credential storage
- [ADR-040](ADR-040-scan-metadata-enrichment.md): Scan Metadata Enrichment —
  Galaxy server config is another form of scan metadata

## References

- [PR #130](https://github.com/ansible/apme/pull/130): Galaxy multi-server
  and SSO auth — the PR that prompted this architectural review
- [ansible-galaxy CLI](https://docs.ansible.com/ansible/latest/cli/ansible-galaxy.html):
  Upstream CLI for Galaxy operations including `collection download`
- [Configuring Galaxy servers (ansible.cfg)](https://docs.ansible.com/ansible/latest/galaxy/user_guide.html#configuring-the-ansible-galaxy-client):
  Upstream documentation for `[galaxy_server_list]` and per-server sections

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-28 | AI-assisted | Initial proposal |
| 2026-03-28 | AI-assisted | Restructured to match ADR template (Copilot review) |
| 2026-03-28 | Human review | Galaxy server defs are global, not per-project (cache coherence) |
| 2026-03-28 | Human review | Implementation split into 3 PRs: proxy, CLI+proto, UI |
