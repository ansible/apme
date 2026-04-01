# ADR-049: Gateway Embedded in Local Daemon

## Status

Accepted

## Date

2026-04-01

## Context

ADR-024 established the local daemon pattern: `ensure_daemon()` auto-starts
Primary + validators as localhost gRPC servers, giving standalone users the
same architecture as the pod without requiring containers. The daemon already
runs five gRPC servers and a uvicorn app (Galaxy Proxy) in a single async
event loop.

ADR-024's "Future Direction" section identified a CLI→Gateway REST migration
path for read-heavy operations on persisted data. `apme sbom` (ADR-040) is
the first command to use this pattern, calling `GET /api/v1/projects/{id}/sbom`
on the Gateway REST API.

However, the Gateway is currently classified as a "pod-level/enterprise
service" that the CLI daemon does not start (CLAUDE.md). This means
`apme sbom` only works when a full pod is running — breaking the "just works"
UX that `apme check` and `apme remediate` provide via auto-daemon-start.

As more CLI subcommands migrate to Gateway REST (health, session list, etc.
per ADR-024), this gap widens. Without the Gateway in the daemon, there is
pressure to duplicate Gateway endpoints as gRPC RPCs on Primary, recreating
the dual-code-path problem ADR-024 was designed to eliminate.

### Forces

- Developers expect `apme sbom <project>` to "just work" like `apme check .`
- The CLI→REST migration path (ADR-024) requires the Gateway to be reachable
- Galaxy Proxy already proves the uvicorn-in-daemon pattern works
- The daemon should remain a single background process (not multiple PIDs)
- Gateway persistence (SQLite) must work in both daemon and pod modes

## Decision

**Embed the Gateway (HTTP server + gRPC ReportingServicer + SQLite
persistence) in the local daemon process**, following the same pattern used
for Galaxy Proxy.

The daemon's `_run_daemon()` function gains two additional services:

1. **Gateway HTTP** (uvicorn/FastAPI) on port 8080 — serves the REST API
   (`/api/v1/projects/...`, `/api/v1/.../sbom`, etc.)
2. **Gateway gRPC** (ReportingServicer) on port 50060 — receives
   `FixCompletedEvent` from the co-located Primary

The Gateway database defaults to `~/.apme-data/gateway.db` (SQLite, same
engine as the pod Gateway per ADR-029).

The `DaemonState` dataclass and `daemon.json` state file gain a
`gateway_http` field so CLI commands can discover the Gateway URL without
hardcoding a port.

Primary's `GrpcReportingSink` is wired to the co-located Gateway via
`APME_REPORTING_ADDRESS=127.0.0.1:50060`, set as an env var in
`_run_daemon()` (same pattern as `NATIVE_GRPC_ADDRESS`, `OPA_GRPC_ADDRESS`,
etc.).

## Alternatives Considered

### Alternative 1: Keep Gateway Pod-Only

**Description**: Gateway remains a pod-level service. CLI commands that need
the Gateway REST API require the user to start the Gateway separately or run
the full pod.

**Pros**:
- No daemon changes needed
- Clear separation: daemon = engine, pod = full stack

**Cons**:
- `apme sbom` and future REST-backed commands don't "just work" standalone
- Breaks UX consistency from ADR-024
- Pressures duplicate gRPC endpoints on Primary

**Why not chosen**: Undermines the CLI→REST migration path and creates
two-tier CLI experience.

### Alternative 2: Standalone Gateway Launcher

**Description**: Add `ensure_gateway()` that auto-starts the Gateway as its
own background process, independent of the daemon.

**Pros**:
- Independent lifecycle
- Can be started/stopped separately

**Cons**:
- Two background processes to manage (two PIDs, two state files)
- Version mismatch risk between daemon and Gateway
- Over-engineered — Galaxy Proxy proves uvicorn-in-daemon works

**Why not chosen**: Unnecessary complexity for what is structurally identical
to the existing Galaxy Proxy pattern.

## Consequences

### Positive

- **"Just works" UX preserved.** `apme sbom` auto-starts the daemon (which
  now includes Gateway), matching the experience of all other commands.
- **CLI→REST migration enabled.** Future commands (health, session list) can
  move to Gateway REST without UX regression.
- **Single code path.** Gateway REST API is the same in daemon and pod. No
  duplicate gRPC endpoints needed on Primary.
- **Scan data auto-populates.** `FixCompletedEvent` flows from Primary to
  co-located Gateway — SBOM data is available immediately after a scan.
- **Follows established precedent.** Same `asyncio.create_task(server.serve())`
  pattern as Galaxy Proxy.

### Negative

- **Larger daemon dependency tree.** `apme_gateway` brings SQLAlchemy,
  Alembic, and FastAPI into the daemon process. Mitigated by lazy imports.
- **Increased memory footprint.** Gateway adds ~20-30 MB to the daemon.
  Acceptable for a developer workstation.
- **Two additional ports.** 8080 (HTTP) and 50060 (gRPC) added to daemon
  port set. Port conflicts are handled by the existing `_assert_ports_free()`
  mechanism.

### Neutral

- The pod deployment is unchanged — Gateway continues to run as a separate
  container in the Podman pod. The daemon simply co-locates what the pod
  runs as separate containers.
- SQLite remains the database engine in both modes (per ADR-029).

## Implementation Notes

### Changes to `launcher.py`

Add to `_DEFAULT_PORTS`:
```python
_DEFAULT_PORTS = {
    "primary": 50051,
    "native": 50055,
    "opa": 50054,
    "ansible": 50053,
    "galaxy_proxy": 8765,
    "gateway_grpc": 50060,   # NEW
    "gateway_http": 8080,    # NEW
}
```

Add to `_run_daemon()` (after Galaxy Proxy, before Primary):
```python
if "gateway_grpc" in services:
    from apme_gateway.db import init_db
    from apme_gateway.app import create_app as create_gateway_app
    from apme_gateway.grpc_reporting.servicer import ReportingServicer

    gw_db_path = str(_DATA_DIR / "gateway.db")
    await init_db(gw_db_path)

    # Gateway HTTP (uvicorn)
    gw_app = create_gateway_app()
    gw_host, _, gw_port_s = services["gateway_http"].rpartition(":")
    gw_config = uvicorn.Config(
        gw_app, host=gw_host or "127.0.0.1",
        port=int(gw_port_s), log_level="warning",
    )
    gw_server = uvicorn.Server(gw_config)
    asyncio.create_task(gw_server.serve())

    # Gateway gRPC (ReportingServicer)
    # Can be co-hosted on Primary's server or on separate port
    os.environ["APME_REPORTING_ADDRESS"] = services["gateway_grpc"]

    sys.stderr.write(f"  Gateway HTTP on http://{services['gateway_http']}\n")
    sys.stderr.write(f"  Gateway gRPC on {services['gateway_grpc']}\n")
```

Add `gateway_http` to `DaemonState`:
```python
@dataclass
class DaemonState:
    pid: int
    primary: str
    version: str
    started_at: str
    services: dict[str, str] = field(default_factory=dict)
```

The `services` dict already supports arbitrary entries — `gateway_http`
is added automatically by the existing `for name, port in all_ports.items()`
loop.

### CLI Gateway Discovery

`sbom_cmd.py` (and future REST-backed commands) discover the Gateway URL:

1. `--gateway-url` CLI flag (explicit, wins always)
2. `APME_GATEWAY_URL` env var
3. `daemon.json` → `services.gateway_http`
4. Default `http://localhost:8080`

### CLAUDE.md Update

Remove Gateway from "pod-level/enterprise services the CLI daemon does not
start" and add it to the daemon service list.

## Related Decisions

- ADR-024: Thin CLI with Local Daemon Mode — establishes daemon pattern,
  documents CLI→REST future direction
- ADR-029: SQLite in Web Gateway — Gateway persistence design
- ADR-040: Scan Metadata Enrichment — `apme sbom` is PR 3 of this ADR
- ADR-004: Podman Pod Deployment — pod topology (unchanged)
- DR-008: Scan Result Persistence — decided: SQLite in Gateway
- DR-016: Embed Gateway in Local Daemon — this ADR documents that decision

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-04-01 | Roger Lopez | Initial proposal (from DR-016) |
