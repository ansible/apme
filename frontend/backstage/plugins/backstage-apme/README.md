# @apme/backstage-plugin

APME-owned Backstage integration built on the existing Portal plugin's
discovery/authenticated-fetch adapter and the current `@apme/ui-workflow`.
The standard package requires no private Portal SDK or AAP dependencies.

The page lists and registers repositories, preserves branch/project identity,
and provides findings, history/run details, dependency inventory and the existing
assessment/remediation workflow, rule catalog, overrides, system health,
model preferences, Galaxy settings, content graphs and CycloneDX SBOM download. It uses
BUI for ordinary host controls and the existing PatternFly workflow for findings,
proposal review, progress and publishing. Remaining feature migration is tracked
in REQ-021; this package does not yet replace the existing Portal plugin's full
catalog/SDK contribution surface.

## Standard host

Install a released plugin tarball into the host. Its manifest pins the client
and workflow tarballs from the same APME product release. Ensure the host provides
the peer versions in the repository compatibility table and BUI/PatternFly CSS.

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
registered `@apme/backstage-backend` plugin, configured with server-only
`apme.gatewayBaseUrl`. See the workspace README and ADR-074 for installation.

Use `createPortalProjectApiAdapter` after deploying the companion shared-data
routes. It preserves full rule metadata and project history pagination, checks
project ownership for activity, and routes AI discovery through settings RBAC.
`enableUploads` must remain false until Portal mounts the shared backend router
with its tenant/user/entitlement/scan authorization and shutdown hook.
