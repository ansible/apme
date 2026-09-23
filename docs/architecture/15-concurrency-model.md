# 15 — Concurrency Model

> Previous: [14 — UI and WebSocket Integration](14-ui-integration.md) | Next: [16 — Diagnostics Instrumentation](16-diagnostics.md)

## Purpose

All APME gRPC servers use `grpc.aio` (fully async). This document
describes how each service manages concurrency — async event loops,
executor dispatch for blocking work, and RPC concurrency limits.

## Core Principle

The event loop must never block. Any work that blocks the thread —
subprocess calls, CPU-bound rule evaluation, venv builds, file I/O —
is dispatched via `asyncio.get_event_loop().run_in_executor()`. This
keeps the gRPC server responsive to health checks and concurrent
requests even when long-running work is in progress.

## Per-Service Concurrency

| Service | Concurrency strategy | `maximum_concurrent_rpcs` |
|---------|---------------------|--------------------------|
| Engine | `asyncio.gather()` fan-out to validators; engine scan via `run_in_executor()` | 16 |
| Native | CPU-bound graph rules via `run_in_executor()` | 32 |
| OPA | Blocking `opa eval` subprocess via `run_in_executor()` | 32 |
| Ansible | Blocking venv build + ansible subprocess via `run_in_executor()` | 8 |
| Gitleaks | Blocking gitleaks subprocess via `run_in_executor()` | 16 |
| Collection Health | Blocking collection metadata scan via `run_in_executor()` | 4 |
| Dep Audit | Blocking dependency audit via `run_in_executor()` | 8 |
| Gateway | Fully async (asyncpg, grpc.aio); FastAPI + uvicorn event loop | — |

Services with a documented numeric limit expose `maximum_concurrent_rpcs` via
environment variable (e.g., `APME_ENGINE_MAX_RPCS=16`). The
[APME Operator](https://github.com/ansible/apme-operator) sets Engine's limit
via `APME_ENGINE_MAX_RPCS` (default `16` in the reconciled manifest). Gateway
uses the FastAPI/uvicorn event loop without a gRPC `maximum_concurrent_rpcs`
knob. The defaults balance throughput against resource consumption on a single
pod.

## Engine

The Engine is the most complex concurrency case. A single `FixSession`
RPC handles:

1. **File upload** — async stream of `ScanChunk` messages (I/O-bound,
   stays on the event loop)
2. **Engine scan** — CPU-bound ARI parse + annotate, dispatched to the
   default executor via `run_in_executor()`
3. **Validator fan-out** — `asyncio.gather(*validator_calls,
   return_exceptions=True)` for parallel async gRPC calls
4. **Remediation** — graph transforms on the event loop; rescan cycles
   re-enter the executor for engine work
5. **Event emission** — fire-and-forget async gRPC calls to the Gateway

Multiple `FixSession` streams can be active concurrently up to
`maximum_concurrent_rpcs`. Each session has its own state in
`SessionStore` — sessions do not share mutable state.

### Validator Fan-Out

```python
results = await asyncio.gather(
    self._call_validator("native", request),
    self._call_validator("opa", request),
    self._call_validator("ansible", request),
    self._call_validator("gitleaks", request),
    self._call_validator("collection_health", request),
    self._call_validator("dep_audit", request),
    return_exceptions=True,
)
```

Wall-clock time = `max(all validators)` rather than `sum`. Each call is
an independent async gRPC stub call. Failures in one validator do not
block or cancel others — `return_exceptions=True`
captures errors as return values for graceful degradation.

## Validator Services

### Native Validator

Graph rules are Python functions that iterate over `ContentGraph` nodes.
Some rules (e.g., FQCN lookup, regex matching) are CPU-bound. The
entire rule evaluation runs in the default `ThreadPoolExecutor` via
`run_in_executor()`.

### OPA Validator

Each `Validate` call writes the hierarchy payload to a temp file and
invokes `opa eval` as a subprocess. The subprocess call blocks, so it
runs in the executor. The higher `maximum_concurrent_rpcs` (32) allows
many concurrent OPA evaluations since each is I/O-bound (waiting for
the subprocess).

### Ansible Validator

The lowest concurrency limit (8) because each validation may involve:
- Writing files to a temp directory
- Running `ansible-lint` or ansible module introspection as a subprocess
- Reading from the session venv (shared volume, read-only)

These are resource-intensive operations with significant memory and
disk I/O per request.

### Gitleaks Validator

Similar to OPA — a subprocess wrapper. Each call writes files to a temp
directory and runs `gitleaks detect`. The subprocess is the bottleneck;
the Python wrapper is lightweight.

## Gateway

The Gateway uses two async servers concurrently:

- **gRPC** (`grpc.aio`) — receives `FixCompletedEvent` and
  `RegisterRules` from engine pods
- **HTTP** (FastAPI + uvicorn) — serves REST API and WebSocket endpoints

Both share the same `asyncio` event loop via `asyncio.gather()` in the
main entry point. Database access uses `asyncpg` through SQLAlchemy's
async session — no blocking I/O on the event loop.

WebSocket endpoints that bridge to Engine's `FixSession` use
`asyncio.create_task()` to run the gRPC stream reader and WebSocket
command reader concurrently within a single connection handler.

## Executor Configuration

The default `ThreadPoolExecutor` is used by all services. Python's
default pool size is `min(32, os.cpu_count() + 4)`. For most
deployments this is sufficient. Services that need larger pools can
configure them at startup.

The executor pool is shared across all concurrent RPCs within a service.
This means the effective parallelism of blocking work is bounded by
`min(maximum_concurrent_rpcs, executor_pool_size)`.

## Request Correlation

Every scan request carries a `request_id` (derived from
`ScanChunk.scan_id`) that propagates through the entire system:

```
CLI → Engine (scan_id) → ValidateRequest.request_id → each validator logs [req=xxx]
                                                      → ValidateResponse.request_id (echo)
```

All validator logs are prefixed with `[req=xxx]` for end-to-end
correlation across concurrent requests. This makes it possible to trace
a single request through all services even when many requests are being
processed simultaneously.

## Key Source Files

| File | Concurrency role |
|------|-----------------|
| `src/apme_engine/daemon/engine_server.py` | `FixSession` handler, `asyncio.gather()` fan-out |
| `src/apme_engine/daemon/native_validator_server.py` | `run_in_executor()` for graph rules |
| `src/apme_engine/daemon/opa_validator_server.py` | `run_in_executor()` for `opa eval` subprocess |
| `src/apme_engine/daemon/ansible_validator_server.py` | `run_in_executor()` for ansible subprocess |
| `src/apme_engine/daemon/gitleaks_validator_server.py` | `run_in_executor()` for gitleaks subprocess |
| `src/apme_gateway/main.py` | `asyncio.gather()` for dual gRPC + HTTP servers |
| `src/apme_gateway/session_client.py` | `asyncio.create_task()` for WS + gRPC bridging |

## Related ADRs

- **ADR-007** — Async gRPC (`grpc.aio`) for all servers
- **ADR-001** — gRPC for all inter-service communication

---

> Next: [16 — Diagnostics Instrumentation](16-diagnostics.md)
