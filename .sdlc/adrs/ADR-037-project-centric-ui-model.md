# ADR-037: Project-Centric UI Model with Session Abstraction

## Status

Implemented

## Date

2026-03-24

## Context

ADR-029 established the web gateway as a "CLI without a terminal" that translates
HTTP/WebSocket to gRPC. ADR-028 defined FixSession as a bidirectional streaming
protocol for interactive remediation. ADR-022 introduced session-scoped venvs for
warm-path performance. All three work correctly, but their implementation leaked
the engine's internal "session" concept into the user experience.

Today, users interact with the UI through sessions: they upload files, a session
is created with a TTL, they see session IDs, and they browse scan history
organized by session. Sessions are an engine optimization (venv caching, state
tracking for the approval loop), not a user concept. Users think about
**projects** — "scan my ansible-network-config repo against 2.19" — not about
managing ephemeral sessions with expiration timers.

Three forces drive this decision:

1. **SCM repos are the natural unit of work.** Users scan the same repos
   repeatedly. A project pointing at a repo gives scan history, violation trends,
   and health tracking across time — something sessions (ephemeral, TTL-bounded)
   cannot provide.

2. **Check and remediate are operations, not entities.** A check or remediate request against a
   project should carry its own parameters (ansible version, collections, enable
   AI) because users check the same project with different configurations. The
   ansible version in particular is a per-operation parameter — a user may check
   against 2.18, 2.19, and 2.20 to plan a migration. These are operation options,
   not project attributes.

3. **The engine's optimization boundary must not leak.** The gateway should
   silently derive a deterministic `session_id` from the project identity so the
   engine reuses warm venvs, but users never see or manage sessions.

### What the current UI exposes

The current SPA organizes data around sessions and scans at the top level.
Navigation includes Sessions, Activity, Top Violations, Fix Tracker, AI Metrics as
separate pages. The "New check" flow is file-upload based — users manually select
files from their local machine and upload them through a WebSocket. There is no
concept of a persistent project, no SCM integration, and no cross-project
analytics.

## Decision

**Introduce "project" as the top-level user-facing entity in the gateway and UI.
A project is pure metadata — a name, an SCM repo URL, and a branch. Sessions
become an invisible engine optimization managed by the gateway.**

### Project is metadata only

A project record stores:

- `name` — user-facing display label
- `repo_url` — HTTPS clone URL for the SCM repository
- `branch` — which branch to clone (default `main`)
- `created_at` — creation timestamp

No ansible version, no collection specs, no scan configuration. These are
per-operation parameters specified when the user triggers a check or remediate.

### Clone on demand

Each check or remediate request triggers a fresh `git clone --depth 1` of the project's
repo into a temporary directory. The gateway chunks the cloned files and opens
`FixSession` against Primary for both modes (check vs remediate determined by session options; ADR-039 — `ScanStream` removed). The temporary
directory is cleaned up after the operation completes. This ensures checks always
run against the current repo state, avoids stale persistent clones, and
eliminates clone lifecycle management (no `clone_status` state machine, no sync
endpoint, no persistent repo volume).

### Check and remediate are the same infrastructure

The gateway's project operation driver handles both modes:

1. Clone the project's repo to a temp directory
2. Derive `session_id = sha256(project.id)[:16]` (deterministic, ensures venv
   reuse across operations on the same project)
3. Chunk files via `yield_scan_chunks()`
4. Open `FixSession` to Primary (check-only vs remediate per request options / `fix_options`)
5. Stream progress to the UI via WebSocket
6. Persist results (violations, diagnostics, proposals) to DB under the project
7. Clean up temp directory

The difference between check and remediate is a flag on the request, not separate
infrastructure. The interactive approval flow (proposals → approve → apply) adds
more WebSocket messages in the remediate path but uses the same connection.

### Session hiding

The gateway derives a deterministic `session_id` from the project ID so the
engine reuses the same venv across operations on the same project. Users never
see session IDs, TTLs, or expiration warnings. The gateway manages the
FixSession lifecycle transparently. The `SessionStore` (ADR-028) and
`VenvSessionManager` (ADR-022) continue to work unchanged — only the gateway's
translation layer is new.

### Playground for ad-hoc scans

File-upload based scanning (the current UI flow) becomes a "playground" —
ephemeral, no project association, no persistence. This serves the "quick lint
check" use case without requiring project creation. The existing
`WS /api/v1/ws/session` endpoint and `session_client.py` bridge are reused
as-is for the playground.

### Global dashboard

A cross-project dashboard provides portfolio-level visibility:

- Health scores per project (0–100, computed from latest check severity breakdown)
- Violation trends (improving / declining / stable)
- Most checked / least checked projects
- Longest time since last check (stale projects)
- Top 10 cleanest vs top 10 highest-violation projects
- Overall violation counts, fix rates, AI acceptance across all projects

### DB schema changes

New `projects` table with `id`, `name`, `repo_url`, `branch`, `created_at`.

Modified `scans` table: add nullable `project_id` FK (null for playground/CLI
scans), add `trigger` field (`"cli"` | `"ui"` | `"playground"`).

Existing `sessions` table stays for reporting servicer compatibility (CLI-
initiated scans) but is removed from all user-facing APIs.

### UI hierarchy

```
Global Dashboard
  └── Project List
        └── Project Detail
              ├── Overview (health, top violations, recent activity)
              ├── Activity (history, trigger check/remediate with per-operation options)
              ├── Violations (filterable, from latest check)
              ├── Fixes (applied remediations with diffs)
              ├── AI Suggestions (proposals, approve/reject)
              └── Settings (name, repo URL, branch, delete)

Playground (separate, no project association)
```

### WebSocket protocol

New endpoint `WS /api/v1/projects/{project_id}/ws/operate` with a unified
protocol for both check and remediate. The `remediate` flag in the start message determines
the flow:

| Direction | Message | When |
|-----------|---------|------|
| Client → Server | `{"type": "start", "remediate": false, "options": {...}}` | Initiate check or remediate |
| Client → Server | `{"type": "approve", "approved_ids": [...]}` | Remediate flow: approve proposals |
| Client → Server | `{"type": "close"}` | Terminate |
| Server → Client | `{"type": "cloning"}` | Repo clone started |
| Server → Client | `{"type": "started", "scan_id": "..."}` | RPC opened (engine-internal `scan_id` unchanged) |
| Server → Client | `{"type": "progress", ...}` | Real-time progress |
| Server → Client | `{"type": "tier1_complete", ...}` | Remediate: Tier 1 results |
| Server → Client | `{"type": "proposals", ...}` | Remediate: AI proposals for review |
| Server → Client | `{"type": "approval_ack", ...}` | Remediate: approval confirmed |
| Server → Client | `{"type": "result", ...}` | Final check/remediate result |
| Server → Client | `{"type": "error", ...}` | Error |
| Server → Client | `{"type": "closed"}` | Session ended |

## Alternatives Considered

### Alternative 1: Persistent Clones with Sync

Clone once at project creation, `git pull` on each scan.

**Pros**:
- Faster subsequent scans (no re-clone)

**Cons**:
- Stale state if sync fails silently
- Disk usage for idle projects
- Clone lifecycle state machine (pending/cloning/ready/error)
- Needs persistent volume for cloned repos

**Why not chosen**: Shallow clone is fast enough. Ephemeral clones are simpler,
always current, and require no lifecycle management.

### Alternative 2: Ansible Version as Project Attribute

Store ansible version and collection specs on the project record.

**Pros**:
- Less to specify per scan

**Cons**:
- Prevents scanning the same project against multiple ansible versions — a core
  use case for migration planning (e.g., compare 2.18 vs 2.19 vs 2.20 readiness)
- Collections are often discoverable from the repo itself (requirements.yml,
  FQCNs per ADR-032)

**Why not chosen**: These are operation parameters, not project identity.

### Alternative 3: Keep Sessions in the UI, Add Project as Grouping Label

Tag sessions and scans with a project name.

**Pros**:
- Minimal code change

**Cons**:
- Doesn't solve the UX problem — users still manage sessions, see TTLs, deal
  with expiration warnings
- The session leak remains — it's a cosmetic fix, not architectural

**Why not chosen**: Does not address the fundamental abstraction mismatch.

## Consequences

### Positive

- **Users work with projects** (their mental model), not sessions (the engine's
  optimization detail)
- **Same project, different ansible versions** — migration planning supported
  natively via per-operation options
- **Cross-project dashboard** enables portfolio-level visibility: health scores,
  trends, staleness, rankings
- **Engine is unmodified** — all changes are in the gateway and frontend. Primary,
  validators, FixSession protocol, venv session manager, reporting sink are
  unchanged.
- **Playground preserves quick scanning** — ad-hoc file upload without project
  creation

### Negative

- **Fresh clone on every check adds latency** (~2–10s for shallow clone depending
  on repo size and network)
- **Gateway needs git** — new system dependency in the container image
- **More complex gateway** — SCM clone + operation driver + project CRUD vs the
  current pass-through WebSocket bridge

### Neutral

- CLI is unaffected — continues working as before via direct Primary connection
- Engine sessions, venvs, FixSession protocol all unchanged
- Reporting servicer continues to work for CLI-initiated scans
  (`project_id = null`)
- ADR-029's architecture and file ingestion paths remain valid; this ADR narrows
  "SCM ingestion" to "project-triggered on-demand clone"

## Implementation Notes

- Gateway container needs `git` installed in Dockerfile (`microdnf install -y git` on UBI10)
- No new persistent volume — clones go to temp directories
- Health score formula: `max(0, 100 - (high * 10 + medium * 3 + low * 1))`
  from latest scan, clamped to 0–100
- Violation trend: compare latest scan to previous — fewer = improving, more =
  declining, same = stable
- Session ID derivation: `hashlib.sha256(project.id.encode()).hexdigest()[:16]`

## Related Decisions

- ADR-020: Reporting service (CLI scans still flow through reporting sink)
- ADR-022: Session-scoped venvs (sessions stay, just hidden from user)
- ADR-024: Thin CLI (unaffected; gateway mirrors CLI's gRPC client role)
- ADR-028: FixSession bidi stream (gateway drives it for project remediate operations)
- ADR-029: Web gateway architecture (extended with project CRUD and operation driver)
- ADR-030: Frontend deployment model (UI restructured around projects)
- ADR-032: FQCN collection auto-discovery (collections discovered at check time)

## Addendum

> **Note (ADR-039):** The user-facing terminology was renamed: `scan` → `check`, `fix` → `remediate`, `Scans` UI → `Activity`. Engine-internal names (`ScanChunk`, `scan_id`, `_scan_pipeline`) are unchanged. The `ScanStream` RPC was removed; `FixSession` serves both check and remediate modes. The `apme` binary name is unchanged. Detail routes use **`/activity/{id}`** (replacing `/scans/{id}`) where the product exposes per-run history.

## References

- [ADR-029: Web Gateway Architecture](ADR-029-web-gateway-architecture.md)
- [ADR-028: Session-Based Fix Workflow](ADR-028-session-based-fix-workflow.md)
- [ADR-022: Session-Scoped Venvs](ADR-022-session-scoped-venvs.md)

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-24 | APME Team | Initial proposal |
| 2026-03-25 | APME Team | ADR-039 terminology: Activity, check/remediate, FixSession-only; `/activity/{id}`. |
