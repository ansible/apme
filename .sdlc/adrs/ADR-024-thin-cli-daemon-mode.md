# ADR-024: Thin CLI with Local Daemon Mode

## Status

Proposed

## Date

2026-03-18

## Context

### The original design intent

ADR-001 established gRPC as the protocol for all inter-service communication.
ADR-004 placed the Primary as the orchestrator inside a Podman pod, with the
CLI as a thin on-the-fly container that calls Primary over gRPC. The
architecture diagram in CLAUDE.md shows the CLI outside the pod, talking to
Primary on :50051.

### What actually happened

The CLI grew into a **fat client** that embeds the entire engine. When
`APME_PRIMARY_ADDRESS` is not set (standalone mode), the CLI runs the ARI
engine, all validators (OPA, Native, Ansible), the YAML formatter
(`ruamel.yaml` round-trip with 824 lines of customization), and the full
remediation convergence loop — all in-process. The gRPC path is used only for
the `scan` subcommand when running inside the pod; `fix`, `format`, and `cache`
always run locally.

This happened incrementally: each feature needed a "works without the pod"
path, and the local fallback accumulated until the CLI became a monolith
(1,315 lines in `cli.py`).

### The problems this creates

1. **No reuse.** The orchestration logic (format → idempotency check →
   scan → remediate convergence loop) lives inside `cli.py`. A web UI,
   VS Code extension, or CI integration would need to reimplement it.
   The Primary — which should be the single orchestrator — only knows
   how to scan and format, not remediate.

2. **Dual code paths.** Every subcommand has `if primary_addr:` / `else:`
   branching. The two paths have subtly different behavior (e.g., the gRPC
   scan path silently dropped `collection_specs` until PR #34 fixed it).
   Bugs in one path don't surface in the other.

3. **Heavy dependency tree.** The CLI imports `ruamel.yaml`, the ARI engine,
   `jsonpickle`, all validators, venv management (`uv`, `ansible-galaxy`),
   and the remediation engine — even when running in the pod where those
   dependencies exist in dedicated containers.

4. **Not the architecture we designed.** ADR-001 says "gRPC for all
   inter-service communication." ADR-004 says the CLI is ephemeral and
   on-the-fly. The current CLI violates both — it bypasses gRPC for most
   operations and embeds long-running engine logic.

### Forces in tension

- Developers need `apme-scan scan .` to "just work" without starting a pod.
- The pod is the correct architecture for production and CI.
- A web UI is on the roadmap and needs the same backend capabilities.
- The CLI should not require Podman/Docker for local development.

## Decision

**Refactor the CLI to a thin gRPC presentation layer. Add a local daemon mode
that runs the Primary + validators as localhost gRPC servers, giving standalone
users the same architecture as the pod without requiring containers.**

The CLI will always speak gRPC. The backend is either:

1. A **Podman pod** (production, CI) — discovered via `APME_PRIMARY_ADDRESS`
2. A **local daemon** (development) — auto-started on first use

The CLI will no longer embed engine, validator, formatter, or remediation
logic. All orchestration moves to the Primary via new gRPC RPCs.

## Alternatives Considered

### Alternative 1: Keep the fat CLI (status quo)

**Description**: Leave the dual local/gRPC code paths in place. Add new
features to both paths.

**Pros**:
- No migration effort
- Works today

**Cons**:
- Every new feature requires two implementations
- Web UI cannot reuse CLI orchestration logic
- Bugs hide in the less-tested code path
- Violates ADR-001 and ADR-004

**Why not chosen**: The architectural debt compounds with every new feature.
The web UI would force a reckoning regardless.

### Alternative 2: Subprocess spawning (no daemon persistence)

**Description**: The CLI spawns Primary + validators as background processes
for each invocation, tears them down on exit.

**Pros**:
- No persistent daemon to manage
- Clean process isolation

**Cons**:
- 2-5 second startup latency per invocation (spawn 4-6 processes + health
  check polling)
- Unacceptable for iterative `apme-scan fix .` workflows
- Process cleanup on SIGKILL is unreliable

**Why not chosen**: Startup cost makes it impractical for the primary use
case (repeated scans during development).

### Alternative 3: In-process servers (no gRPC, direct function calls)

**Description**: Run the engine and validators as library calls inside the
CLI process, but behind a clean interface that matches the gRPC contract.

**Pros**:
- Zero network overhead
- Single process

**Cons**:
- Still a fat CLI (same dependency tree)
- Does not test the same code path as production
- A web UI still cannot reuse the CLI's in-process wiring

**Why not chosen**: Preserves the core problem (CLI embeds everything) and
does not enable multi-client reuse.

## Consequences

### Positive

- **Single orchestrator.** The Primary owns all orchestration logic (scan,
  format, remediate). Any gRPC client (CLI, web UI, CI) gets the same
  capabilities.
- **One code path.** The CLI always speaks gRPC — no dual branching, no
  subtle divergence between local and pod modes.
- **Web UI enablement.** A web UI becomes a second gRPC client to the same
  Primary. No reimplementation of the convergence loop needed.
- **Thin CLI is Rust-rewritable.** Once the CLI is pure gRPC client + file
  I/O + output rendering, it could be rewritten in Rust for fast startup
  and single-binary distribution.
- **Same architecture everywhere.** Local dev, CI, and production all use
  the same gRPC protocol and service topology. Bugs found locally reproduce
  in production.

### Negative

- **Daemon lifecycle management.** Stale daemons, port conflicts, version
  mismatches need handling. Mitigated by version gating in the state file
  and auto-restart on mismatch.
- **Localhost gRPC overhead.** Adds serialization/deserialization for what
  was previously in-process function calls. For the typical workload (tens
  of YAML files), this is negligible — the scan + transform time dominates.
- **Migration effort.** Existing CLI tests that exercise local-mode code
  paths will need to be updated to test against a running daemon instead.

### Neutral

- The `apme-engine` Python package still contains all engine code. The
  daemon runs the same code the containers run. No code is deleted — it
  just stops being imported by the CLI.

## Implementation Notes

### New gRPC RPCs on Primary

Add to `proto/apme/v1/primary.proto`:

```protobuf
service Primary {
  // ... existing RPCs ...
  rpc FormatStream(stream ScanChunk) returns (FormatResponse);
  rpc FixStream(stream ScanChunk) returns (FixResponse);
}

message FixOptions {
  int32 max_passes = 1;
  string ansible_core_version = 2;
  repeated string collection_specs = 3;
  repeated string exclude_patterns = 4;
}

message FixResponse {
  repeated FileDiff format_diffs = 1;
  repeated FilePatch remediation_patches = 2;
  FixReport report = 3;
  bool idempotency_ok = 4;
}

message FilePatch {
  string path = 1;
  bytes original = 2;
  bytes patched = 3;
  string diff = 4;
  repeated string applied_rules = 5;
}

message FixReport {
  int32 passes = 1;
  int32 fixed = 2;
  int32 remaining_ai = 3;
  int32 remaining_manual = 4;
  bool oscillation_detected = 5;
  repeated Violation remaining_violations = 6;
}
```

`FixStream` reuses `ScanChunk` for file streaming (same chunked FS model as
`ScanStream`). The first chunk carries `FixOptions`. The Primary runs all
three phases (format, idempotency, remediate) server-side and returns the
combined result.

`FormatStream` is the streaming variant of the existing `Format` RPC for
large projects.

### Local daemon

New module `src/apme_engine/daemon/launcher.py`:

- `start_daemon()` — fork a background process that starts Primary + Native
  + OPA validators as async gRPC servers on localhost. Write PID and ports
  to `~/.apme/daemon.json`. Optional services (Ansible, Gitleaks, Cache)
  start lazily on first use.
- `stop_daemon()` — read PID from state file, SIGTERM, remove state file.
- `daemon_status()` — check PID liveness, return port info and uptime.
- `ensure_daemon()` — called by CLI before each command: check for running
  daemon, auto-start if not found, wait for health.

State file (`~/.apme/daemon.json`):

```json
{
  "pid": 12345,
  "primary": "127.0.0.1:50051",
  "version": "0.1.0",
  "started_at": "2026-03-18T16:30:00Z"
}
```

Version field enables auto-restart when the installed package is updated.

### Backend discovery order

1. `APME_PRIMARY_ADDRESS` env var — explicit, wins always (pod, CI)
2. `~/.apme/daemon.json` exists and PID is alive — reuse running daemon
3. Nothing found — auto-start daemon, wait for health, then proceed

### CLI subcommand mapping (after refactor)

| Subcommand     | gRPC RPC                              | CLI responsibility               |
| -------------- | ------------------------------------- | -------------------------------- |
| `scan`         | `ScanStream` (existing)               | Chunk files, render output       |
| `format`       | `FormatStream` (new)                  | Chunk files, apply diffs         |
| `fix`          | `FixStream` (new)                     | Chunk files, apply patches       |
| `cache`        | CacheMaintainer RPCs (existing)       | Dispatch to cache service        |
| `health-check` | Health RPCs (existing)                | Render status                    |
| `daemon`       | N/A (local process management)        | start / stop / status            |

The `session` subcommand becomes unnecessary — venv lifecycle is managed by
the Ansible validator service, not the CLI.

### Phased rollout

**Phase A — Daemon + wire existing RPCs.** Build the daemon launcher and
`daemon` subcommand. Wire `format` to the existing `Format` RPC and `cache`
to the existing CacheMaintainer RPCs. Keep local-mode fallback as safety net.

**Phase B — Add FixStream + FormatStream.** Add proto definitions, regenerate
stubs, implement `FixStream` in `primary_server.py` (reuses existing scan +
validator fan-out, adds formatter + `RemediationEngine`). Wire CLI `fix` and
`format` to the streaming RPCs.

**Phase C — Split cli.py.** Break the 1,315-line monolith into a `cli/`
package: `parser.py`, `scan.py`, `fix.py`, `format.py`, `output.py`,
`daemon.py`.

**Phase D — Remove local-mode code.** Once the daemon is stable, remove the
local-mode branches. The CLI becomes a pure gRPC client with no engine
imports. At this point a Rust CLI rewrite is feasible.

### What the thin CLI keeps

- Argument parsing and subcommand dispatch
- Backend discovery (env var > daemon.json > auto-start)
- File chunking via `yield_scan_chunks()` (already in `chunked_fs.py`)
- Applying returned patches/diffs to local files
- Output rendering (tables, JSON, diagnostics)
- Daemon lifecycle management (start/stop/status)

### What moves out of the CLI

- `ruamel.yaml` / `FormattedYAML` / `FormattedEmitter` (824 lines)
- `RemediationEngine` + all transforms + `StructuredFile`
- ARI engine (`runner.py`, `scanner.py`, annotators)
- All validators (OPA, Native, Ansible, Gitleaks)
- Venv management (`resolve_session`, `resolve_venv_root`)
- `formatter.py` (400 lines)
- Orchestration logic (format → idempotency → scan → remediate loop)

These remain in the `apme_engine` package for the daemon/containers but are
no longer imported by the CLI.

## Related Decisions

- ADR-001: gRPC for all inter-service communication — this ADR restores
  compliance by removing the CLI's local-mode bypass
- ADR-004: Podman pod deployment — the daemon provides the same topology
  on localhost without requiring containers
- ADR-007: Async gRPC servers — the daemon reuses the existing async server
  implementations
- ADR-009: Separate remediation engine — `FixStream` moves the remediation
  convergence loop into the Primary, where it belongs
- ADR-011: YAML formatter pre-pass — `FixStream` runs the formatter as
  Phase 1 server-side, matching the current `fix` pipeline

## References

- Architecture review and plan: `.cursor/plans/thin_cli_refactor_e47328df.plan.md`
- Engine architecture review: `.cursor/plans/engine_architecture_review_5e8be78c.plan.md`
- PR #34 review: identified `collection_specs` divergence between local and
  gRPC scan paths — a direct consequence of dual code paths

---

## Revision History

| Date       | Author        | Change           |
|------------|---------------|------------------|
| 2026-03-18 | Architecture review | Initial proposal |
