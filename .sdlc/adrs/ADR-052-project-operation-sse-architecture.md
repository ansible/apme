# ADR-052: Project Operation SSE Architecture

## Status

Accepted

## Date

2026-04-14

## Context

Project operations (check/remediate) today use a WebSocket tunnel: browser ↔ WS
↔ Gateway ↔ gRPC ↔ Primary. All operation state lives in React `useState` inside
the `useProjectOperation` hook. This creates several problems:

1. **State lost on navigation.** Navigating away during a 2+ minute AI proposal
   review destroys all progress. The server-side operation may still be running
   but the UI cannot reconnect.

2. **Single-viewer.** Only the originating browser tab can see the operation.
   Other tabs, other users, or even the same user returning later have no
   visibility.

3. **Tight coupling.** The AI approval flow (`proposals` → `approve`) requires
   the original WebSocket to be connected. A dropped connection means the
   Gateway's gRPC task either hangs waiting for approval or auto-declines.

4. **No discovery.** There is no API to ask "is there an operation running for
   this project?" The Gateway has no memory of in-flight operations.

The Playground already addressed session persistence via `sessionStorage` and
WebSocket `?resume=` (PR #279), but the project path is architecturally
different: the Gateway clones the repo and drives Primary's `FixSession`, so the
browser is not a direct participant in the gRPC stream.

Issue #94 identified this gap. Frontend-only persistence (sessionStorage) was
considered but rejected because it treats symptoms rather than the root cause —
the browser WebSocket is the operation's lifeline.

## Decision

Replace the project WebSocket tunnel with a **Gateway-authoritative operation
model**:

- **REST** for actions: initiate, approve, cancel, create-pr
- **SSE (Server-Sent Events)** for real-time state delivery
- **OperationRegistry** (in-memory) as the Gateway's source of truth

The Gateway becomes the owner of the operation lifecycle. It maintains the gRPC
stream to Primary independently of any browser connection. The UI is a pure
state renderer that any client can attach to at any time via SSE.

### State machine

Operations progress through 11 discrete states:

```
queued → cloning → scanning → completed → submitting_pr → pr_submitted
                      │                        │
                      ▼                        ▼ (failure → retry)
               awaiting_approval          completed
                      │
                  applying → completed
```

Terminal states: `completed`, `pr_submitted`, `failed`, `expired`, `cancelled`.

Each state maps to exactly one UI screen. When a client connects (or
reconnects), the Gateway sends a snapshot of the current `OperationState` and the
UI renders the correct panel.

### API surface

All under `/api/v1/projects/{project_id}/operation`:

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/` | Initiate check or remediate |
| GET | `/` | Current state snapshot |
| POST | `/approve` | Submit AI proposal decisions |
| POST | `/cancel` | Cancel the operation |
| POST | `/create-pr` | Create PR from remediation |
| GET | `/events` | SSE stream (snapshot + deltas) |

The existing `WS /projects/{project_id}/ws/operate` endpoint is removed.

### Why SSE over WebSocket

The operation event stream is inherently one-directional (server → client). The
only client-to-server actions are approval and cancel, which map naturally to
REST POST calls. SSE provides:

- Automatic browser reconnect via `EventSource`
- Simpler server implementation (no connection upgrade, no ping/pong)
- Easier load-balancing (standard HTTP)
- `Last-Event-ID` for resuming after reconnect

### Why in-memory

Operations are ephemeral (minutes, not hours). If the Gateway restarts, the
Primary gRPC stream dies too, so there is nothing to recover. The completed scan
is already persisted to SQLite via the reporting sink. In-memory storage keeps
the implementation simple and avoids schema changes for transient state.

## Consequences

### Positive

- Operations survive browser navigation, tab close, and page refresh
- Any client can view any running operation — multi-tab, multi-user
- AI approval is decoupled from a specific WebSocket connection
- PR creation is folded into the operation state machine, visible to all viewers
- Frontend becomes a pure state renderer — no local state machine or
  sessionStorage needed

### Negative

- Gateway memory usage increases slightly (one `OperationState` per active
  operation, with progress log buffer)
- Requires a TTL reaper to evict stale terminal states
- Existing frontend `useProjectOperation` hook must be rewritten
- The `session_client.py` playground WS path is not changed by this ADR — it
  retains its existing WebSocket model

### Constraints

- **One operation per project.** `POST /operate` rejects with 409 if the
  project already has a non-terminal operation.
- **Terminal state retention.** Completed/failed/expired operations remain in
  the registry for 10 minutes (configurable) so returning users see results.
  After that, results are available only via the persisted `scans` REST API.
- **No Primary changes.** The Primary's `SessionStore`, `FixSession` RPC, and
  proto definitions are unchanged.
- **Engine invariants preserved.** The engine never queries out (ADR-020),
  validators are read-only (ADR-009), stateless engine (ADR-029).

## References

- Issue #94 — persist active sessions and allow resume from UI
- ADR-001 — gRPC for inter-service communication
- ADR-007 — async gRPC servers
- ADR-020 — engine event sink abstraction
- ADR-029 — stateless engine, persistence at the edge
- ADR-037 — Gateway project model
- ADR-039 — FixSession as unified client path
- ADR-050 — post-remediation PR creation

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-04-14 | APME Team | Initial proposal |
| 2026-07-08 | Brad Thornton | Accepted — operation registry, SSE router, state machine, and frontend hooks implemented; legacy WebSocket removal remains |
