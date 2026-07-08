# ADR-033: Centralized Log Bridge with gRPC Transport

## Status

Implemented

## Date

2026-03-22

## Context

### The thin CLI cannot see server-side logs

ADR-024 established the CLI as a thin gRPC presentation layer. All engine
logic runs server-side — either in a local daemon process or a
multi-container pod. Currently, all server-side code uses raw
`sys.stderr.write()` calls. These go to `daemon.log` (daemon mode) or
container stderr (pod mode) but are completely invisible to the CLI.

The CLI's `-v`/`-vv` flag only controls which post-hoc diagnostics
summary is printed after a scan completes. There is no visibility into
what is happening during a scan — no indication of which phase is running,
how long each phase takes, or what decisions the engine is making.

### Two deployment modes with different process boundaries

**Daemon mode** (`launcher.py`): A single process runs Primary + all
validators in the same event loop. Stderr is dup2'd to
`~/.apme-data/daemon.log`. All services share the same Python logging
infrastructure.

**Pod mode** (Podman pod via `tox -e up`, ADR-004): Each service is a separate container with
its own entry point (`primary_main.py`, `native_validator_main.py`, etc.).
Container stderr goes to container logs. Validators communicate with
Primary via gRPC over the network.

In pod mode, validator logs are trapped in their own container and never
reach the Primary or CLI. The `ValidateResponse` proto has no mechanism
to carry logs back.

### The FixSession stream already has a log channel

The `FixSession` bidirectional stream (ADR-028) includes `ProgressUpdate`
messages with `LogLevel`, `phase`, and `message` fields. The CLI's
`remediate` command already filters these by level. However:

- `ProgressUpdate` messages are manually constructed, not driven by a
  logging system
- `Scan` and `Format` are unary RPCs with no log channel at all
- There is no consistency — some phases emit progress, others are silent

### Forces in tension

- Server-side code needs a familiar, low-friction logging API (standard
  Python `logging`)
- Logs must reach the CLI across gRPC boundaries in both deployment modes
- Logs must also persist to files for daemon/container debugging
- The solution must not require infrastructure (EFK, Loki) for local use
- Unary RPCs (Scan, Format) cannot stream logs during processing —
  logs must be collected and returned in the response

## Decision

**Implement a centralized log bridge where all subsystems use standard
Python `logging`, a shared `RequestLogHandler` collects log records
per-request via `contextvars`, and logs are transported back to the CLI
through gRPC responses. Validators return their logs in
`ValidateResponse`; Primary merges them with its own and forwards the
combined set to the CLI.**

### Core components

1. **`log_bridge.py`** — shared module installed in every process:
   - `RequestLogHandler(logging.Handler)` — routes every log record to
     stderr (file sink) and to the active per-request gRPC sink
   - `CollectorSink` — appends `ProgressUpdate` to a list (for unary
     RPCs and validator handlers)
   - `StreamSink` — puts `ProgressUpdate` into an `asyncio.Queue` (for
     `FixSession` bidirectional streaming)
   - `contextvars.ContextVar` tracks the active sink per-request
   - `install_handler()` — idempotent setup called by every entry point

2. **Proto changes** — `ProgressUpdate` and `LogLevel` move from
   `primary.proto` to `common.proto` for clean layering. New `logs`
   fields added to `ValidateResponse`, `ScanResponse`, and
   `FormatResponse`.

3. **Log flow**:
   - Validator code logs via `logging.getLogger("apme.native")` etc.
   - Validator's `Validate()` handler attaches a `CollectorSink`,
     returns collected logs in `ValidateResponse.logs`
   - Primary's `_call_validator()` extracts logs from each response
   - Primary merges its own logs with validator logs
   - CLI receives merged logs in `ScanResponse.logs` or as
     `SessionEvent.progress` messages and renders them based on
     `-v`/`-vv` verbosity

4. **Milestone logging convention** — every major pipeline phase logs
   a start message (INFO) and a finish message (INFO with duration).
   Sub-step details are DEBUG. This gives `-v` users a clear timeline
   of what happened and how long each step took.

## Alternatives Considered

### Alternative 1: Status quo (raw stderr)

**Description**: Keep `sys.stderr.write()` calls. Logs go to daemon.log
and container stderr only.

**Pros**:
- No code changes
- Simple — no handler plumbing

**Cons**:
- CLI users see nothing during scans
- Pod mode validator logs are invisible to Primary
- No structured levels, no filtering
- Debugging requires SSH/exec into containers or reading daemon.log

**Why not chosen**: The thin CLI (ADR-024) removed the user's ability to
see what the engine is doing. Without log transport, `-v` is meaningless.

### Alternative 2: Separate log streaming RPC

**Description**: Add a `rpc StreamLogs(LogRequest) returns (stream
ProgressUpdate)` RPC. The CLI opens a second connection to receive logs.

**Pros**:
- Clean separation of data and log channels
- Works for all RPC types

**Cons**:
- CLI must manage two connections per operation
- Race condition: log stream may start after interesting events
- Unary RPCs (Scan, Format) would need to be converted to streaming
  or the log stream needs correlation IDs
- More complex client code

**Why not chosen**: Overengineered for the use case. Embedding logs in
existing responses is simpler and avoids timing/correlation issues.

### Alternative 3: Container log aggregation (EFK/Loki)

**Description**: Deploy Elasticsearch/Fluentd/Kibana or Grafana Loki
alongside the pod. CLI queries the log aggregator.

**Pros**:
- Industry-standard observability
- Rich querying and dashboards

**Cons**:
- Heavy infrastructure — not suitable for local daemon mode
- Adds 3+ services to the pod
- CLI cannot query in real-time during a scan
- Latency between log emission and availability

**Why not chosen**: APME is a developer tool, not a long-running service.
The logging solution must work with `apme check .` on a laptop.

### Alternative 4: Custom log functions (not standard logging)

**Description**: Create `apme_log.info()`, `apme_log.debug()` etc. that
directly construct `ProgressUpdate` messages.

**Pros**:
- Direct control over log format
- No handler/contextvar indirection

**Cons**:
- Non-standard — developers must learn a custom API
- Cannot capture logs from third-party libraries
- Duplicates Python's logging module
- Every subsystem needs explicit import of the custom module

**Why not chosen**: Standard `logging` is familiar, well-tested, and
supports handler composition. The bridge pattern gives us the same
control without a custom API.

## Consequences

### Positive

- **CLI visibility.** Users see the full pipeline flow with `-v` and
  detailed internals with `-vv`. Scan timing is transparent.
- **Identical in both modes.** The same gRPC log transport works in
  daemon mode (localhost) and pod mode (network). No mode-specific code.
- **Standard API.** All subsystems use `logging.getLogger()`. No new
  APIs to learn. Third-party library logs are captured automatically.
- **File logging is free.** The handler always writes to stderr, which
  goes to daemon.log or container logs. No separate file configuration.
- **Structured and filterable.** `ProgressUpdate` messages carry `phase`
  and `LogLevel`, enabling the CLI to filter and format output cleanly.

### Negative

- **Response size increase.** `ScanResponse` and `ValidateResponse` grow
  by the size of collected log entries. For typical scans this is a few
  KB — negligible compared to violations and diagnostics.
- **Contextvar discipline.** Every RPC handler must attach/detach a sink.
  Forgetting to attach means logs go to stderr only (safe default, but
  the CLI won't see them).
- **Unary RPCs show logs after completion.** Scan and Format return logs
  in the response, so the CLI displays them post-hoc. Only FixSession
  (streaming) shows logs in real-time.

### Neutral

- The existing `ScanDiagnostics` timing data is preserved and still
  rendered by `-v`/`-vv`. The log bridge adds narrative context
  alongside the structured diagnostics.
- `ProgressUpdate` and `LogLevel` move to `common.proto`. This is a
  non-breaking change — field numbers and semantics are preserved.

## Implementation Notes

### Milestone logging convention

Every major pipeline phase follows the start/finish pattern:

```python
logger.info("Venv: acquiring session=%s core=%s", sid, version)
t0 = time.monotonic()
# ... do work ...
dur = (time.monotonic() - t0) * 1000
logger.info("Venv: ready (%.0fms, %s, %d collections)", dur, status, n)
```

Start messages are INFO. Finish messages are INFO with duration in
milliseconds. Sub-step internals are DEBUG. This gives `-v` a clean
timeline and `-vv` the full detail.

### Logger name to phase mapping

Logger names follow the `apme.<subsystem>` convention:

- `apme.primary` → phase `"primary"`
- `apme.native` → phase `"native"`
- `apme.opa` → phase `"opa"`
- `apme.ansible` → phase `"ansible"`
- `apme.gitleaks` → phase `"gitleaks"`
- `apme.engine` → phase `"engine"`
- `apme.venv` → phase `"venv"`
- `apme.remediation` → phase `"remediation"`

The handler strips the `apme.` prefix to derive the phase field.

### Proto changes

```protobuf
// common.proto — moved from primary.proto
enum LogLevel {
  LOG_LEVEL_UNSPECIFIED = 0;
  DEBUG = 1;
  INFO = 2;
  WARNING = 3;
  ERROR = 4;
}

message ProgressUpdate {
  string message = 1;
  string phase = 2;
  float progress = 3;
  LogLevel level = 4;
}

// validate.proto — new field
message ValidateResponse {
  // ... existing fields ...
  repeated ProgressUpdate logs = 4;
}

// primary.proto — new fields
message ScanResponse {
  // ... existing fields ...
  repeated ProgressUpdate logs = 7;
}

message FormatResponse {
  repeated FileDiff diffs = 1;
  repeated ProgressUpdate logs = 2;
}
```

### Threading / async safety

- `contextvars.ContextVar` propagates across `asyncio.Task` boundaries.
- `CollectorSink` uses `threading.Lock` for thread safety (validators
  run blocking code in `run_in_executor()`).
- `StreamSink` uses `asyncio.Queue` (async-safe, drained by the RPC
  handler coroutine).

## Related Decisions

- ADR-001: gRPC for all inter-service communication — logs now travel
  via gRPC too
- ADR-007: Async gRPC servers — handler uses contextvars and asyncio
  primitives compatible with grpc.aio
- ADR-024: Thin CLI with daemon mode — this ADR solves the observability
  gap created by moving all logic server-side
- ADR-028: Session-based fix workflow — FixSession's `ProgressUpdate`
  streaming is the model for the log bridge; this ADR generalizes it

## Addendum

> **Note (ADR-039):** The user-facing terminology was renamed: `scan` → `check`, `fix` → `remediate`, `Scans` UI → `Activity`. Engine-internal names (`ScanChunk`, `scan_id`, `_scan_pipeline`) and `ScanResponse` are unchanged. The `ScanStream` RPC was removed; `FixSession` serves both check and remediate modes. The `apme` binary name is unchanged.

## References

- Python `logging` module: https://docs.python.org/3/library/logging.html
- Python `contextvars`: https://docs.python.org/3/library/contextvars.html
- gRPC Python async API: https://grpc.github.io/grpc/python/grpc_asyncio.html

---

## Revision History

| Date       | Author   | Change           |
|------------|----------|------------------|
| 2026-03-22 | AI Agent | Initial proposal |
