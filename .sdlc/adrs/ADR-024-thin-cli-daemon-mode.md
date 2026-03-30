# ADR-024: Thin CLI with Local Daemon Mode

## Status

Implemented

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
the `check` subcommand when running inside the pod; `remediate`, `format`, and `cache`
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

- Developers need `apme check .` to "just work" without starting a pod.
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
- Unacceptable for iterative `apme remediate .` workflows
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

Added to `proto/apme/v1/primary.proto`:

```protobuf
service Primary {
  // ... existing RPCs ...
  rpc FormatStream(stream ScanChunk) returns (FormatResponse);
  rpc FixSession(stream SessionCommand) returns (stream SessionEvent);
}
```

`FormatStream` is the streaming variant of the existing `Format` RPC for
large projects.

`FixSession` is a **bidirectional streaming RPC** (supersedes the originally
proposed one-shot `FixStream`). The client streams `SessionCommand` messages
(file uploads, approvals, extend/close/resume) and receives `SessionEvent`
messages (progress, Tier 1 summary, AI proposals, results). This enables
real-time progress feedback and human-in-the-loop approval of AI-generated
fixes without re-uploading files. See ADR-028 for the full session-based
fix workflow design.

### Local daemon

New module `src/apme_engine/daemon/launcher.py`:

- `start_daemon()` — fork a background process that starts Primary + Native
  + OPA validators as async gRPC servers on localhost. Write PID and ports
  to `~/.apme-data/daemon.json`. Optional services (Ansible, Gitleaks, Cache)
  start lazily on first use.
- `stop_daemon()` — read PID from state file, SIGTERM, remove state file.
- `daemon_status()` — check PID liveness, return port info and uptime.
- `ensure_daemon()` — called by CLI before each command: check for running
  daemon, auto-start if not found, wait for health.

State file (`~/.apme-data/daemon.json`):

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
2. `~/.apme-data/daemon.json` exists and PID is alive — reuse running daemon
3. Nothing found — auto-start daemon, wait for health, then proceed

### CLI subcommand mapping (after refactor)

| Subcommand     | gRPC RPC                              | CLI responsibility               |
| -------------- | ------------------------------------- | -------------------------------- |
| `check`        | `FixSession` (no `FixOptions`; ADR-039) | Chunk files, render output       |
| `format`       | `FormatStream` (new)                  | Chunk files, apply diffs         |
| `remediate`    | `FixSession` bidi stream (ADR-028)    | Stream files, review proposals, apply patches |
| `cache`        | CacheMaintainer RPCs (existing)       | Dispatch to cache service        |
| `health-check` | Health RPCs (existing)                | Render status                    |
| `daemon`       | N/A (local process management)        | start / stop / status            |

The `session` subcommand (ADR-022) becomes a gRPC pass-through to the
Primary service (which owns session-scoped venvs via `VenvSessionManager`)
rather than managing venvs locally. The CLI's
`session list/info/delete/reap` commands remain available but delegate to
the Primary over gRPC instead of directly manipulating `~/.apme-data/`.

### Phased rollout (completed)

All four phases were implemented together in PR #43:

**Phase A — Daemon + wire existing RPCs.** Built `daemon/launcher.py` with
`start_daemon()`, `stop_daemon()`, `daemon_status()`, `ensure_daemon()`.
Wired all subcommands through gRPC.

**Phase B — Add FixSession + FormatStream.** Added `FormatStream` (unary
response from streamed chunks) and `FixSession` (bidirectional stream per
ADR-028) proto definitions, implemented in `primary_server.py`.

**Phase C — Split cli.py.** Broke the monolith into `src/apme_engine/cli/`
package: `parser.py`, `check.py`, `remediate.py`, `format_cmd.py`, `output.py`,
`daemon_cmd.py`, `health.py`, `cache.py`, `discovery.py`, `ansi.py`,
`_convert.py`, `_models.py`.

**Phase D — Remove local-mode code.** Local-mode branches removed from the
thin CLI. The old monolith (`_cli_legacy.py`) has been deleted.
The CLI imports only proto stubs, gRPC, and presentation utilities.

### What the thin CLI keeps

- Argument parsing and subcommand dispatch
- Backend discovery (env var > `~/.apme-data/daemon.json` > auto-start)
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
- ADR-009: Separate remediation engine — `FixSession` moves the remediation
  convergence loop into the Primary, where it belongs
- ADR-011: YAML formatter pre-pass — `FixSession` runs the formatter as
  Phase 1 server-side, matching the current `fix` pipeline
- ADR-028: Session-based fix workflow — `FixSession` bidirectional streaming
  RPC supersedes the originally proposed one-shot `FixStream`
- ADR-022: Session-scoped venvs — `session` CLI commands become gRPC
  pass-throughs to the Ansible validator; venv lifecycle management
  remains per ADR-022 but moves server-side

## Addendum

> **Note (ADR-039):** The user-facing terminology was renamed: `scan` → `check`, `fix` → `remediate`, `Scans` UI → `Activity`. Engine-internal names (`ScanChunk`, `scan_id`, `_scan_pipeline`) are unchanged. The `ScanStream` RPC was removed; `FixSession` serves both check and remediate modes. The `apme` binary name is unchanged.

## Future Direction: CLI → Gateway REST

ADR-024 made the CLI a thin gRPC client to the Primary. As the Gateway
matures (ADR-029, ADR-038, ADR-040), it is becoming the natural API surface
for the system — it owns persistence, context enrichment, and the public
REST API.

For CLI operations that **consume persisted data** rather than orchestrating
engine work, the Gateway REST API is a better fit than direct gRPC to
Primary:

| Operation | Current path | Future path |
|-----------|-------------|-------------|
| `sbom` | (not implemented) | CLI → Gateway REST (`GET /api/v1/projects/{id}/sbom?format=cyclonedx`) |
| `health-check` | CLI → gRPC Health probes | CLI → Gateway REST (aggregated health) |
| `session list/info` | (not implemented) | CLI → Gateway REST (persisted session data) |

Streaming operations (`check`, `remediate`) that require real-time progress
and bidirectional interaction stay on gRPC to Primary (via `FixSession`).
The Gateway may eventually proxy these over WebSocket for browser clients,
but the CLI's gRPC path remains efficient for interactive terminal use.

`apme sbom` (ADR-040) is the first CLI subcommand to use this pattern.
The CLI calls the Gateway REST API to retrieve a formatted SBOM
(CycloneDX, SPDX, etc.) from persisted manifest data — no engine RPC,
no local serializer, one code path shared with the web UI and external
consumers.

## References

- [PR #37](https://github.com/ansible/apme/pull/37): This ADR proposal and
  discussion
- [PR #34](https://github.com/ansible/apme/pull/34): Identified
  `collection_specs` divergence between local and gRPC scan paths — a
  direct consequence of dual code paths

---

## Revision History

| Date       | Author        | Change           |
|------------|---------------|------------------|
| 2026-03-18 | Architecture review | Initial proposal |
| 2026-03-19 | AI Agent      | Accepted: implemented in PR #43. Updated FixStream → FixSession (bidi, ADR-028), updated subcommand table and phased rollout to reflect implementation. |
| 2026-03-25 | APME Team     | ADR-039 addendum; subcommand table and examples: check/remediate, `FixSession` for check; module names `check.py` / `remediate.py`. |
| 2026-03-30 | Architecture review | Future direction: CLI → Gateway REST for read-heavy operations (SBOM first case) |
