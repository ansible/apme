# DR-022: Pip Index Egress via Galaxy Proxy

## Status

Decided

## Raised By

User — 2026-09-16

## Category

Architecture

## Priority

High

---

## Question

How should APME support custom / multiple pip indexes (including air-gapped
environments that never reach public PyPI) without inventing a separate
"air-gap mode"?

## Context

Session venvs install collections via Galaxy Proxy (`--extra-index-url`) while
`uv`/`pip` still resolve transitive Python deps against the default public
PyPI index. Galaxy Proxy already has `--pypi-url` passthrough, but Engine does
not route Python-package traffic through it.

Enterprise and air-gapped deployments need:

1. **Multiple** configured pip indexes (not a single primary only).
2. **Galaxy Proxy as the sole Engine egress** for Python package resolution
   (proxy owns passthrough to upstream indexes).
3. **Config + credentials in the Gateway DB**, pushed to the proxy the same
   way Galaxy servers are today (`POST /admin/galaxy-config`, ADR-045) and
   applied at runtime as env / in-process upstream config.
4. **No dedicated air-gap mode** — leave defaults (public PyPI) when nothing
   is configured; if the network cannot reach them, installs fail naturally.
   Operators who need isolation configure private indexes / private Galaxy
   (or Hub) instead of flipping a flag.

Conversation decisions (2026-09-16) already lean toward Option A below;
this DR records the choice for formal review and follow-up ADR/REQ.

## Impact of Not Deciding

- Air-gapped and mirror-only customers cannot run collection installs without
  undocumented workarounds.
- Continuing `--extra-index-url` + implicit PyPI leaves an uncontrolled egress
  path and dependency-confusion surface (`unsafe-best-match`).
- Ad-hoc env vars on Engine would diverge from the Gateway-owned Galaxy
  config pattern (ADR-045) and complicate Helm/Podman.

---

## Options Considered

### Option A: Gateway-managed indexes; Galaxy Proxy is sole pip egress (recommended)

**Description**:

- Add a Gateway DB table (and REST CRUD) for ordered pip indexes
  (URL, optional auth, priority/order), mirroring `galaxy_servers`.
- On startup and after CRUD, Gateway pushes the list to Galaxy Proxy
  (new `/admin/pip-indexes` or extended admin payload) with acknowledgment,
  retry/backoff, and unsynchronized vs empty-config tracking (ADR-071).
- Proxy applies upstreams at runtime: first-hit multi-index passthrough for
  non-collection `/simple/{pkg}/` (ordered try per ADR-071; no cross-upstream
  merge); inject credentials via env where subprocesses need them (same idea
  as `_inject_galaxy_env`).
- Engine `uv pip install` uses **only**
  `--index-url http://galaxy-proxy:8765/simple/` (Galaxy Proxy is primary
  index, not merely `--extra-index-url`). Collections and Python deps both
  resolve through the proxy.
- Unset index list normalizes to explicit empty desired configuration;
  public PyPI passthrough via proxy requires synchronized acknowledgment
  (ADR-071 state transitions). No `APME_AIRGAP=1` (or equivalent) flag.

**Pros**:

- One egress point to reason about (CA trust, logging, allowlists).
- Reuses proven Gateway DB → proxy push pattern (ADR-045).
- Multiple indexes and private mirrors fall out of the same model.
- Air-gap is configuration, not a product mode.
- Aligns with invariant 11 (engine does not query external indexes;
  Gateway owns enrichment/config, proxy is pod-local).

**Cons**:

- Proxy must implement multi-upstream passthrough correctly (ordering,
  auth headers, caching).
- Engine install flag change (`--index-url` vs `--extra-index-url`) needs
  careful index-strategy defaults (`first-match` vs `unsafe-best-match`
  once PyPI is no longer a sibling index).
- Token storage plaintext in the Gateway DB inherits ADR-045 follow-up (encryption).

**Effort**: Medium

**Architectural pre-screen**: Clean with invariants 2, 11, 12. Extends
ADR-031/045; warrants a short ADR for install flags + admin sync contract.
Does **not** invert Gateway↔Engine dependency (Gateway pushes to proxy;
Engine still only talks to proxy URL).

### Option B: Engine-side multi `--index-url` / `--extra-index-url` env vars

**Description**: Configure `APME_PIP_INDEX_URL` / extras on the Engine;
Galaxy Proxy remains collections-only; Python deps bypass the proxy.

**Pros**:

- Smaller proxy change.
- Familiar pip/uv knobs.

**Cons**:

- Two egress paths (Engine→PyPI and Proxy→Galaxy/PyPI).
- Duplicates config vs Gateway Galaxy servers.
- Weaker air-gap story (must lock down Engine and Proxy separately).
- ⚠ Weaker fit for invariant 11 (engine would reach external indexes
  directly).

**Effort**: Low–Medium

### Option C: Explicit air-gap mode flag

**Description**: `APME_AIRGAP=true` disables public defaults and fails closed
unless private indexes are set.

**Pros**:

- Obvious operator signal.

**Cons**:

- Rejected in discussion: mode flag is redundant if defaults simply attempt
  public PyPI and fail when unreachable; isolation is achieved by configuring
  private indexes / Hub only.
- Extra branching in health checks and docs.

**Effort**: Low (but wrong product shape)

### Option D: Do Nothing / Defer

**Description**: Keep implicit public PyPI + proxy `--extra-index-url`.

**Pros**:

- No work now.

**Cons**:

- Blocks air-gapped and mirror-only deployments.
- Leaves uncontrolled Engine→PyPI egress.

**Effort**: None

---

## Recommendation

**Option A**, per 2026-09-16 discussion:

1. Multiple pip indexes.
2. Galaxy Proxy is the Engine's only pip index (egress / passthrough).
3. Persist indexes (and credentials) in Gateway DB; push to proxy; apply as
   runtime env / upstream config (Galaxy-server precedent).
4. No air-gap mode — defaults attempt public PyPI via proxy passthrough;
   unreachable indexes fail install naturally.

Follow-up after decide: ADR (topology + admin API + Engine install flags),
then REQ/TASK for implementation.

---

## Related Artifacts

- ADR-031: Unified Collection Cache (Galaxy Proxy PEP 503)
- ADR-045: Galaxy auth delegation; Gateway DB → proxy config push
- ADR-029: Web gateway architecture (persistence at edge)
- ADR-071: Pip Index Egress via Galaxy Proxy (Proposed — from this DR)
- docs/guides/DEPLOYMENT.md — `APME_UV_INDEX_STRATEGY` / `unsafe-best-match`

---

## Discussion Log

| Date | Participant | Input |
|------|-------------|-------|
| 2026-09-16 | User | Want custom pip repo + true air-gap support |
| 2026-09-16 | User | (1) multiple indexes (2) Galaxy Proxy as egress/passthrough (3) Gateway DB then env (4) no air-gap mode — attempt defaults |
| 2026-09-16 | User | `/dr-review` → Option A; action item: draft ADR only |

---

## Decision

**Status**: Decided
**Date**: 2026-09-16
**Decided By**: User

**Decision**: Option A — Gateway-managed ordered pip indexes; Galaxy Proxy is
the Engine's sole pip Simple API index (egress / passthrough). No air-gap mode.

**Rationale**: Multiple pip indexes in Gateway DB, pushed to Galaxy Proxy;
Engine uses the proxy as its only pip index; no air-gap mode — defaults
attempt public PyPI via passthrough and fail naturally if unreachable.

**Action Items**:
- [x] Draft ADR for pip-index egress topology — [ADR-071](../../../adrs/ADR-071-pip-index-egress.md)

---

## Post-Decision Updates

| Date | Update |
|------|--------|
| 2026-09-16 | ADR-071 created (Proposed) from this DR |
