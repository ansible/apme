# ADR-038: Public Data API for Platform Consumers

## Status

Accepted

## Date

2026-03-25

## Context

APME is the source of truth for static analysis data about Ansible content — violations, health scores, deprecation status, remediation guidance. The Gateway (ADR-029) already persists scan results in SQLite and serves them via REST endpoints under `/api/v1`. However, this API was designed as a backend-for-frontend (BFF) for the APME dashboard UI, not as a public interface for platform consumers.

Customer RFE [AAPRFE-1607](https://redhat.atlassian.net/browse/AAPRFE-1607) and PR #102/#107 discussions revealed a broader architectural question: how do platform components — AAP Controller, Automation Analytics, CI/CD systems — consume APME's project quality data?

Three forces shape this decision:

1. **APME is static analysis; AA is runtime.** Automation Analytics collects data during job execution. APME scans content before it runs. These are complementary data planes. AA already gets runtime deprecation warnings from ansible-core; what it lacks is the static analysis perspective. Pushing APME data into AA conflates them.

2. **Correlation requires Controller.** AA sees job runs but may not know which SCM repository a job template points to. Controller knows the project URL (SCM repo). APME indexes by project URL (ADR-037). The correlation key is the project URL, and Controller is the component that has it — not AA, not APME.

3. **APME should not know consumer schemas.** Building integrations for each consumer (AA API, Insights client, Controller callback) means APME must understand every consumer's data model and transport. This inverts the dependency: APME should expose its data; consumers should come and get it.

### The chuck wagon principle

Think of APME as a chuck wagon on a cattle drive. The cook prepares the food (scan data, violations, health scores) and has it ready at the wagon. When a meal is ready, the cook rings the dinner bell triangle — but doesn't carry custom plates to every cowboy's tent. The cowboys hear the bell, walk over, and serve themselves from the spread. One wants steak (deprecated module violations). Another wants beans (a health score for a pre-flight gate). They all come to the same wagon.

The push model — where APME builds integrations to deliver data into AA's schema, Controller's callback format, and Insights' transport — is the cook running around camp with custom plates, needing to know who's vegetarian, who wants extra sauce, and where everyone sleeps. It doesn't scale and it's not the cook's job.

APME rings the bell (webhook notification) and serves the food (REST API). Consumers come and get it.

### What already exists

The Gateway (ADR-029, ADR-037) provides:

- **Project model** with `repo_url`, `branch`, `health_score` (0–100)
- **REST endpoints**: project read (list/detail), scans per project, violations per project, trends, dashboard summary, rankings
- **SQLite persistence** with violation details, proposals, scan logs
- **Health score computation** from violation severity/counts
- **Pluggable event sinks** (ADR-020) for scan completion notifications

What is missing: documentation as a public contract, authentication for machine consumers, a notification mechanism for external subscribers, and a stability commitment.

## Decision

**We will designate the Gateway REST API as APME's public data-sharing interface for platform consumers, using a pull model with optional webhook notifications.**

APME does not push data to individual consumers. Instead:

1. **Consumers query the Gateway** by project ID to get scan status, violations, health scores, and trends. A planned extension will add lookup by project URL via `GET /api/v1/projects?repo_url=...`.
2. **Webhook notifications** alert consumers when new scan data is available, so they can pull fresh results on demand rather than polling.
3. **The project URL is the correlation key.** Controller knows the SCM URL for each job template project and can map it to an APME project ID. Once URL-based lookup is available, Controller will query APME directly by SCM URL. AA gets APME data transitively through Controller — it does not need its own APME integration.

### Consumer interaction model

```
┌─────────────────────────────────────────────────────────────────────┐
│                      APME Gateway (public API)                      │
│                                                                     │
│  GET /api/v1/projects/{id}            → project + health score               │
│  GET /api/v1/projects/{id}/scans      → scan history                         │
│  GET /api/v1/projects/{id}/violations → filterable violation list             │
│  GET /api/v1/projects/{id}/trend      → scan-over-scan trend data            │
│  GET /api/v1/dashboard/summary        → org-wide health overview             │
│  GET /api/v1/projects?repo_url=...    → project lookup by URL (planned)      │
│  GET /api/v1/projects/{id}/health     → health gate answer (planned)         │
│  POST /api/v1/webhooks                → register notification URL (planned)  │
│                                                                     │
└────────────┬──────────────────────┬───────────────────┬─────────────┘
             │                      │                   │
             ▼                      ▼                   ▼
      ┌──────────────┐    ┌─────────────────┐   ┌─────────────┐
      │  Controller   │    │  CI/CD System   │   │  Other Tool  │
      │              │    │                 │   │              │
      │ Knows project│    │ Queries by repo │   │ Integrates   │
      │ SCM URL      │    │ URL for pass/   │   │ via REST     │
      │ Gates jobs on│    │ fail gate       │   │              │
      │ health score │    │                 │   │              │
      └──────────────┘    └─────────────────┘   └─────────────┘
             │
             ▼
      ┌──────────────┐
      │      AA       │
      │              │
      │ Gets APME    │
      │ data through │
      │ Controller   │
      └──────────────┘
```

### API surface for consumers

The existing Gateway routes (ADR-029) already cover most consumer needs. This ADR formalizes the implemented ones as public and calls out planned extensions separately.

**Existing endpoints (implemented)**

| Endpoint | Consumer Use Case |
|----------|-------------------|
| `GET /api/v1/projects/{id}` | Look up project by ID; response includes `repo_url` and `health_score` for correlation and gating |
| `GET /api/v1/projects/{id}/violations` | Deprecated module report, policy violations, filterable by exact `rule_id` and `severity` query parameters |
| `GET /api/v1/projects/{id}/scans` | Scan history for trending |
| `GET /api/v1/projects/{id}/trend` | Scan-over-scan improvement data |
| `GET /api/v1/dashboard/summary` | Org-wide health overview |
| `GET /api/v1/dashboard/rankings` | Project rankings by health score or violation counts |

**Planned extensions (not yet implemented)**

| Endpoint | Consumer Use Case |
|----------|-------------------|
| `GET /api/v1/projects?repo_url={url}` | Look up project by SCM URL (the correlation key) |
| `GET /api/v1/projects/{id}/health` | Pre-flight gate: is this project clean enough to run? |
| `POST /api/v1/webhooks` | Register a callback URL for scan-complete notifications |
| `DELETE /api/v1/webhooks/{id}` | Unregister a webhook |

The violations endpoint may be extended to support prefix-based rule filters via an optional `rule_id_prefix` query parameter (e.g., `M` for all modernization rules). Until then, consumers use exact-match `rule_id` and `severity` filtering.

### Webhook notifications

When a scan completes for a project, the Gateway notifies registered webhooks with a lightweight payload:

```json
{
  "event": "scan.completed",
  "project_id": "...",
  "repo_url": "git@github.com:org/playbooks.git",
  "scan_id": "...",
  "health_score": 72,
  "violation_count": 14,
  "timestamp": "2026-03-25T12:00:00Z"
}
```

The webhook tells consumers *that* new data is available. Consumers then pull the details they need from the REST API. This keeps the notification payload stable even as violation schemas evolve.

Implementation: the engine continues to use `GrpcReportingSink` (ADR-020) to push scan-completed events to the Gateway's gRPC Reporting server. After persisting the results, the Gateway looks up registered webhooks in its own store and dispatches outbound deliveries. This keeps webhook emission in the Gateway and avoids coupling webhook configuration to the engine's `EventSink` mechanism.

### Authentication

| Mode | Mechanism | When |
|------|-----------|------|
| **Standalone** (V1) | No auth; single-user assumption | Developer workstation, local pod |
| **Token-based** | `Authorization: Bearer <token>` header; tokens issued via Gateway admin endpoint | Multi-user standalone, CI integration |
| **Enterprise** | Trust identity headers from AAP Gateway (`X-User`, `X-Org`); AAP Gateway handles OAuth2/OIDC | AAP-managed deployment |

Enterprise mode is already sketched in ADR-029. Token-based auth is the new addition for machine consumers in non-AAP deployments.

### API stability

Routes under `/api/v1` are the public contract. Breaking changes (removed fields, changed semantics) require a new version prefix (`/api/v2`). Additive changes (new optional fields, new endpoints) are allowed under `/api/v1`.

## Alternatives Considered

### Alternative 1: Push model (APME sends data to each consumer)

**Description**: APME builds integrations for each consumer — AA API, Controller callback, Insights client, CI webhook — and pushes scan results to each.

**Pros**:
- Consumers get data without querying
- Familiar event-driven pattern

**Cons**:
- APME must understand each consumer's schema and transport
- N consumers = N integrations to maintain
- Coupling: APME changes break consumer integrations
- AA's schema and Controller's callback API are external dependencies APME cannot control

**Why not chosen**: Inverts the dependency. APME is the authority on scan data; consumers should adapt to APME's API, not the other way around.

### Alternative 2: Expose Primary gRPC directly

**Description**: Platform consumers speak gRPC to Primary on :50051 for scan results.

**Pros**:
- Single protocol, no REST translation
- Strongly typed contracts via protobuf

**Cons**:
- Primary is stateless (ADR-020) — it has no scan history, no project model
- Every consumer would need to scan on demand (no cached results)
- gRPC is less accessible than REST for CI/CD and web-based consumers

**Why not chosen**: Primary does not have the data consumers need. The Gateway does.

### Alternative 3: Separate public API service

**Description**: Build a new dedicated API service for external consumers, separate from the Gateway.

**Pros**:
- Clean separation of internal (UI) and external (platform) APIs
- Can evolve independently

**Cons**:
- Duplicates Gateway functionality (same data, same DB, same queries)
- Additional container to deploy and maintain
- ADR-020 already documents the extraction path for when this becomes necessary

**Why not chosen**: Premature split. The Gateway already serves the right data. Formalizing its API as public is simpler than duplicating it. The extraction path (ADR-020) remains available if internal and external API needs diverge significantly.

## Consequences

### Positive

- **Single API for all consumers** — Controller, AA, CI, and custom tools all use the same REST endpoints. No per-consumer integration code in APME.
- **Controller is the correlation bridge** — APME doesn't need to understand job templates, inventories, or AA schemas. Controller has the project URL; Controller queries APME.
- **AA gets data transitively** — no direct APME-to-AA integration needed. Controller already feeds AA through existing AAP telemetry.
- **Webhook notifications reduce polling** — consumers get notified when data changes, then pull what they need.
- **Builds on existing infrastructure** — Gateway routes, project model, health scores, and event sinks are already implemented. This ADR formalizes and extends them.

### Negative

- **Gateway becomes the external-facing surface** — availability requirements increase. If the Gateway is down, platform consumers cannot query project health.
- **API stability commitment** — the public contract constrains how quickly internal API shapes can change.
- **Token management** — token-based auth adds operational overhead for non-AAP deployments.

### Neutral

- The CLI continues to work independently, connecting to Primary directly. CLI users are unaffected.
- The existing UI BFF routes become a subset of the public API. No separate "internal" vs "external" route sets for V1.
- The `EventSink` protocol (ADR-020) already delivers scan events to the Gateway. Webhook emission is a Gateway concern — no engine-side architectural change required.

## Implementation Notes

### Webhook dispatch

The Gateway emits webhooks after persisting scan events. It already receives `FixCompletedEvent` via the gRPC Reporting service (ADR-039) and has the DB session needed to discover registered webhooks. The engine remains stateless and does not perform any webhook lookup — it simply emits events via `GrpcReportingSink` (ADR-020) and the Gateway handles delivery. Webhook failures are logged and do not block the scan persistence path (consistent with ADR-020 best-effort delivery).

### Project lookup by URL

Add a query parameter to `GET /api/v1/projects` to filter by `repo_url`, enabling consumers to look up projects by SCM URL without knowing the internal project ID. The `repo_url` field already exists on the `Project` model (ADR-037).

### CLI JSON metadata gap

The CLI `--json` output currently drops the violation `metadata` map (rule-specific fields like `resolved_fqcn`, `original_module`). This should be fixed as a separate task — it affects the CLI user experience independent of the public API.

## Related Decisions

- ADR-020: Reporting service and event delivery (pluggable sinks, best-effort model)
- ADR-029: Web Gateway architecture (REST API, enterprise auth sketch, extraction path)
- ADR-037: Project-centric UI model (project entity with `repo_url`, health score)
- ADR-012: Scale pods, not services (Gateway sits outside engine pods)
- ADR-001: gRPC for inter-service communication (Gateway is a gRPC client to Primary)

## References

- [PR #102](https://github.com/ansible/apme/pull/102) — Original AA deprecated module reporting discussion
- [PR #107](https://github.com/ansible/apme/pull/107) — Resubmission with corrected branch name
- [AAPRFE-1607](https://redhat.atlassian.net/browse/AAPRFE-1607) — Customer RFE that prompted this architectural review
- [DR-004](/.sdlc/decisions/closed/deferred/DR-004-aap-integration.md) — AAP Pre-Flight Integration (deferred)

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-25 | Brad Thornton | Initial proposal from PR #102/#107 review discussion |
| 2026-07-08 | Brad Thornton | Accepted — pull API, project lookup, and dashboard endpoints implemented; webhooks and token auth remain |
