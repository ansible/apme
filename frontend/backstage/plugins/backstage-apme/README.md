# @apme/backstage-plugin

APME-owned Backstage integration built on the existing Portal plugin's
discovery/authenticated-fetch adapter and the current `@apme/ui-workflow`.
The standard package requires no private Portal SDK or AAP dependencies.

The page lists and registers repositories, preserves branch/project identity,
and provides findings, history/run details, dependency inventory and the existing
assessment/remediation workflow, rule catalog, overrides, system health,
model preferences, Galaxy settings, content graphs and CycloneDX SBOM download. It uses
BUI for host controls and shared workflow findings, proposal review, progress
and publishing. Remaining feature migration is tracked
in REQ-021; this package does not yet replace the existing Portal plugin's full
catalog/SDK contribution surface.

## Standard host

Compose individual views without taking the full-page shell:

| Entry point      | Exports                                      | Capability                  |
| ---------------- | -------------------------------------------- | --------------------------- |
| `./workflow`     | `ApmeProjectWorkflow`                        | `apme.project.workflow`     |
| `./findings`     | `ApmeProjectFindings`                        | `apme.project.findings`     |
| `./activity`     | `ApmeProjectHistory`, `ApmeActivityDetails`  | `apme.project.activity`     |
| `./dependencies` | `ApmeProjectDependencies`, `ApmeProjectSbom` | `apme.project.dependencies` |
| `./graph`        | `ApmeProjectGraph`                           | `apme.project.graph`        |

Each entry exports its props and a `capability` descriptor. `./capabilities`
exports the complete `apmeProjectViews` registry and `ApmeProjectView` type.
Hosts advertise `projectViews` through `ApmeHostApi` or workbench/tab props.
An explicit empty array enables no read views; omitted configuration preserves
existing views. A disabled deep-linked view falls back to the first supported
view without loading the disabled content. Write permissions remain independently
opt-in, including repository creation. A capability declaration never grants backend access.

The same artifact supports personal and enterprise audiences. Hosts provide
scope, catalog context, entitlements and RBAC, and choose entry points or variants
through props; the views do not detect which host is running.

Install a released plugin tarball into the host. Its manifest pins the client
and workflow tarballs from the same APME product release. Ensure the host provides
React 18 / Router 6 peer versions in the repository compatibility table,
`BUIProvider` context and `@backstage/ui/css/styles.css`. Set BUI's native
`data-theme-mode="light|dark"` on a shared ancestor of page content and body
portals, normally the document root. Local resolves host-selected custom theme
IDs and Auto/system preference to that attribute and restores it on unmount.
Shared layouts use BUI tokens and require no PatternFly reset or theme class.
Portal must retain its separate PatternFly bridge while Portal-owned PatternFly
components remain.

Native backward compatibility is waived for this BUI migration. This does not
establish native retirement or issue #833 acceptance; actual RHDH/Portal workflow,
theme, accessibility and resource acceptance remain tracked in TASK-005.

```tsx
import { ApmePage } from "@apme/backstage-plugin";

// Inside the host's FlatRoutes:
<Route path="/apme" element={<ApmePage />} />;
```

The plugin registers its API factory and resolves `discovery('proxy')/apme`.
Configure the standard Backstage proxy to target the personal Gateway:

```yaml
proxy:
    endpoints:
        "/apme":
            target: "http://127.0.0.1:8080/api/v1"
            changeOrigin: true
            credentials: require
```

Use the Gateway's reachable private service address when host/backend containers
run separately. Proxy transport must preserve streaming responses and disconnects.
`apme.apiBase` can configure another **host backend** prefix; never use a direct
Gateway prefix to bypass Portal policy. This Local profile is personal and is
not a multiple-user authorization backend.

Local uploads are disabled by default. Install and configure
`@apme/backstage-plugin-backend`, then explicitly set
`apme.uploads.enabled: true` to expose the upload workflow. The optional
`apme.uploadApiBase` setting changes the authenticated backend discovery URL;
it does not point directly to the Gateway.

## Portal composition

The existing Portal composition can use the shared project workflow while
retaining its catalog and RBAC adapters:

```tsx
import { ApmeProjectWorkflow, createApmeUiWorkflowAdapter } from "@apme/backstage-plugin";

const adapter = await createApmeUiWorkflowAdapter({ discoveryApi, fetchApi });
// Render inside the Portal-authorized project surface:
<ApmeProjectWorkflow projectId={project.id} adapter={adapter} />;
```

This keeps REST and SSE on `catalog/apme`, using `fetchApi` for authentication.
Portal continues to authorize resource access and every mutation in its backend.
Import the embeddable exports when the existing plugin already owns the `apme`
plugin ID; do not mount two plugins with the same ID. Private SDK entry points
remain in Portal's integration until their release contract is implemented.

## Provenance

`src/api/createApmeUiWorkflowAdapter.ts` was imported from
`ansible/ansible-backstage-plugins` commit
`6ab7fce5a37406c8958d1dc0291a4bc96eb1d3f5`, path
`workspaces/apme/plugins/backstage-apme/src/api/createApmeUiWorkflowAdapter.ts`.
Its Apache-2.0 header is preserved; its workflow import uses the new headless
entry point. No Portal SDK implementation was copied.

## Shared project data

`ApmeProjectFindings`, `ApmeProjectHistory`, `ApmeActivityDetails` and
`ApmeProjectDependencies` are independently embeddable. `ApmeProjectTabs` composes
those with the workflow and receives tab/activity callbacks from the host.

```tsx
import { ApmeProjectHistory, createPortalApmeApiAdapter } from "@apme/backstage-plugin";
const adapter = await createPortalApmeApiAdapter({ discoveryApi, fetchApi });
<ApmeProjectHistory projectId={project.id} adapter={adapter} onOpenActivity={openHostActivity} />;
```

The Portal adapter normalizes its bounded list/history responses and never
bypasses catalog authorization or rewrites operation streams. Gateway-shaped
paginated responses remain unchanged. For just the original workflow transport,
`createApmeUiWorkflowAdapter` remains available with its original behavior.

## Rules and health

`ApmeHealth` loads health once on entry and supports refresh. `ApmeRules` provides
local catalog search, category/source filters, pagination and details. It is
read-only by default. Portal evaluates its settings-management permission before
passing `canManage`; the backend remains the authority for all mutations:

```tsx
import { ApmeRules } from "@apme/backstage-plugin";

<ApmeRules adapter={adapter} canManage={settingsManageAllowed} />;
```

Use the Portal adapter for these views. It preserves Portal's existing catalog
labels, including its lossy severity/category normalization. Scope, registration
time and numeric severity are unavailable from that endpoint. Override actions
send partial updates through the original authorized route, never copy a
normalized numeric severity back implicitly, and display denied writes visibly.
The default Local factory enables rule editing for the personal deployment.
Hosts overriding `apmeHostApiRef` own its `canManageRules` decision; omission is
read-only. Real-host RBAC/theme acceptance remains in TASK-005.

## Model and Galaxy settings

Local mounts global APME settings at **APME → Settings** (`/apme/settings`)
using `ApmeSettingsPageContent` from the `./settings` entry point.
The shared workbench can hide its global settings control with
`showGlobalSettings={false}` when its host supplies that navigation.
Hosts with dedicated section routes can supply `section="repositories"`,
`"rules"`, `"health"` or `"upload"` and `showSectionNavigation={false}` to
`ApmePageContent`/`ApmeWorkbench`. The host supplies page titles; embedded hosts
that omit these props retain the workbench's section buttons and heading.

Embed `ApmeSettings` or its `ApmeModelSettings` / `ApmeGalaxySettings` children.
The model selector uses the existing workflow's browser preference and model API.
Galaxy access defaults to disabled for embedded hosts:

```tsx
import { ApmeSettings } from "@apme/backstage-plugin";

<ApmeSettings adapter={adapter} canViewSettings={settingsViewAllowed} canManageSettings={settingsManageAllowed} supportsGalaxyCertificateSettings={false} />;
```

Portal currently drops `validate_certs` from Galaxy create/update requests. Keep
certificate settings disabled until the host forwards the field. Local enables
this capability and settings view/manage access for its personal deployment.
The Galaxy form never displays saved tokens, leaves them unchanged on blank edits
and offers explicit removal; deletion has a confirmation dialog. These controls
use the existing authorized CRUD routes. The model selector manages a browser preference; provider administration is
provided by the separate component described below.

## Project settings

`ApmeProjectSettings` edits existing project metadata through the adapter's
authenticated `PATCH /projects/:id`. Supply the current `ProjectDetail`, adapter,
an authorized `canManage` decision and an `onSaved` refresh callback. The project
tabs also accept `project`, `canManageProject` and `onProjectUpdated`; the
workbench uses the host API's opt-in `canManageProjects` capability. These default
to denied for embedded hosts. Portal must authorize the selected resource and
provide its update route before opting in; no direct Gateway fallback is added.

The editor never reads back credentials, omits an untouched token from updates,
and requires explicit removal or a new value to change it. It sends only changed
metadata and reloads project details after a workbench save.

## Graph and SBOM

`ApmeProjectGraph` is independently embeddable with `projectId` and `adapter`, and
is available in `ApmeProjectTabs` as `graph`. The existing D3/Dagre renderer loads
only when the diagram mounts; its searchable node list exposes YAML and connected
edges without relying on pointer hover. `ApmeProjectSbom` uses the same props and
prepares a downloadable CycloneDX file only after an explicit user action.
The dependencies view includes that control.

The current Portal catalog router does not forward graph/SBOM endpoints. Add
authorized `GET /apme/projects/:projectId/graph` and `/sbom` routes in Portal before
enabling them there. The shared components display failures and never fall back
to a direct Gateway. The specialist graph palette remains a documented styling
exception pending actual-host theme and accessibility acceptance.

## AI administration and uploads

Settings now includes `ApmeAiProviders`, using the original plugin's provider
normalization and model merge. It shares the explicit settings view/manage props.
Deployment providers remain read-only and API keys are never stored in browser
storage. Model-save failures after credential persistence are reported explicitly.

`ApmeUploadWorkbench` accepts `adapter` and `canUpload` (default false), supports
paste/files/directories, reconnect, proposal approval and remediated ZIP download.
It requires `adapter.openSessionSocket`. Local gets this from the separately
registered `@apme/backstage-plugin-backend` plugin, configured with server-only
`apme.gatewayBaseUrl`. See the workspace README and ADR-074 for installation.

Use `createPortalProjectApiAdapter` after deploying the companion shared-data
routes. It preserves full rule metadata and project history pagination, checks
project ownership for activity, and routes AI discovery through settings RBAC.
`enableUploads` must remain false until Portal mounts the shared backend router
with its tenant/user/entitlement/scan authorization and shutdown hook.

### Shared presentation

The page and reusable views load their packaged layout stylesheet automatically.
APME uses host-provided BUI color tokens, compact tables with contained horizontal
scrolling, bounded forms and responsive filter/upload layouts. These rules apply
to APME components; host navigation and the surrounding application keep their own
styles.

Dependency views show open and acknowledged finding counts and load all findings
pages before displaying complete counts. Hosts opt into project-scoped triage
with `canAcknowledgeFindings`; read-only hosts still expose finding details.
Acknowledgment uses the existing suppression API. The confirmation describes
whether the scope is matching task content or every finding of that rule within
the project, including future checks.

Ordinary actions and dialogs use scoped compact controls, independently of the
specialist remediation UI. Rule enablement, severity and enforcement are directly
editable in the catalog when the host grants `canManageRules`. Changes affect
future checks across projects. Project deletion requires both management and
`canDeleteProjects`; embedding hosts default to denied. Local enables deletion
with name confirmation and prevents it while an operation is active.

Dependency diagrams open by default above the package inventory and use the
content graph’s D3/Dagre renderer with fit, zoom and pan. Edges come only from
the recorded uv tree; shared package/version nodes are deduplicated. Without a
recorded tree, the inventory is shown without inferred connections. Raw text
remains available via Text tree. Content graphs also default to Diagram, with
Tree and Node list alternatives. Containment forms the
hierarchy; other relationships remain explicit, and shared/cyclic nodes are
references. Trees are limited to 5,000 lines, with full node access through the
paginated inspector. See the [functionality audit](../../../../.sdlc/research/apme-local-controls-and-parity-audit.md)
for the replacement gaps that remain before native UI retirement.

Opening a project discovers the Gateway workflow but does not attach to it.
Use Resume workflow to reconnect to a live operation or uncommitted changes, or
Check repository to start a new check. Dismiss returns to the project form and
leaving/reopening the project never forces the Create branch step to reopen.
