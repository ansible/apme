# UI Capabilities Assessment

**Status**: Current
**Date**: 2026-03-30

## Objective

Document the current capabilities of the APME frontend UI. The UI is a
demo/reference implementation that validates the Gateway REST API
(ADR-029, ADR-038). Any presentation layer — portal, AAP, standalone
dashboard, or a system not yet conceived — should be able to replicate
these capabilities by consuming the same API surface.

## Technology Stack

| Area | Technology | Version |
|------|-----------|---------|
| Framework | React | 18.x |
| Routing | react-router-dom | 6.28 |
| Design system | PatternFly | 6.3.1 |
| UI shell | @ansible/ansible-ui-framework | vendored (`frontend/vendor/`) |
| Charts (available) | @patternfly/react-charts (Victory) | 8.3.1 |
| Code highlighting | react-syntax-highlighter (Prism) | 16.x |
| Archive download | JSZip | 3.10 |
| Build | Vite | 6.x |
| Testing | Vitest | 4.1.x |
| Testing (DOM) | Testing Library | 16.x |
| Styling | PatternFly CSS + custom `theme.css` |
| HTTP | Native `fetch` — no axios, no client cache library |
| State | Local React state (`useState`/`useReducer`) — no Redux, no React Query |
| Auth | None — unauthenticated `fetch`; any auth is Gateway/nginx layer |

### Dependencies declared but unused in application source

- `i18next` / `react-i18next` — declared, no `useTranslation` calls in `frontend/src`
- `swr` — in dependencies, no imports in `frontend/src`
- `zustand` — in devDependencies, no imports in `frontend/src`

### Vendored framework

`@ansible/ansible-ui-framework` is vendored at `frontend/vendor/ansible-ui-framework/`.
It provides the shell layout (`PageApp`, `PageFramework`, `PageMasthead`),
navigation structure (`PageNavigationItem`), table primitives, and dashboard
chart wrappers (`PageDashboardChart`, `PageDonutChart`, `PageStackHeatmapChart`).
The app's `tsconfig.json` excludes `vendor/` from typecheck.

---

## Navigation Structure

```
frontend/src/hooks/useApmeNavigation.tsx
```

| Nav Group | Route | Page Component | Visible in Nav |
|-----------|-------|----------------|:-:|
| **Overview** | `/` | DashboardPage | Yes |
| | `/analytics` | AnalyticsPage | Yes |
| **Projects** | `/projects` | ProjectsPage | Yes |
| | `/projects/:projectId` | ProjectDetailPage | No (detail) |
| **Dependencies** | `/collections` | CollectionsPage | Yes |
| | `/collections/:fqcn` | CollectionDetailPage | No (detail) |
| | `/python-packages` | PythonPackagesPage | Yes |
| | `/python-packages/:name` | PythonPackageDetailPage | No (detail) |
| **Operations** | `/playground` | PlaygroundPage | Yes |
| | `/activity` | ActivityPage | Yes |
| | `/activity/:activityId` | ActivityDetailPage | No (detail) |
| **System** | `/health` | HealthPage | Yes |
| | `/settings` | SettingsPage | Yes |

---

## Page Capabilities

### Dashboard (`/`)

**Purpose**: Aggregate project health at a glance.

**Data sources**:
- `GET /dashboard/summary` — total projects, total violations, fixable count, AI candidates
- `GET /dashboard/rankings` — sorted project lists (cleanest, dirtiest, stale, most scanned)

**Capabilities**:
- Four ranking tables: cleanest (highest health score), dirtiest (lowest health score), stale (longest since last scan), most scanned (highest scan count)
- Click-through from any project row to project detail
- Keyboard navigation (Enter on rows)

**Visualizations**: Cards with metric counts. No charts — uses HTML tables and PatternFly
`Card`, `Label`, `Flex`, `Bullseye` components.

**Not present**: Trend charts, sparklines, time-series. The API endpoints `getProjectTrend`
and `getSessionTrend` exist in `api.ts` but are not called from DashboardPage.

---

### Analytics (`/analytics`)

**Purpose**: Cross-project violation and remediation insights.

**Data sources**:
- `GET /violations/top` — most common rule violations
- `GET /stats/remediation-rates` — per-rule fix success rates
- `GET /stats/ai-acceptance` — AI proposal acceptance/rejection breakdown

**Capabilities**:
- Top violations table with rule ID, count, severity
- Remediation rates table with rule ID, fix count, fix rate
- AI acceptance table with model, total proposals, accepted, rejected, acceptance rate

**Visualizations**: HTML tables with PatternFly `Label` severity badges. No charts.

---

### Projects (`/projects`)

**Purpose**: CRUD management of scan projects (ADR-037).

**Data sources**:
- `GET /projects` — paginated list
- `POST /projects` — create
- `DELETE /projects/{id}` — delete

**Capabilities**:
- Search filter (client-side text match)
- Client-side sortable columns (name, health score, violations, last scanned)
- Create project modal (name, repo URL, branch; ansible version and collections are configured per operation via `CheckOptionsForm`)
- Kebab menu per row: edit, delete
- Row click navigates to project detail
- Empty state with create prompt

---

### Project Detail (`/projects/:projectId`)

**Purpose**: Single project deep-dive — the operational hub.

**Tabs**:

1. **Overview** — health score, violation summary, operational UI (check/remediate controls + live progress)
2. **Activity** — check/remediate buttons, scan history table linking to activity detail
3. **Violations** — current violation list with severity/rule filters
4. **Dependencies** — collections and Python packages used, linking to dependency detail pages
5. **Settings** — edit project details (name, repo URL, branch); delete project. Ansible version and collections are per-operation options, not persisted project settings.

**Data sources**:
- `GET /projects/{id}` — project detail
- `PATCH /projects/{id}` — update
- `DELETE /projects/{id}` — delete
- `GET /projects/{id}/activity` — scan history
- `GET /projects/{id}/violations` — current violations (filterable)
- `GET /projects/{id}/dependencies` — dependency manifest
- `WS /api/v1/projects/{id}/ws/operate` — live scan/remediate stream

**Live operation flow** (WebSocket):
1. User clicks Check or Remediate on Overview or Activity tab
2. `useProjectOperation` connects via `WS /api/v1/projects/{id}/ws/operate`
3. Sends `start` message with operation type + options
4. Receives streaming messages: `cloning`, `started`, `progress`, `tier1_complete`, `proposals`, `approval_ack`, `result`, `error`, `closed`
5. UI renders `OperationProgressPanel` (phase log + progress bar)
6. For remediate: `Tier1ResultsPanel` shows auto-fix diffs, `ProposalReviewPanel` shows AI proposals for interactive approval
7. Deep link support: `?action=check` auto-triggers check when idle

**Not present**: Trend chart for project health over time (API exists: `getProjectTrend`, not rendered).

---

### Playground (`/playground`)

**Purpose**: Ad-hoc upload scanning — no project setup required.

**Data sources**:
- `WS /api/v1/ws/session` — live session stream
- No REST endpoints for scan initiation

**Capabilities**:
- Drag-and-drop file/directory upload
- File picker for individual files
- Directory picker for folder upload
- Ansible version selector, collections CSV input, AI toggle (`CheckOptionsForm`)
- Live scan progress via WebSocket (`useSessionStream`)
- Tier 1 auto-fix diff display (`Tier1ResultsPanel`)
- AI proposal interactive approval (`ProposalReviewPanel`)
- JSZip download of remediated files on completion
- Session reconnect on dropped WebSocket (`resumeSession`, `canReconnect`)

**WebSocket message flow**:
1. Connect → send `start` → stream `file` messages → `files_done`
2. Receive: `session_created`, `progress`, `tier1_complete`, `proposals`, `approval_ack`, `result`, `error`, `closed`, `expiring`
3. Approval: user selects proposals → `{ type: "approve", approved_ids }` → `approval_ack` → `result`

---

### Activity (`/activity`)

**Purpose**: Global scan history across all projects and playground sessions.

**Data sources**:
- `GET /activity` — paginated list
- `GET /activity/{scanId}` — detail
- `DELETE /activity/{scanId}` — delete

**Capabilities**:
- Paginated table with status, project, timestamp, violation counts
- Filter by session ID (`?session_id=`)
- Click-through to activity detail

---

### Activity Detail (`/activity/:activityId`)

**Purpose**: Deep inspection of a completed scan result.

**Data sources**:
- `GET /activity/{id}` — full scan result with violations, proposals, pipeline log, diagnostics

**Capabilities**:
- Job-style output with expandable sections
- Violation list grouped by file, with severity badges and rule IDs
- Severity/rule/text filter toolbar (`ViolationOutputToolbar`)
- Violation detail modal with tabs: snippet (syntax-highlighted via Prism), rule description, metadata
- AI proposal history table (status, model, confidence) — read-only (not interactive)
- Pipeline phase log (`PipelineLogOutput`)
- Diagnostics section (timing, validator health)
- Delete scan button
- Feedback modal for reporting false positives (`FeedbackModal` → `POST /feedback`)

---

### Collections (`/collections`)

**Purpose**: Cross-project collection dependency inventory.

**Data sources**:
- `GET /collections` — all known collections with usage counts

**Capabilities**:
- Search filter
- Sortable columns (name, version, project count)
- Click-through to collection detail

---

### Collection Detail (`/collections/:fqcn`)

**Purpose**: Single collection view with which projects use it.

**Data sources**:
- `GET /collections/{fqcn}` — collection metadata + project list

**Capabilities**:
- Collection metadata display (FQCN, version, source)
- Projects using this collection, with links to project detail

---

### Python Packages (`/python-packages`)

**Purpose**: Cross-project Python dependency inventory.

**Data sources**:
- `GET /python-packages` — all known packages with usage counts

**Capabilities**: Same pattern as Collections — search, sort, click-through.

---

### Python Package Detail (`/python-packages/:name`)

**Purpose**: Single package view with which projects use it.

**Data sources**:
- `GET /python-packages/{name}` — package metadata + project list

**Capabilities**: Same pattern as Collection Detail.

---

### Health (`/health`)

**Purpose**: System health monitoring.

**Data sources**:
- `GET /health` — gateway, database, and component health status

**Capabilities**:
- Component health cards with status indicators
- Manual refresh button
- PatternFly `Label` color-coded status (green/red/yellow)

---

### Settings (`/settings`)

**Purpose**: User preferences.

**Data sources**:
- `GET /ai/models` — available AI models

**Capabilities**:
- Default AI model selector (persisted to `localStorage` key `apme-ai-model`)
- No server-side settings persistence — browser-local only

---

## Shared Components

### Scan Operation Components

| Component | Purpose | Used by |
|-----------|---------|---------|
| `CheckOptionsForm` | Ansible version, collections, AI toggle inputs | ProjectDetailPage, PlaygroundPage |
| `OperationProgressPanel` | Phase log + PatternFly `Progress` bar | ProjectDetailPage, PlaygroundPage |
| `OperationResultCard` | Summary counts after scan completes | ProjectDetailPage, PlaygroundPage |
| `Tier1ResultsPanel` | Auto-fix diffs for Tier 1 transforms | PlaygroundPage (primary), ProjectDetailPage |
| `ProposalReviewPanel` | AI proposal select/expand/approve/skip | ProjectDetailPage, PlaygroundPage |
| `DiffView` | Unified diff rendering (colored `<pre>`) | Tier1ResultsPanel, ProposalReviewPanel |

### Violation Display Components

| Component | Purpose | Used by |
|-----------|---------|---------|
| `ViolationOutput` | Grouped violation list by file | ActivityDetailPage, ProjectDetailPage |
| `ViolationDetailModal` | Modal with tabs: snippet, rule description, metadata | ViolationOutput |
| `ViolationOutputToolbar` | Search + severity/rule chip filters | ActivityDetailPage |
| `ViolationStatusBar` | Violation count badges | Multiple pages |
| `SeverityStatusBar` | Proportional severity bar (color segments) | DashboardPage, ProjectDetailPage |

### Shell Components

| Component | Purpose |
|-----------|---------|
| `ApmeMasthead` | Brand, theme switcher, notifications icon, help/about modal |
| `StatusBadge` | Activity status chip (running, completed, failed) |
| `FeedbackModal` | Submit false positive reports → `POST /feedback` |
| `PipelineLogOutput` | Phase-grouped pipeline execution log |

---

## API Surface Consumed

### REST Endpoints

All paths are relative to the `/api/v1` base path (set in `frontend/src/services/api.ts`).

```
GET  /health
GET  /sessions                           (defined, unused in UI)
GET  /sessions/{id}                      (defined, unused in UI)
GET  /sessions/{id}/trend                (defined, unused in UI)
GET  /activity
GET  /activity/{scanId}
DELETE /activity/{scanId}
GET  /violations/top
GET  /stats/remediation-rates
GET  /stats/ai-acceptance
GET  /ai/models
POST /projects
GET  /projects
GET  /projects/{id}
PATCH /projects/{id}
DELETE /projects/{id}
GET  /projects/{id}/activity
GET  /projects/{id}/violations
GET  /projects/{id}/trend                (defined, unused in UI)
GET  /projects/{id}/dependencies
GET  /dashboard/summary
GET  /dashboard/rankings
GET  /collections
GET  /collections/{fqcn}
GET  /python-packages
GET  /python-packages/{name}
GET  /feedback/enabled
POST /feedback
```

### WebSocket Endpoints

```
WS  /api/v1/ws/session                   Playground ad-hoc scan
WS  /api/v1/projects/{id}/ws/operate     Project check/remediate
```

### Unused API functions

`api.ts` defines functions that no page currently calls:
- `listSessions` / `getSession` — session-level endpoints (activity endpoints used instead)
- `getSessionTrend` — session trend data
- `getProjectTrend` — project trend over time

These represent API surface available for future visualization work.

---

## Visualization Capabilities

### Currently rendered

- **PatternFly `Progress` bar** — scan progress percentage
- **SeverityStatusBar** — proportional colored bar showing error/warning/info ratios
- **PatternFly `Label`** — color-coded severity badges, status chips
- **Prism syntax highlighting** — violation snippet display in modal
- **DiffView** — colored unified diff for Tier 1 and AI transforms
- **HTML tables** — all data display uses PatternFly table patterns or custom `<table>` elements

### Available but unused in application pages

- **@patternfly/react-charts** — declared dependency (8.3.1), wraps Victory/D3
- **Vendor chart components** — `PageDashboardChart`, `PageDonutChart`, `PageStackHeatmapChart` exist in vendor framework
- **Trend API endpoints** — `getProjectTrend`, `getSessionTrend` return `TrendPoint[]` data but no page renders them

### Not present

- Line charts, bar charts, pie charts, donut charts (libraries available, not used)
- Time-series trend visualizations
- Heatmaps
- Topology/graph visualizations
- Sparklines

---

## Remediation Workflow

The UI supports the full `FixSession` bidirectional streaming workflow (ADR-039):

1. **Initiation** — check or remediate triggered from Project Overview/Activity tab or Playground
2. **Progress tracking** — real-time phase updates via WebSocket, rendered in `OperationProgressPanel`
3. **Tier 1 auto-fixes** — deterministic transforms displayed as diffs in `Tier1ResultsPanel`
4. **Tier 2 AI proposals** — interactive approval in `ProposalReviewPanel`:
   - Select/deselect individual proposals
   - Expand to see explanation + diff
   - "Apply N Selected" or "Skip All"
   - Declined proposals shown in expandable section with "Why?" and suggestion fields
5. **Completion** — `OperationResultCard` shows summary; PlaygroundPage offers JSZip download of remediated files
6. **History** — `ActivityDetailPage` shows read-only record of all proposals, their status, and applied patches
7. **Feedback** — `FeedbackModal` (when enabled) allows reporting false positives or bad AI proposals

---

## Build and Deployment

- **Dev server**: `npm run dev` → Vite on port 3000, proxy `/api` → `localhost:8080`
- **Build**: `npm run build` → TypeScript check + Vite build → `frontend/dist/`
- **Container**: `containers/ui/Dockerfile` — multi-stage Node build + nginx Alpine
- **Production**: nginx on port 8081 serves SPA with `try_files` fallback, reverse-proxies `/api/` to Gateway on port 8080 with WebSocket upgrade headers
- **Version**: `__APME_VERSION__` injected from `package.json` at build time via Vite define

---

## Testing

| Test file | Coverage |
|-----------|----------|
| `api.test.ts` | REST client URL construction, fetch mocking |
| `AppShell.test.tsx` | Smoke render with MemoryRouter |
| `StatusBadge.test.tsx` | Component rendering by status |
| `format.test.ts` | Formatting utility functions |
| `ruleDescriptions.test.ts` | Rule description data integrity |

Vitest with jsdom environment. Setup file mocks `window.matchMedia`.
No integration tests, no E2E tests, no visual regression tests.

---

## Architectural Observations

### Presentation-layer independence

The UI communicates exclusively through the Gateway REST API and WebSocket endpoints.
It has no direct dependency on the engine, validators, or any gRPC service. This
validates ADR-029's design: any presentation layer that speaks HTTP + WebSocket to the
Gateway can replicate everything the UI does.

### Unused potential

The charting libraries (@patternfly/react-charts, Victory) and trend API endpoints
are available but unused. Adding trend visualizations, health score charts, or
remediation rate graphs requires only frontend work — the data is already served
by the Gateway.

### No server-side UI state

All UI preferences are browser-local (`localStorage`). The Gateway stores scan data
and project configuration; the UI stores only the selected AI model preference. This
means the UI is fully stateless from the server's perspective — any browser can
connect and see the same data.

### Demo status

The UI is a reference implementation, not the final presentation layer. It
demonstrates that the Gateway API is sufficient for a full-featured dashboard
including project management, live scan operations, interactive AI remediation
approval, dependency tracking, and cross-project analytics. The same API surface
could be consumed by a Backstage plugin, an AAP dashboard page, a mobile app, or
any other frontend — the Gateway is the integration point, not the UI.
