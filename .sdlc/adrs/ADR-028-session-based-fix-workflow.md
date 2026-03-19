# ADR-028: Session-Based Fix Workflow with Bidirectional Streaming

## Status

Proposed

## Date

2026-03-19

## Context

### Current state

ADR-024 refactored the CLI into a thin gRPC client that talks exclusively to the
Primary orchestrator. The `FixStream` RPC is one-shot: the client streams file
bytes in, the Primary runs format → Tier 1 convergence loop → post-format, and
returns a single `FixResponse` with all applied patches.

### Problems with one-shot

1. **No human-in-the-loop for AI proposals.** Tier 2 AI proposals need review
   before application. The one-shot model returns everything at once — the client
   can only accept or reject the entire result.

2. **Tier dependency on approval state.** ADR-027 introduces Tier 3 agentic
   remediation that operates on the project state *after* Tier 2 approvals. If
   the engine doesn't know which Tier 2 proposals were accepted, it can't run
   Tier 3 on the correct state. The client would have to re-submit the entire
   file tree after each approval round.

3. **No progress feedback.** The one-shot RPC blocks until the entire pipeline
   completes. For large projects with AI escalation, this can take minutes with
   no visibility into what's happening.

4. **All fixes are the same shape.** Every fix — Tier 1 deterministic, Tier 2
   AI-proposed, Tier 3 agentic — is an atomic, unit-delimited change. The
   approval model should be uniform, not split across different RPC patterns.

### Design question: polling vs. back channel

We considered three approaches for session management:

- **Separate unary RPCs** (CreateSession, GetProposals, SubmitApprovals, etc.):
  Simple but requires multiple round-trips and doesn't solve progress streaming
  or expiration notifications without additional mechanisms.

- **TTL-in-response + polling**: Client gets TTL from responses, polls for
  updates. No back channel. But wastes calls and doesn't provide real-time
  progress.

- **Bidirectional gRPC stream**: One connection handles file upload, progress
  events, approval flow, expiration warnings, and result delivery. gRPC
  keepalives handle connection health. Real-time feedback from the start.

> "I've done grpc back channels in abbenay, not too bad. Doesn't that solve the
> approval/notification/logging issues out of the gate rather than rework later?"
> — user decision

Modern networks are stable. WebSocket-like persistent connections are the
foundation of every real-time application (Slack, VS Code, collaborative
editors). gRPC keepalives handle connection health natively.

## Decision

**Replace `FixStream` with a single bidirectional streaming RPC: `FixSession`.**

An ephemeral session assistant on the Primary holds working state per session.
The engine (scan, remediate, format) stays stateless. The session streams
progress events and waits for approval commands. All clients (CLI, web UI, CI)
use the same pipeline — `--auto-approve` is just the client sending `Approve`
immediately.

### Architecture

```
Client (stream)                        Session Assistant              Engine (stateless)
─────────────────                      ──────────────────             ──────────────────
ScanChunk(files, last=true) ──────>    
                             <──────── SessionCreated(id, ttl)
                                       ──► format()
                             <──────── Progress("Formatting 47 files...")
                                       ──► scan() + Tier 1 convergence
                             <──────── Progress("Tier 1 pass 1: 12 fixes")
                             <──────── Progress("Tier 1 converged")
                             <──────── Tier1Summary(patches, diffs)
                                       ──► Tier 2 AI escalation
                             <──────── Progress("AI unit 3/8: M-301...")
                             <──────── ProposalsReady(proposals)

  (user reviews interactively)

Approve([id1, id3, id5])     ──────>
                                       Apply approved to working state
                             <──────── ApprovalAck(applied=3)
                                       ──► Tier 3 agentic (future)
                             <──────── SessionResult(patched_files, report)

Close()                      ──────>
                             <──────── SessionClosed()
```

### gRPC contract

```protobuf
service Primary {
  rpc FixSession(stream SessionCommand) returns (stream SessionEvent);
}
```

**Client → Server** (`SessionCommand` oneof):

| Command | Purpose |
|---------|---------|
| `ScanChunk upload` | File upload (reuses existing chunking) |
| `ApprovalRequest approve` | Approve/reject proposals by ID |
| `ExtendRequest extend` | Reset idle timer |
| `CloseRequest close` | End session |
| `ResumeRequest resume` | Reconnect to existing session after disconnect |

**Server → Client** (`SessionEvent` oneof):

| Event | Purpose |
|-------|---------|
| `SessionCreated` | Session ID + TTL |
| `ProgressUpdate` | Real-time progress with `LogLevel` (DEBUG/INFO/WARNING/ERROR) |
| `Tier1Summary` | Auto-applied Tier 1 patches, format diffs, report |
| `ProposalsReady` | Tier 2/3 proposals for review |
| `ApprovalAck` | Confirmation of applied proposals + session status |
| `SessionResult` | Final patched files + report |
| `ExpirationWarning` | Proactive warning before session timeout |
| `SessionClosed` | Cleanup confirmed |
| `DataPayload` | Generic structured data (kind + `google.protobuf.Struct`) |

### Message presentation levels

`ProgressUpdate` carries a `LogLevel` so the presentation layer knows how to
render each message:

| Level | CLI default | CLI -v | Web UI |
|-------|------------|--------|--------|
| DEBUG | hidden | dim text | console.debug |
| INFO | normal text | normal text | status line |
| WARNING | yellow | yellow | toast notification |
| ERROR | red | red | error banner |

### Structured data payloads

`DataPayload` uses `google.protobuf.Struct` (JSON-native) with a `kind`
discriminator. The server sends typed data without requiring new proto messages:

| Kind | Payload | When |
|------|---------|------|
| `fix_report` | passes, fixed, remaining counts | end of pipeline |
| `scan_diagnostics` | timing breakdown, validator stats | after scan |
| `cost_estimate` | tokens used, API calls, cost | after AI tier |

Clients route on `kind`: CLI renders human-readable or dumps JSON with `--json`;
web UI renders appropriate components.

### Session lifecycle

- **TTL**: Default 30 minutes, configurable via `APME_SESSION_TTL`.
- **Idle timeout**: `last_activity_at` updated on every command. Background
  reaper runs every 60 seconds.
- **Expiration warning**: Server sends `ExpirationWarning` at 5 minutes
  remaining. Client sends `Extend` to reset.
- **Max sessions**: Configurable limit (default 10). Exceeding returns
  `RESOURCE_EXHAUSTED`.
- **Max lifetime**: Hard cap (2 hours) prevents indefinite extension.
- **Reconnection**: Session survives server-side on disconnect. Client opens a
  new stream with `ResumeRequest(session_id)`. Server re-sends current state.

### Tier 1 auto-apply

Tier 1 (deterministic) fixes are always safe and always applied automatically.
They do not go through the approval flow. The `Tier1Summary` event reports what
was applied. Only Tier 2+ proposals require client approval.

> "Auto-apply Tier 1 (deterministic fixes are always safe, just report what was
> done)" — user decision

### --check short-circuit

`fix --check` short-circuits after Tier 1 summary — skips AI entirely. Exits
with code 1 if changes are needed (CI gate). Runs the same session pipeline
but sends `Close` immediately after `Tier1Summary`.

## Alternatives Considered

### Alternative 1: Separate unary RPCs (CreateFixSession, GetProposals, etc.)

**Description**: Five separate RPCs for session lifecycle.

**Pros**:
- Simpler per-RPC implementation
- Each call is independent and debuggable

**Cons**:
- No real-time progress without adding a sixth streaming RPC
- Expiration notifications need polling or a separate back channel
- More round-trips for the full workflow
- Would need rework when Tier 3 agentic requires progress streaming

**Why not chosen**: Builds multiple mechanisms that a single bidi stream solves.

### Alternative 2: Keep FixStream one-shot + re-submit

**Description**: Client calls FixStream, reviews result, re-submits modified
files for the next tier.

**Pros**:
- No protocol changes
- Engine stays fully stateless (no sessions)

**Cons**:
- Re-scanning on every round-trip (wasted work)
- Client manages tier progression
- No progress feedback during long pipelines
- Multiple full file transfers

**Why not chosen**: Wasteful and pushes orchestration complexity to the client.

### Alternative 3: Keep FixStream + add WatchSession for progress

**Description**: One-shot FixStream for processing, separate server-side stream
for progress events.

**Pros**:
- Progress streaming without changing FixStream

**Cons**:
- Two connections to manage
- Approval flow still needs separate RPCs
- Fragmented protocol

**Why not chosen**: Half-measure that doesn't solve the approval problem.

## Consequences

### Positive

- **Real-time feedback** from day one — progress events stream during every
  phase (format, Tier 1 passes, AI unit calls)
- **Human-in-the-loop** via the same stream — proposals, review, approve, all on
  one connection
- **Tier dependency solved** — engine knows the approved state before running
  the next tier
- **Uniform approval model** — all fixes (Tier 2, Tier 3) go through the same
  `ProposalsReady` → `Approve` → `ApprovalAck` flow
- **Extensible** — `DataPayload` carries arbitrary structured data without proto
  changes
- **Same pipeline for all clients** — CLI with `--auto-approve`, web UI with
  checkboxes, CI with auto-approve all use identical server-side logic
- **Reconnection support** — sessions survive disconnects

### Negative

- **Primary becomes session-aware** — ephemeral in-memory state per session
  (mitigated by timeout-based GC and max session limits)
- **Bidirectional streaming complexity** — more complex than unary RPCs
  (mitigated by team's existing experience with gRPC back channels in Abbenay)
- **Connection lifetime** — streams live for minutes during interactive review
  (mitigated by gRPC keepalives)

### Neutral

- Engine (scan, remediate, format) stays fully stateless — only the session
  coordinator is stateful
- `ScanStream`, `FormatStream`, `Health`, cache RPCs are unaffected
- `RemediationEngine`, `TransformRegistry`, `AIProvider` are unchanged
- Session state is ephemeral (minutes, not hours) and disposable

## Implementation Notes

### FixOptions extension

```protobuf
message FixOptions {
  int32 max_passes = 1;
  string ansible_core_version = 2;
  repeated string collection_specs = 3;
  repeated string exclude_patterns = 4;
  bool enable_ai = 5;             // run Tier 2
  bool enable_agentic = 6;        // run Tier 3 (future)
  string ai_model = 7;
}
```

### SessionState (server-side)

```python
@dataclass
class SessionState:
    session_id: str
    original_files: dict[str, bytes]
    working_files: dict[str, bytes]
    tier1_patches: list
    format_diffs: list
    proposals: dict[str, Proposal]
    current_tier: int
    fix_options: FixOptions
    report: FixReport
    temp_dir: Path
    created_at: datetime
    last_activity_at: datetime
    ai_proposals: list[AIProposal]
    idempotency_ok: bool
    status: SessionStatus
```

### FixSession handler phases

1. **Upload**: Accumulate `ScanChunk` until `last=true`. Create session.
2. **Processing**: Format → Tier 1 → Tier 2. Stream `ProgressUpdate` events.
3. **Approval**: Wait for `Approve`. Apply. Advance tier or complete.
4. **Close**: Cleanup temp_dir, remove from store.
5. **Resume**: Look up session, re-send current state.
6. **Extend**: Reset idle timer.

### CLI event loop

```python
async for event in stub.FixSession(command_iter()):
    match event.WhichOneof("event"):
        case "proposals":
            if args.auto_approve:
                ids = [p.id for p in event.proposals.proposals]
            else:
                ids = interactive_review(event.proposals.proposals)
            await command_queue.put(Approve(ids))
        case "result":
            write_patches(target, event.result.patches)
            await command_queue.put(Close())
        # ...
```

## Related Decisions

- ADR-009: Remediation Engine (tiered architecture)
- ADR-024: Thin CLI with Local Daemon Mode (CLI as gRPC client)
- ADR-025: AIProvider Protocol (Tier 2 AI integration)
- ADR-027: Agentic Project-Level AI Remediation (Tier 3, sandbox)

## References

- gRPC Bidirectional Streaming: https://grpc.io/docs/what-is-grpc/core-concepts/#bidirectional-streaming-rpc
- gRPC Keepalive: https://grpc.io/docs/guides/keepalive/
- google.protobuf.Struct: https://protobuf.dev/reference/protobuf/google.protobuf/#struct

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-19 | AI Agent | Initial proposal |
