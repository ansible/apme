# APME Backstage hosts

APME owns the Gateway client, shared workflow and standard Backstage package.
RHDH Local is a personal deployment; Portal supplies catalog integration and
RBAC. Both target all APME features except fleet, which can initially be Portal
only. See [REQ-021](../../.sdlc/specs/REQ-021-ui-host-consolidation/requirement.md).

## Shared baseline

| Dependency | Initial baseline | Evidence                                                    |
| ---------- | ---------------- | ----------------------------------------------------------- |
| Backstage  | 1.54.9           | Both hosts' `backstage.json`                                |
| React/DOM  | 18.3.1           | RHDH app pin; Portal allows React 18                        |
| Router     | 6.30.6           | RHDH app pin; Portal's Router 6 range                       |
| BUI        | 0.17.1           | Both hosts' app manifests                                   |
| Node       | 24.15.0          | Latest shared declared Node major; satisfies native minimum |
| TypeScript | 5.9.3            | RHDH app pin; declarations build against React 18           |

Evidence collected 2026-10-30:
[RHDH main](https://github.com/redhat-developer/rhdh/blob/3df3b62caf41a2b7c87c6200e0fb2f8938df6a0d/packages/app/package.json)
and [Portal](https://github.com/ansible/ansible-backstage-plugins/blob/6ab7fce5a37406c8958d1dc0291a4bc96eb1d3f5/workspaces/portal-core/packages/app/package.json).
Newer upstream Backstage 1.56.2/BUI 0.19.0 and React 19 exist; the current hosts
do not declare that tuple. Registry metadata for the newest Backstage core API
still peers React 17/18 and Router 6. Package checks prove the declared initial
baseline; they do not constitute RHDH/Portal runtime certification.

The workspace root is the **repository root**, with one lockfile for these
packages. `frontend/` retains its legacy native workspace/lockfile during the
replacement. Keep both installs reproducible until native retirement. Shared
sources stay in `frontend/packages`; all new host code is under this directory.

## Develop

Use Node 24.15 or a newer Node 24 patch and npm. From the repository root, a clean
reproducible install uses `npm ci` through the dedicated environment:

```sh
tox -e frontend-ci
tox -e frontend-build,frontend-lint,frontend-unit
tox -e frontend-dev
```

The development harness runs at `http://127.0.0.1:3001` and proxies a running
Gateway at `127.0.0.1:8080`. It supplies real Backstage API context, React 18,
Router 6 and BUI; it is a developer harness rather than a supported replacement
SPA or a running RHDH distribution. Run `tox -e frontend-dev-build` to check its
browser bundle. Use a real RHDH/Portal host for deployment acceptance.

## Pack

```sh
tox -e frontend-pack -- --version 2026.10.4
tox -e frontend-pack-smoke
```

`frontend-pack` builds and stages packages without editing source manifests.
The output directory `dist/ui-packages` contains `apme-client-VERSION.tgz`,
`apme-ui-workflow-VERSION.tgz`, `apme-backstage-plugin-VERSION.tgz`, SHA256SUMS
and a source-commit artifact manifest. Release manifests pin package dependencies
to same-version GitHub Release URLs. The smoke check uses local tarball references
and verifies a clean install, Node client import, public declarations and browser bundle outside this
checkout. It does not publish anything or require a Portal repository.

## Initial coverage and remaining work

Current coverage includes independent client/workflow/plugin packages, a
CSS-free controller entry point, repository list/register/select, project findings/history/run details/dependency
inventory, and the shared Gateway operation workflow, using the original plugin transport boundary.
The workbench also includes searchable/paged rules with overrides and system health.
Model preferences and Galaxy server management are available through Settings.
Pure client/transport extraction preserves native import paths.

Before native removal, finish
AI provider administration, paste/upload, Portal contribution integration,
and actual two-host scan/remediation/SCM and permission-denial acceptance.
Existing PatternFly workflow presentation is an explicit transitional exception
to new BUI host controls. Measure real host startup/memory after a runnable RHDH
installation exists; compressed bundle sizes alone cannot establish runtime cost.

## Project views and Portal list compatibility

The workbench uses `project`, `tab` and `scan` query parameters for deep links.
The reusable findings, history, activity and dependency exports receive project
identity, an adapter and host callbacks. Portal can embed them in its own tabs
without adopting Local URLs. Switching tabs stops UI subscriptions rather than
cancelling the Gateway operation; returning to Workflow probes its current state.

Use `createPortalApmeApiAdapter` for shared project pages. It keeps the existing
catalog authorization path and normalizes the current Portal list/history
response envelopes. Legacy Portal endpoints return bounded results without a
Gateway total or forwarded pagination. These are marked `total_is_exact: false`
and displayed as available entries. This adapter cannot recover entries omitted
by the existing backend; full Portal pagination remains a TASK-005 backend
integration item. Operation/SSE responses and failed requests pass through intact.

Findings and dependency tables are paged; dependency searches use the already
loaded inventory. Run details are read-only and verify the selected project
relationship before display. That check complements the UI; Portal must still
authorize resources on the backend. Structured diff/log/tree previews mount only
when opened and expose no raw source bundle download.

## Rules, health and host permissions

`ApmeRules` and `ApmeHealth` can be embedded independently. The workbench selects
them with `view=rules` / `view=health`, retaining project/tab query parameters
for the return to the project. Only the selected view mounts. Health loads on
entry and refresh; rule searches, category/source filters and paging use the
loaded catalog. Rule details do not request a separate endpoint.

Embedded `ApmeRules` defaults to read-only. Pass `canManage` (or
`ApmeWorkbench.canManageRules`) from the host's permission decision to expose
override actions. The default Local API factory sets `canManageRules: true` for
the personal deployment. Portal must supply its own API/adapter and permission
decision; its backend still authorizes each write. Denied writes are displayed
without optimistic changes or retries to another backend.

`createPortalApmeApiAdapter` also normalizes Portal's existing rule catalog.
Portal currently collapses Gateway ERROR and CRITICAL to `critical`, renames
`aap` to `modernize`, and omits scope, registration time and proto severity.
The shared view preserves those labels, leaves omitted metadata unspecified
(numeric severity `0`), and writes only the field explicitly chosen by the user.
It does not reconstruct the lost values. Preserving the full catalog contract
in the Portal backend remains part of TASK-005.

## Model preferences and Galaxy settings

`view=settings` mounts the model preference and Galaxy components. Independent
exports are `ApmeSettings`, `ApmeModelSettings` and `ApmeGalaxySettings`.
The model preference uses the existing workflow's browser storage key and model
API, with visible storage/load errors. It does not configure AI providers.

Embedded Galaxy settings require explicit `canViewSettings` and
`canManageSettings` decisions from the host (or `canView`/`canManage` on
`ApmeGalaxySettings`). Without view permission no Galaxy request is sent. Local's
personal API factory enables both. Portal must evaluate its settings permissions;
the backend continues to authorize reads and writes.

Forms preserve existing tokens when the edit field is blank, support explicit
token removal, and show only token presence. Certificates support deployment
default, verify and skip verification where the host backend supports those
writes. Local enables `supportsGalaxyCertificateSettings`; Portal must leave it
false until its create/update routes forward `validate_certs` rather than
discarding it. Fixing that Portal backend gap remains TASK-005. No token is saved
in browser storage. Delete requires an explicit confirmation in the shared UI.

## Content graph and SBOM

The project `graph` tab uses `ApmeProjectGraph`. It loads the existing shared
renderer only on that view, with searchable/paged node details as an alternative
to the diagram. `@apme/ui-workflow/graph` owns that renderer; the native UI now
re-exports it. D3/Dagre do not enter the headless or ordinary workflow entries.
The specialist SVG view retains its existing dark palette as a documented
presentation exception; host theme/accessibility acceptance remains outstanding.
Markers have per-instance IDs and listeners/transitions are cleaned up on exit.

Dependencies includes `ApmeProjectSbom`, which requests CycloneDX only when
Prepare is pressed, then offers a download link. Object URLs are revoked on
replacement/unmount, and a project or adapter change discards the prepared file.
SBOM comes from recorded Gateway dependencies, not a raw source archive. Portal's
current catalog backend has no graph/SBOM route; it must add authorized forwarding
before those views work there. Failed/denied requests remain visible with no
direct-Gateway retry.
