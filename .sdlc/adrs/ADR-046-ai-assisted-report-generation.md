# ADR-046: AI-Assisted Report Generation

## Status

Proposed

## Date

2026-03-30

## Context

APME's UI includes a dashboard with project rankings and aggregate health metrics, but users cannot ask ad-hoc analytical questions about their fleet. Questions like "Which violations should we focus on across all projects?", "Which projects are improving their health?", or "How effective has AI remediation been?" require navigating multiple pages and mentally correlating data.

Several pieces of infrastructure are already in place but underutilized:

- **Rich Gateway REST API** — endpoints like `/violations/top`, `/stats/remediation-rates`, `/stats/ai-acceptance`, and per-project `/trend` are implemented in the Gateway but have no corresponding UI. The API surface covers fleet-level aggregates, per-project trends, violation frequency, dependency analysis, and AI proposal metrics.
- **PatternFly React Charts** — `@patternfly/react-charts` (Victory/D3) is a declared frontend dependency but is unused in any APME page. The vendored Ansible UI Framework includes chart components, but the APME dashboard renders only metric cards and tables.
- **Abbenay AI service** — the existing AI service ([ADR-025](ADR-025-ai-provider-protocol.md)) is available in the pod on `:50057`, accessible through Primary. It is currently scoped to Tier 2 remediation, but its chat interface (`AbbenayClient.chat()`) is a general-purpose LLM completion API.
- **Rule catalog** — [ADR-041](ADR-041-rule-catalog-override-architecture.md) (proposed) will give the Gateway a `rules` table with rule descriptions, categories, severities, and sources — the metadata needed to explain violations in human terms.

The question is how to connect these: let a user type a natural language question and get a meaningful, visual report assembled from data the system already has.

### The code generation trap

A natural first approach is to have the LLM generate UI code (HTML, JSX, or web components) on the fly. Projects like [RedHat-UX/next-gen-ui-agent](https://github.com/RedHat-UX/next-gen-ui-agent) take this direction — an LLM selects and configures UI components, transforms backend data into their data format, and renders the result. We evaluated this approach and identified three problems for APME's use case:

1. **Snapshot data model** — NGUI bakes fetched data into the component config and ships it as a static blob. APME's data lives in the Gateway's database and is queryable at any time. Embedding data in the response prevents refresh, filtering, pagination, and drill-down.
2. **Unnecessary abstraction** — APME already has a React/PatternFly frontend with a rich REST API. Adding a framework that fetches data, transforms it, serializes it into a component schema, and ships it to a frontend that could have called the API directly is round-tripping for no benefit.
3. **Fragile rendering** — LLMs produce syntactically inconsistent HTML/JSX across invocations. Styling drifts, closing tags are missed, PatternFly classes are hallucinated. Every response is a quality gamble that cannot be unit tested.

The core value of NGUI — LLM-based component selection — is replicable in a focused system prompt without the framework dependency.

### Where the LLM adds value

The LLM is not valuable for fetching or rendering data — the API and React do that. It is valuable for:

- **Intent mapping** — understanding that "focus on" means prioritize by severity x frequency, not just list
- **Composition** — deciding to pair a bar chart of top violations with a trend line and a severity donut in one report
- **Narrative synthesis** — turning `{rule_id: "M001", count: 847}` into "Your biggest modernization opportunity is FQCN adoption — 847 violations across 12 projects, all auto-fixable"
- **Recommendations** — "Consider running `apme remediate` on Project Alpha — it has the most auto-fixable violations"

This suggests a separation: the LLM decides *what to show* (a plan), and the existing infrastructure *shows it* (execution).

## Decision

**We will implement AI-assisted report generation using a report-spec architecture where the LLM generates a structured JSON plan and pre-built frontend components execute it against live API data.**

### 1. The LLM is a planner, not a renderer

The LLM receives a user's natural language query and returns a **report spec** — a JSON document describing the report structure. It never generates HTML, JSX, or any rendering code. The spec contains:

```json
{
  "title": "Fleet Violation Priority Report",
  "narrative": "Rule M001 (FQCN) accounts for 34% of all violations...",
  "sections": [
    {
      "type": "bar-chart",
      "title": "Top 10 Violations by Frequency",
      "endpoint": "/api/v1/violations/top",
      "params": {"limit": 10},
      "x_field": "rule_id",
      "y_field": "count"
    },
    {
      "type": "donut-chart",
      "title": "Violations Overview",
      "endpoint": "/api/v1/dashboard/summary",
      "fields": {"violations": "current_violations", "fixable": "current_fixable", "ai_candidates": "current_ai_candidates"}
    },
    {
      "type": "table",
      "title": "Projects Needing Attention",
      "endpoint": "/api/v1/dashboard/rankings",
      "params": {"sort_by": "total_violations", "order": "desc", "limit": 5},
      "columns": ["id", "name", "health_score", "total_violations", "days_since_last_scan", "last_scanned_at"]
    }
  ]
}
```

Each section references a Gateway API endpoint and specifies which fields to use. The frontend has pre-built components for each visualization type that call the specified endpoint and render the data.

### 2. Primary as generic inference proxy

A new `Inference` RPC on Primary provides a domain-agnostic LLM completion interface:

```protobuf
rpc Inference(InferenceRequest) returns (InferenceResponse);

message InferenceRequest {
  string system_prompt = 1;
  string user_prompt = 2;
  string model = 3;
  InferencePolicy policy = 4;
}

message InferencePolicy {
  float temperature = 1;
  int32 max_tokens = 2;
  string output_format = 3;
}

message InferenceResponse {
  string content = 1;
  string model_used = 2;
}
```

The Gateway constructs the full prompt (API catalog, visualization types, report schema, user query), calls `Primary.Inference()`, and parses the JSON response. Primary proxies to Abbenay via `AbbenayClient.chat()` and returns the raw text. Primary does not know about report schemas, visualization types, or Gateway endpoints — it is a transparent LLM bridge.

This preserves:
- **Single Abbenay entry point** — Primary is the only service with `abbenay_grpc`. No new AI client dependencies in the Gateway.
- **Clean dependency direction** — Gateway depends on Primary, not the reverse. Primary does not query the Gateway.
- **Reusability** — the `Inference` RPC is generic and available for future LLM features beyond report generation.

### 3. Dynamic data fetching, not baked-in data

Frontend visualization components call Gateway API endpoints directly, based on the report spec. The LLM never touches raw scan data or violation records. This provides:

- **Live data** — charts refresh on demand, tables paginate, filters apply
- **Low token cost** — the LLM prompt contains an API catalog (~400 tokens), not raw data (potentially thousands of tokens)
- **Interactivity** — clicking a project in a ranking table can drill down to its detail view
- **Existing infrastructure** — the Gateway endpoints already exist and are tested; no new data plumbing is needed for most queries

### 4. Curated API catalog in the system prompt

The Gateway constructs a system prompt that includes a hand-written summary of its data-read endpoints. This is a compact (~400 tokens) catalog of the ~15 GET endpoints relevant to report generation:

```
Available data endpoints (all GET, prefix /api/v1):

/dashboard/summary
  Fleet aggregates: total_projects, total_scans, total_violations,
  current_violations, current_fixable, current_ai_candidates,
  total_fixed, avg_health_score

/dashboard/rankings?sort_by=S&order=O&limit=N
  Per-project: id, name, health_score, total_violations, scan_count, last_scanned_at, days_since_last_scan

/violations/top?limit=N
  Global rule frequency: rule_id, count

/projects/{project_id}/trend?limit=N
  Time series per scan: scan_id, created_at, total_violations, fixable, scan_type

/stats/remediation-rates?limit=N
  Rule fix frequency (remediate scans): rule_id, fix_count

/stats/ai-acceptance
  AI proposal outcomes: rule_id, approved, rejected, pending, avg_confidence

/rules
  Rule catalog (ADR-041): rule_id, description, category, severity, source

...
```

The catalog is curated rather than auto-generated from the OpenAPI spec because editorial descriptions ("Fleet aggregates") are more useful to the LLM than raw schema types. A startup validation step compares the catalog against the FastAPI-generated OpenAPI spec at `/openapi.json` and logs warnings for mismatches.

### 5. Pre-built visualization component library

The frontend provides a fixed set of visualization components, each of which can render data from any API endpoint given a field mapping:

| Component | Use case | Implementation |
|-----------|----------|----------------|
| `table` | Ranked lists, detailed breakdowns | PatternFly `Table` |
| `bar-chart` | Comparing a metric across categories | `@patternfly/react-charts` (`ChartBar`) |
| `line-chart` | Trends over time | `@patternfly/react-charts` (`ChartLine`) |
| `pie-chart` | Proportions of a whole | `@patternfly/react-charts` (`ChartPie`) |
| `donut-chart` | Proportions with central metric | `@patternfly/react-charts` (`ChartDonut`) |
| `mirrored-bar` | Two-metric side-by-side comparison | `@patternfly/react-charts` (`ChartBar` mirrored) |
| `heatmap` | Two-dimensional cross-tabulation | Custom (CSS grid + PatternFly color tokens) |

The LLM selects from this constrained list. The system prompt includes usage guidance:

- Trends or "over time" → `line-chart`
- Distribution or "breakdown" → `pie-chart` / `donut-chart`
- Comparison or "top N" → `bar-chart` or `table`
- Two categorical dimensions → `heatmap`
- Always include a `table` as fallback for accessibility

The component set is extensible — adding a new visualization means adding a React component and a line to the system prompt. No LLM retraining, no framework changes.

## Alternatives Considered

### Alternative 1: Next Gen UI Agent integration

**Description**: Integrate [RedHat-UX/next-gen-ui-agent](https://github.com/RedHat-UX/next-gen-ui-agent) as the report generation engine. The agent takes user prompt + backend data, selects a UI component type via LLM, transforms data into the component schema, and renders via its PatternFly renderer.

**Pros**:
- Established project with LLM evaluation tooling and component catalog
- Supports PatternFly React client-side rendering
- Clean `InferenceBase` abstraction — adding an `AbbenayInference` provider would be ~25 lines

**Cons**:
- Snapshot-based data model: agent bakes data into component config at generation time. No live refresh, filtering, or drill-down.
- Adds a framework dependency for functionality replicable in a system prompt and pre-built components
- The core LLM task (component selection) is a narrow classification problem solvable without a framework
- APME already has the frontend, API, and AI infrastructure — NGUI solves a general problem we don't have

**Why not chosen**: The report-spec approach achieves the same result (LLM selects visualization, data rendered in PatternFly) without the dependency, with live data, and with lower token cost. The NGUI evaluation did inform the design — particularly the component metadata pattern and the value of constrained component selection.

### Alternative 2: Gateway calls Abbenay directly

**Description**: The Gateway imports `abbenay_grpc` and calls the Abbenay daemon directly for LLM inference, bypassing Primary.

**Pros**:
- Shortest code path — no proxy hop
- Gateway has full control over prompts and responses

**Cons**:
- Creates a second Abbenay client in the system. Two services managing connection state, auth tokens, and address discovery.
- Breaks the single-entry-point pattern. `ListAIModels` already goes through Primary.
- Adds `abbenay_grpc` (and its transitive dependencies) to the Gateway.
- If Abbenay addressing or auth changes, two services need updating.

**Why not chosen**: The proxy adds negligible latency (pod-local gRPC) and keeps AI infrastructure management in one place.

### Alternative 3: LLM generates rendering code (HTML/JSX)

**Description**: The LLM returns HTML, JSX, or web component markup that the frontend renders via `dangerouslySetInnerHTML` or a sandboxed iframe.

**Pros**:
- Maximum flexibility — the LLM can create any layout
- No pre-built component library needed

**Cons**:
- Fragile — LLMs produce inconsistent HTML across invocations. Missing closing tags, hallucinated CSS classes, broken layouts.
- Security — rendering LLM-generated HTML introduces XSS surface.
- Untestable — every response is unique; no unit tests for rendering.
- Expensive — generating 5000 tokens of HTML vs. 500 tokens of JSON spec.
- Inconsistent styling — generated code drifts from PatternFly design system.

**Why not chosen**: The LLM is reliable at structured decisions (which chart, which endpoint, which fields). It is unreliable at syntactically precise code generation. The report-spec approach plays to its strengths.

## Consequences

### Positive

- **Live, interactive reports** — charts refresh, tables paginate, projects drill down to detail views. Reports are not static snapshots.
- **Low token cost** — the LLM prompt is ~900 tokens (system prompt) + user query. It never processes raw data. A typical report spec response is ~500 tokens.
- **Testable** — each visualization component is a standard React component with unit tests. The report spec JSON schema can be validated before rendering.
- **Extensible** — adding a new visualization type requires one React component and one line in the system prompt. No LLM retraining, no framework changes, no proto regeneration.
- **No new dependencies** — uses Abbenay (existing), PatternFly Charts (existing dependency), Gateway API (existing). The only new code is the `Inference` RPC, the Gateway endpoint, the system prompt, and the frontend `ReportViewer` component.
- **Reusable inference RPC** — the generic `Primary.Inference()` is available for future LLM features (e.g., natural language search, violation explanation, content authoring guidance) without adding new RPCs per feature.

### Negative

- **New proto RPC** — the `Inference` RPC on Primary requires proto definition, regeneration, and coordinated deployment.
- **Prompt maintenance** — the curated API catalog in the system prompt must be updated when data endpoints change. Mitigated by startup validation against the OpenAPI spec.
- **Limited to pre-built visualizations** — the LLM cannot invent new chart types. It selects from the fixed component library. Mitigated: the library covers the standard set (bar, line, pie, donut, table, heatmap), and new types can be added without LLM changes.
- **Abbenay required** — report generation depends on the Abbenay daemon being available. The feature gracefully degrades (no reports, existing dashboard unaffected) but cannot function without an LLM backend.
- **Two-dimensional queries need new endpoints** — heatmap-style cross-tabulations (rule x project, severity x time) require new aggregation endpoints in the Gateway. See Implementation Notes.

### Neutral

- Existing dashboard and project views are unaffected. Report generation is a new page, not a modification of existing pages.
- The `Inference` RPC does not change the AI provider protocol ([ADR-025](ADR-025-ai-provider-protocol.md)). `AbbenayProvider` is unchanged — Primary calls `AbbenayClient.chat()` from the new RPC handler just as the remediation path does.
- The report spec schema is a Gateway-side concern. It is not defined in proto — it is a JSON contract between the Gateway and the frontend.

## Implementation Notes

### Phase 1: Inference RPC

1. Define `InferenceRequest`, `InferenceResponse`, and `InferencePolicy` messages in `primary.proto`
2. Add `Inference` RPC to the `Primary` service
3. Implement the handler in `primary_server.py` — construct `AbbenayClient.chat()` call from request fields, collect streaming chunks, return complete text
4. Gate on Abbenay availability (return `UNAVAILABLE` if Abbenay is not configured or not healthy)

### Phase 2: Gateway report endpoint

1. Add `POST /api/v1/reports/generate` endpoint to the Gateway router
2. Build the system prompt: visualization type catalog, curated API catalog, report spec JSON schema, selection rules
3. Call `Primary.Inference()` with the system prompt and user query
4. Parse the JSON response into a validated `ReportSpec` (Pydantic model)
5. Return the spec to the frontend
6. Add startup validation: compare curated API catalog against `app.openapi()` and log mismatches

### Phase 3: Frontend ReportViewer

1. Add a `/reports` route with a natural language input bar
2. Create `<ReportViewer>` component that renders a `ReportSpec`:
   - Title and narrative (rendered as markdown)
   - Each section rendered by its component type (`<ReportBarChart>`, `<ReportTable>`, etc.)
3. Each section component fetches data from the specified endpoint with params, extracts the specified fields, and renders using PatternFly Charts
4. Add loading/error states per section (individual sections can fail without breaking the report)
5. Add a "Regenerate" button and query history

### Phase 4: New aggregation endpoints

Add Gateway endpoints to support queries that existing endpoints cannot answer:

1. `GET /api/v1/dashboard/trends` — fleet-level health trend over time (per-project `total_violations` aggregated by scan date)
2. Enrich `GET /api/v1/violations/top` with `level` (severity) from violation data
3. `GET /api/v1/dashboard/severity` — cross-project severity breakdown (error/warning/info totals from latest scans)
4. `GET /api/v1/reports/matrix?rows={dim}&cols={dim}` — two-dimensional cross-tabulation endpoint for heatmap queries (e.g., `rows=rule_id&cols=project`)

### Sample queries and their report specs

| User query | Visualization | Endpoints used |
|-----------|---------------|----------------|
| "Which violations should we focus on?" | bar-chart + table + narrative | `/violations/top`, `/stats/remediation-rates` |
| "Which projects are improving?" | line-chart + table + narrative | `/dashboard/rankings`, `/projects/{project_id}/trend` |
| "What are the most common mistakes?" | table + narrative | `/violations/top`, `/rules` (ADR-041) |
| "How effective has AI remediation been?" | donut-chart + bar-chart + table | `/stats/ai-acceptance`, `/stats/remediation-rates` |
| "Compare project X and project Y" | mirrored-bar + line-chart + table | `/projects/{project_id}`, `/projects/{project_id}/trend` |
| "Show severity breakdown by project" | heatmap + table | `/reports/matrix?rows=project&cols=severity` |
| "Executive summary of fleet health" | donut-chart + line-chart + table + narrative | `/dashboard/summary`, `/dashboard/trends`, `/dashboard/rankings` |

## Related Decisions

- [ADR-020](ADR-020-reporting-service.md): Reporting service — the Gateway's gRPC ingestion path; `Inference` follows the same pattern of Primary → Gateway communication
- [ADR-025](ADR-025-ai-provider-protocol.md): AI provider protocol — `AbbenayProvider` is reused by the `Inference` RPC handler; no changes to the provider abstraction
- [ADR-029](ADR-029-web-gateway-architecture.md): Web Gateway architecture — the report endpoint is a new REST route on the existing Gateway
- [ADR-038](ADR-038-public-data-api.md): Public data API — the data endpoints consumed by report specs
- [ADR-041](ADR-041-rule-catalog-override-architecture.md): Rule catalog — provides rule descriptions and categories used in report narratives

## References

- [RedHat-UX/next-gen-ui-agent](https://github.com/RedHat-UX/next-gen-ui-agent): Evaluated for integration; informed the component selection pattern
- `proto/apme/v1/primary.proto` — `Inference` RPC definition
- `src/apme_engine/remediation/abbenay_provider.py` — existing `AbbenayClient.chat()` usage pattern
- `src/apme_gateway/api/router.py` — Gateway REST router (report endpoint location)
- `frontend/src/services/api.ts` — Frontend API client (report generation call)
- `@patternfly/react-charts` — PatternFly charting library (Victory/D3)

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-30 | AI-assisted | Initial proposal |
