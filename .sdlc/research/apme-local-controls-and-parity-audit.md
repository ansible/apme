# Local controls and functionality audit

Date: 2026-11-03. Scope: REQ-021, TASK-018 and TASK-019, PR #832.

Compared the Local plugin with the native pages, shared workflow and original
Portal components in this checkout. This is a source and fixture-browser review;
it does not establish live Gateway/Engine or Portal RBAC acceptance. #833 remains
open and the native UI must not be retired on the strength of these checks.

## Ordinary controls reviewed

| Surface | Controls and states | Result in this change |
| --- | --- | --- |
| Projects | Refresh, add/cancel/submit repository, repository links, previous/next, return to repositories | Shared compact action/link styling; accessible short pagination; page filter and Gateway-supported name/creation/health sorting; health/check/last-check columns restored. Page filtering is explicitly labeled and does not pretend to search unloaded pages. |
| Rules | Refresh, search, category/source selectors, rule links, severity, enablement, enforcement, reset, detail dialog, pagination | Table now exposes severity and switches directly; detail actions remain available. Exact six-level values, global scope, pending states, denied writes and reset are covered. Column sizing accommodates interactive controls instead of clipping them. |
| Health | Refresh and failed-load retry | Shared ordinary action and error-state styling. |
| Paste/upload setup | File/directory selectors and removal of a selected file | Compact file-picker and remove action. Check options, start/cancel, proposal/remediation/result controls keep their existing workflow implementation and presentation. |
| Project findings | Refresh, acknowledged visibility, pagination, existing grouped/flat/filter/detail controls | Shared refresh/pagination; existing specialist finding controls preserved. |
| Project history | Refresh, run links, pagination; run refresh/close, PR link, proposal/change/log disclosures | Consistent actions and readable links; durable results never replay a previous run. |
| Dependencies | Refresh, SBOM prepare/download, search, inventory pagination, issue links, all-findings/refresh, acknowledged visibility, finding pagination, acknowledgment reason/confirm/back/close | Consistent button/link/dialog styling. Acknowledgment retains existing project/content-or-rule scope and explicit permission. Dependency diagram opens by default below inventory, using the content graph renderer; raw text is an alternative (TASK-020, TASK-021). |
| Content graph | Refresh, Tree/Diagram/Node list, node search/selection/pagination, YAML/edge disclosures, fit/zoom/group/include controls | Diagram is the default, shared with dependencies; text tree and full node inspector remain available. Cycles, references, disconnected components and non-containment edges are represented. Rendering is bounded and diagram code remains lazy. |
| Project settings | Save, SCM provider, token visibility/removal, deletion confirmation/cancel/submit | Save remains credential-safe; health/last-check/severity metadata restored above tabs. Deletion restored behind independent host permission, name confirmation and active-operation guard. Failed project load now offers Retry. |
| AI settings | Provider refresh/add/edit/delete/confirm/cancel, engine/model selectors, model refresh, token visibility | Shared compact ordinary controls and scoped dialogs. Destructive controls are marked; configured providers remain read-only. |
| Galaxy settings | Refresh/add/edit/delete/confirm/cancel, token visibility/removal, certificate choice | Shared ordinary controls and scoped dialogs; wrapping long URLs retained. |
| Local navigation | APME expand/collapse, section links, Light/Dark/System | Existing navigation/theme behavior retained; host User settings are host-owned. |

The opt-in `ApmeButton`/`ApmeDialog` scope avoids restyling the remediation
workflow, Backstage's host pages, or Portal's existing Material UI components.
Keyboard focus, disabled states and destructive actions remain distinct.

## Follow-up corrections (TASK-020)

Project entry now discovers workflows without automatically attaching to retained
completed snapshots. Start and Resume are explicit; Next/Dismiss followed by
leaving/reopening no longer forces Create branch. The back link says All projects.

Dependency diagrams use the same D3/Dagre renderer as content graphs and appear
below inventory without a collapsed disclosure (TASK-021). Parsing uses only recorded uv
relationships; shared package/version references become shared graph nodes.
Unrecorded relationships are never inferred from inventory. Raw text remains
available and diagram complexity is bounded.

Local theme/layout styles now load with the plugin entry. Settings route imports
no longer fetch a separate lazy CSS chunk; stale asset load failures offer an
explicit reload. Browser checks with caching enabled cover Settings revisits,
visible rule switches/severity writes, and the workflow navigation sequence.

## Remaining replacement gaps discovered

These are actual gaps, not capabilities waived for Local. They are included in
#833's replacement-acceptance checklist and must be implemented/accepted before
retiring the native UI.

| Native functionality | Current replacement status |
| --- | --- |
| Global activity and durable CLI/upload session browsing | Project history and current upload results exist. Local has no equivalent global Activity/Sessions pages for historical runs without a registered repository. |
| Delete an individual recorded activity | The new durable project activity view is read-only. Native activity deletion has no equivalent control there. |
| Historical project/session trend chart | Project summary trend text and run history exist; the native historical chart is not yet reproduced in Local. |
| Whole-inventory project search and all native sort fields | This change adds an honest current-page filter and the three existing Gateway sort keys. Search across all pages and sorting by computed finding/check/last-check fields remain absent. |
| Native notification inbox/stream integration | Local host navigation does not yet compose the APME notification APIs. A host notification system alone does not establish APME integration. |
| Optional enabled-feedback entry points | Shared workflow supports feedback, but Local does not yet discover and expose the Gateway's enabled-feedback capability. |
| Dashboard, analytics, global collection/package inventories and cross-project drilldowns | Fleet aggregate scope may be Portal-only under ADR-073. Existing Portal fleet/dependency surfaces require acceptance against the agreed native capability matrix; they are not assumed equivalent simply because endpoints exist. |

Repository creation/editing/deletion, project findings/history/dependencies,
SBOM, graph, rules, health and AI/Galaxy settings were checked for source coverage.
The existing repository/paste remediation controllers remain shared. Live checks,
remediation, publish/reconnect, Portal authorization and released-artifact adoption
remain part of #833, independently of this visual audit.

## Verification

Regression tests cover rule writes/reset/denial/read-only access, project deletion
permission/confirmation/denial/active operation, failed-load retry, page filtering
and supported sorting, default-open dependency tree, and content-tree ordering,
cycles, references, disconnected nodes, safe text and rendering bounds.

Browser verification uses an isolated Local image and disposable fixture data;
no real repository, provider, project or suppression is changed. Review includes
both themes, desktop/narrow widths, populated/empty/paginated/error states and
add/edit/delete/acknowledgment dialogs. Final check results are recorded in the
linked tasks and PR validation.
