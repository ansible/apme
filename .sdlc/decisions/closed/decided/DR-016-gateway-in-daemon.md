# DR-016: Embed Gateway in Local Daemon

## Status

Decided

## Raised By

Roger Lopez — 2026-04-01

## Category

Architecture

## Priority

High

---

## Question

Should the Gateway (REST API + persistence) run as part of the local daemon process, so that CLI commands like `apme sbom` work without requiring a Podman pod?

## Context

ADR-024 established the local daemon pattern: `ensure_daemon()` auto-starts Primary + validators as localhost gRPC servers, giving standalone users the same architecture as the pod. The daemon already runs five gRPC servers and a uvicorn app (Galaxy Proxy) in a single async event loop.

PR 3 of the SBOM implementation (ADR-040) introduces `apme sbom` as the first CLI subcommand that calls the Gateway REST API instead of Primary gRPC. ADR-024's "Future Direction" section explicitly identifies this as the intended pattern for read-heavy operations on persisted data.

The problem: today the Gateway is classified as a "pod-level/enterprise service" that the CLI daemon does not start (CLAUDE.md). This means `apme sbom` only works when a full pod is running — breaking the "just works" UX that `apme check` and `apme remediate` provide via auto-daemon-start.

As more CLI subcommands migrate to Gateway REST (health, session list, etc.), this gap widens.

## Impact of Not Deciding

- `apme sbom` requires manual Gateway startup or a running pod — poor standalone DX
- Future CLI→REST migrations (per ADR-024) face the same friction
- Risk of pressure to add duplicate gRPC endpoints on Primary to avoid the Gateway dependency, creating the dual-code-path problem ADR-024 was designed to eliminate

---

## Options Considered

### Option A: Embed Gateway in Daemon

**Description**: Add the Gateway's HTTP server (uvicorn/FastAPI) and gRPC ReportingServicer to the daemon's `_run_daemon()` async event loop, following the same pattern as Galaxy Proxy. The Gateway DB defaults to `~/.apme-data/gateway.db`. The daemon state file gains a `gateway_http` address entry.

**Pros**:
- `apme sbom` "just works" — same auto-start UX as all other commands
- Enables CLI→REST migration path without UX regression
- Follows existing precedent (Galaxy Proxy is already a uvicorn app in the daemon)
- Same architecture everywhere (local ≈ pod), per ADR-024's design goal
- Gateway receives `FixCompletedEvent` on localhost — scans auto-populate the DB

**Cons**:
- Adds `apme_gateway` as a daemon import (SQLAlchemy, Alembic, FastAPI dependencies)
- Increases daemon memory footprint
- Gateway in daemon uses SQLite; pod Gateway also uses SQLite but could diverge to PostgreSQL later — need to ensure compatibility
- Port 8080 added to daemon port set — potential conflicts

**Effort**: Medium

### Option B: Keep Gateway Pod-Only

**Description**: Gateway remains a pod-level service. CLI commands that need the Gateway REST API require the user to start the Gateway separately (or run the full pod). The CLI gives a clear error message when the Gateway is unreachable.

**Pros**:
- No daemon changes needed
- Clear separation: daemon = engine, pod = full stack
- Lighter daemon footprint

**Cons**:
- `apme sbom` and future REST-backed commands don't "just work" in standalone mode
- Breaks the UX consistency established by ADR-024 (all commands auto-start what they need)
- May pressure developers to add duplicate gRPC paths to avoid the Gateway dependency
- Two-tier CLI experience: some commands auto-start, others require manual setup

**Effort**: Low

### Option C: Standalone Gateway Launcher

**Description**: Add a separate `ensure_gateway()` discovery function (similar to `ensure_daemon()`) that auto-starts the Gateway as its own background process when needed. The daemon and Gateway run as independent processes.

**Pros**:
- Gateway lifecycle is independent of daemon
- Can be started/stopped separately
- Lighter coupling

**Cons**:
- Two background processes to manage (two PIDs, two state files, two health checks)
- More complex lifecycle: daemon restart doesn't restart Gateway, version mismatches between the two
- Galaxy Proxy precedent already shows uvicorn-in-daemon works fine
- Over-engineered for what is essentially "add another uvicorn app to the event loop"

**Effort**: Medium

---

## Recommendation

**Option A (Embed Gateway in Daemon)**. The Galaxy Proxy precedent proves the pattern works. The daemon already runs a uvicorn app (`create_app()`) as an `asyncio.create_task()` — the Gateway HTTP server is structurally identical. The ReportingServicer can be added to the existing gRPC server or on a separate port. This keeps the "just works" UX promise from ADR-024 and enables the CLI→REST migration path without friction.

The key implementation detail: Primary needs to send `FixCompletedEvent` to the co-located ReportingServicer. In daemon mode this is localhost gRPC on port 50060 (same as pod). The existing `GrpcReportingSink` already handles this — it just needs `APME_REPORTING_ADDRESS` set to the local Gateway gRPC port.

---

## Related Artifacts

- ADR-024: Thin CLI with Local Daemon Mode — establishes daemon pattern, documents CLI→REST future direction
- ADR-029: SQLite in Web Gateway — Gateway persistence design
- ADR-040: Scan Metadata Enrichment — `apme sbom` is PR 3 of this ADR
- ADR-004: Podman Pod Deployment — pod topology
- DR-008: Scan Result Persistence — decided: SQLite in Gateway

---

## Discussion Log

| Date | Participant | Input |
|------|-------------|-------|
| 2026-04-01 | Roger Lopez | Raised during SBOM CLI (PR 3) implementation — `apme sbom` requires Gateway but daemon doesn't start it. Should follow Galaxy Proxy precedent. |

---

## Decision

**Status**: Decided
**Date**: 2026-04-01
**Decided By**: Roger Lopez

**Decision**: Option A — Embed Gateway in Daemon

**Rationale**: The Galaxy Proxy precedent proves the pattern works. The daemon already runs a uvicorn app (`create_app()`) as an `asyncio.create_task()` — the Gateway HTTP server is structurally identical. The ReportingServicer can be added to the existing gRPC server or on a separate port. This keeps the "just works" UX promise from ADR-024 and enables the CLI→REST migration path without friction. The existing `GrpcReportingSink` wires Primary→Gateway on localhost automatically via `APME_REPORTING_ADDRESS`.

**Action Items**:
- [ ] Create ADR documenting Gateway-in-daemon architecture
- [ ] Add Gateway HTTP + gRPC to `_run_daemon()` in `launcher.py`
- [ ] Add `gateway_http` address to `DaemonState` and `daemon.json`
- [ ] Default Gateway DB to `~/.apme-data/gateway.db`
- [ ] Set `APME_REPORTING_ADDRESS` to local Gateway gRPC port in daemon
- [ ] Add port 8080 (HTTP) and 50060 (gRPC) to `_DEFAULT_PORTS`
- [ ] Update CLAUDE.md to reflect Gateway is now part of daemon
- [ ] Update `sbom_cmd.py` to discover Gateway URL from daemon state

---

## Post-Decision Updates

| Date | Update |
|------|--------|
