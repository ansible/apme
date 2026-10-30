# APME UI Consolidation: Phase 0 Baseline

Status: working evidence for Phase 0. This record is not a ratified capability
matrix, architecture decision, support policy, or approval to begin production
implementation. The controlling plan is the document in [APME PR 785](https://github.com/ansible/apme/blob/4820cceb/.sdlc/research/apme-rhdh-ui-consolidation-plan.md).
The review recommendation and open questions are in the [consolidation proposal](apme-rhdh-ui-consolidation-proposal.md).

Evidence collected on 2026-10-26. Current selections follow the user direction:
use APME main as the work base and current ansible-backstage-plugins main as
the plugin source. No production code was changed; no tests or builds were run.

## P0.1: Pinned inputs and current implementation

| Input | Revision or observed value | Notes |
| --- | --- | --- |
| APME work base | main, 9c4372f79a8b537865040cea17cf9db7659614ad | Fetched from origin before creating feat/apme-ui-consolidation. |
| Plugin source and SDK source | ansible-backstage-plugins main, f3b1bf911c99f7104249105a3055b38260d52d59 | Current APME code is under workspaces/apme/plugins/. |
| Plugin SDK history | main includes workspace migration commit 48041b16b1c9ed8efb6f4c6ae3c21407caaa6d46 | The old feat/portal-plugin-factory-sdk branch is not advertised by origin. Current SDK docs still describe the implementation as experimental and unsupported. |
| APME UI package source | frontend/packages/ui-workflow at APME main | The current product tag is v2026.10.4, commit 2c68e65a1bc929f6ac7ee5b4759e68a22deedadf. |
| Portal's UI package pin | @apme/ui-workflow tarball from v2026.9.3 | Current plugin manifests lag the latest product release. The v2026.10.4 release asset exists and is 84,896 bytes. Source changes from v2026.9.3 to v2026.10.4 touch 20 files, with 2,439 insertions and 167 deletions, including operation/session stream behavior and tests. |
| Candidate RHDH Local image | quay.io/rhdh-community/rhdh@sha256:1a926f34d79e869762735971d3a6f9aa05161ec7ae32a54f1f97f92d264cf515 | This is the multi-platform manifest observed for the plan's next-2.1 tag. Linux/amd64 child digest: sha256:d7cfcd9c9e5c1c3969f8d8aa15e862be5eddc52785f51a33a90c88282ee9b7ef. Community development image only; the tag is mutable and this is not a product support commitment. |

The plan's cited plugin review SHA f6c5bfe5aa5e6ad1eb8f66eecb41a0a32f29da80
is not present in the selected plugins checkout. Per the user's direction,
current plugins main is the input; do not silently substitute the older feature
branch.

### Native APME UI

The native frontend lives under frontend/. Its routes cover dashboard and
analytics; projects and project details; collections and Python packages;
Playground; activity and activity details; sessions and session details;
health; rules; and settings. Playground currently supports uploading files for
an ad-hoc check. A paste editor is a proposed feature; it was not found in the
current Playground source.

The frontend has one npm workspace package, @apme/ui-workflow. That package
contains workflow controllers, operation-state hooks, findings/proposal
review, diff panels, and result views. Its root entrypoint imports
workflow.css, so it is not currently a presentation-free/headless entrypoint.
The UI uses PatternFly and the vendored Ansible UI framework.

The Gateway exposes /api/v1 surfaces for projects, activity/history, sessions,
rules, collections, Python packages, dependency health, graph and SBOM data,
dashboard summaries/rankings, operation state/actions/events, notifications,
and settings. The existing transport includes REST, project-operation SSE,
notification SSE, and WebSockets for interactive session and upload flows.
Endpoints existing in source do not by themselves prove each proposed user
journey or authorization model is complete.

The current native deployment includes containers/ui, the Podman pod, and the
bootc Quadlet apme-ui container. The main branch publishes the APME UI image
through the container image workflow. No deploy/helm chart is present on
selected main.

### APME plugin source on selected plugins main

Current APME packages are:

- @ansible/plugin-backstage-apme: frontend catalog/entity integration.
- @ansible/backstage-apme-common: shared APME types, API, workflow and domain
  helpers. It is currently version 2026.8.0.
- @ansible/backstage-plugin-catalog-backend-module-apme: catalog integration
  and backend operations. It is currently version 2026.8.0.
- @ansible/scaffolder-backend-module-apme: private scaffolder backend module.

The frontend plugin package is also version 2026.8.0. The three
non-private packages declare Apache-2.0; the scaffolder package is private and
also declares Apache-2.0.

The frontend contribution declarations include quality and quality-activity
tabs, a fleet-quality tab, dependency detail, settings, catalog columns,
overview summaries, scan actions, and repository registration/deregistration
flows. Plugin README explicitly excludes Fleet Analytics and Portal-owned
SCM publishing. Repository registration templates and catalog operations are
Portal-specific; APME remains responsible for its Gateway workflow and SCM
effects under ADR-056.

The plugin is not a copy of the full native SPA. It is a host adapter that
consumes the released @apme/ui-workflow package. Its APME package source and
tests are in workspaces/apme/plugins/; do not use dist or node_modules as
migration inputs.

### Confirmed divergence and constraints

- APME native frontend declares Node >=24.15, React ^19.3, and React Router
  ^8.3.1. The plugins monorepo declares Node 22 or 24 and uses Backstage
  1.49.4 with React 18. The APME plugin peers React 17/18 and Router 6.
- Native UI uses PatternFly 6.6.x. The APME plugin depends on PatternFly 6.3
  and Material UI 4. The host app declares @backstage/ui ^0.6.0; the APME
  plugin does not currently declare @backstage/ui. A target BUI version and
  compatibility proof are still required. The upstream [Backstage UI
  changelog](https://github.com/backstage/backstage/blob/master/packages/ui/CHANGELOG.md)
  documents routing changes that require matching BUIProvider and core app
  package versions, so test the full host tuple rather than just the UI package.
- APME's three distributable plugin packages and the private scaffolder package
  depend on @ansible/backstage-rhaap-common and/or Portal SDK workspaces.
  Generic-domain extraction and standalone dependency closure are unproven.
- The plan refers to an in-repository backend-only Helm profile at
  deploy/helm/apme/values-portal.yaml. Helm was removed; the chart is absent
  from selected main, and ADR-069 records its supersession in favor of the
  external APME Operator for Kubernetes/OpenShift. Treat the plan reference as
  outdated and do not reintroduce Helm as part of this UI work.
- The Gateway's graph, SBOM, fleet and dependency-health endpoints exist. Their
  exact consumers, completeness, and fitness for the proposed audiences still
  need review before they are committed as supported capabilities.

## P0.2: Draft capability matrix

This is a proposal for human review, not a product decision.

| Capability | Current evidence | Direction in PR 785 | Decision still needed |
| --- | --- | --- | --- |
| Shared check/remediation workflow | Native @apme/ui-workflow owns the reusable PatternFly workflow; Portal consumes a released tarball. Gateway owns operation state. | One APME-owned domain workflow, composed in both hosts. | Confirm exact shared journeys, error/reconnect behavior, and BUI exceptions. |
| Findings, proposals, approval, results | Present in native workflow and Gateway operation APIs; Portal has a Quality contribution. | Keep shared across both experiences. | Confirm supported result/export contract and whether every action is exposed in both hosts. |
| Repository selection and registration | Native Projects/Playground; Portal catalog integration and scaffolder registration. | Individual repo workflow local; catalog/fleet registration Portal-owned. | Define identity mapping, duplicate registration, removal, and history behavior. |
| Paste/upload assessment | Native Playground uploads files; no paste editor found. Gateway has WebSocket upload/session path. | Local experience gets paste/editor via the existing upload/session path. | Confirm supported file types/size, transient identity, and secure upload transport. |
| Project history, findings, dependency details | Native Activity, Sessions, project detail and dependency pages; Portal exposes activity/dependency contributions. | Shared domain data with host-specific presentation. | Confirm priority, history identity, deep links, and verified Graph/SBOM coverage. |
| Fleet views | Gateway has aggregate dashboard/ranking/dependency-health endpoints; plugin has fleet-quality contribution but README excludes Fleet Analytics. | Portal owns fleet metrics, trends, inventory and cross-repository views. | Ratify which aggregates exist today and which require new API work. |
| Health and configuration | Native Health/Settings; plugin contains settings contributions, including Abbenay. | Local service/AI/Galaxy diagnostics; Portal administrative controls. | Define user/administrator boundaries and which settings are safe in each host. |
| Identity, permissions, entitlement | Native and Portal use different host/auth paths; Portal operations include entitlement/resource checks in current SDK. | Explicit host capability and authorization boundaries; Gateway remains domain state owner. | Security review of REST, WebSockets, SSE, scheduled paths, and local permissions. |
| UI ownership and retirement | Native SPA is built into the UI image and deployment profiles. Portal owns plugin composition today. | APME owns UI packages; retire native SPA only after acceptance and migration window. | Ratify ownership, support window, rollback, and retirement criteria. |

## P0.3: Draft artifact subjects

No formal REQ/TASK/ADR IDs are assigned before G0. The PR plan already contains
the proposed work-package sequence P0.1-P5.4; this evidence record supplies the
current-main deltas those tasks need to carry.

### Requirement draft

APME owns the APME domain UI packages and provides two composed experiences:
a standard Backstage/RHDH Local developer experience that has no Portal SDK or
AAP prerequisite, and an optional Portal experience that uses an agreed,
immutable Portal SDK contract. Shared workflow behavior uses APME-owned
packages and the existing Gateway API/state model. Host-specific identity,
permissions, entitlements, routes, and fleet/personal surfaces are allocated
through a ratified capability matrix.

Proposed acceptance conditions:

- A clean APME checkout can build and install the standard plugin without
  resolving Portal SDK or AAP-specific packages.
- Portal consumes versioned APME artifacts and the approved SDK contract; it
  does not import APME source or maintain a copied production APME tree.
- Both experiences preserve Gateway-owned operation lifecycle, existing
  public API compatibility, SCM ownership, and approved authorization checks.
- The supported capabilities, runtime/version tuple, BUI exceptions, and
  unsupported/forbidden states are explicit and verified for both hosts.
- Native SPA deprecation and removal happen only after the agreed rollout,
  acceptance, support-window, and rollback criteria are met.

These are proposed acceptance conditions, not approved commitments. The
audiences, exact journeys, support budgets, and native UI support window remain
open at G0.

### Draft task adjustments

Use the plan's P0-P5 work packages, with these baseline-specific additions:

1. In P0.1, pin current plugins main at f3b1bf9 and the selected RHDH image
   digest; note that the plugin's v2026.9.3 workflow pin is behind APME
   v2026.10.4, and reconcile the release artifact before comparing behavior.
2. In P0.1/P0.5, correct the plan's outdated Helm reference: Helm was removed
   and Kubernetes/OpenShift deployment is handled by the external APME
   Operator (ADR-054/069). Do not reintroduce a chart as part of this work.
3. In P0.4, do not start the Portal slice until SDK maintainers approve the
   external-consumption contract or the product owner explicitly approves a
   time-bounded alternative.
4. In P1/P2, prove the React/Router/Node and BUI compatibility tuple before
   moving the React 19 native workflow into a React 18 Backstage host.
5. In P5, preserve the current UI container and deployment paths until
   migration acceptance; include Podman and bootc because they exist on main.

### ADR draft outline

Working title: APME UI Package Ownership and Host Composition.

- Context: APME currently has a standalone PatternFly SPA, a shared
  @apme/ui-workflow release package, and Portal plugin packages maintained in
  a separate monorepo. The current Portal packages depend on private SDK
  workspaces and pin an older workflow artifact.
- Option A: APME owns shared domain UI and standard Backstage packages; Portal
  consumes immutable APME and SDK artifacts through a documented adapter.
- Option B: Keep current split ownership and continue shipping the shared
  workflow as a release tarball while Portal keeps its host/domain packages.
- Option C: Make Portal the only supported UI host and retire the native/local
  experience.
- Proposed direction from the plan: Option A, subject to the SDK external
  distribution gate and human ratification of the dual-host capability matrix.
- Decisions to ratify: BUI adoption and supported version; package IDs and
  exports; external SDK distribution; supported host/version tuple; release
  cadence/provenance; native SPA deprecation window and rollback.

This outline is a review draft only. After G0, use the repository's ADR/REQ/TASK
workflows to create formal artifacts and check the latest indexes for IDs.

Relevant existing records include ADR-030, ADR-052, ADR-056, ADR-060,
ADR-065, ADR-066, and ADR-069. Preserve API/state/SCM boundaries unless the
approved architecture explicitly changes them.

## P0.4: SDK distribution gate

The Portal SDK packages in selected plugins main are version 0.1.0, licensed
Apache-2.0, and marked private: portal-extension-api,
portal-extension-common, portal-plugin-sdk, and portal-plugin-node. The APME
plugins consume these through workspace dependencies and dynamic-plugin export
scripts embed some Portal packages. Current code therefore does not demonstrate
the plan's required clean APME checkout and independently distributable
Portal adapter.

The plan's preferred path is immutable, versioned SDK artifacts with license
and provenance; an approved immutable tarball/registry is an interim option.
No SDK maintainer agreement has been obtained or requested externally. G0
cannot pass this gate until the maintainers approve a consumption contract or
the product owner approves a documented deviation. Do not vendor SDK code or
start Phase 1 on a floating source dependency.

## P0.5: Available baseline measurements

| Measure | Current snapshot | Limitation |
| --- | --- | --- |
| Native frontend source | 336 tracked frontend files; 26 frontend/workflow test files; 1 workspace package; frontend tree is 2.6 MB. | Static repo footprint, not a clean install/build measurement. |
| Portal APME source | 272 tracked APME plugin files; 94 test files; 4 package manifests; workspace tree is 2.0 MB. | Static repo footprint, not a packed dynamic-plugin size. |
| Lockfiles | Native npm lock is 150,651 bytes with 321 package entries; plugins monorepo Yarn lock is 1,791,918 bytes with 3,984 resolution entries. | Whole plugins monorepo lock, not APME-only dependency closure. |
| Dependency update process | APME Dependabot runs weekly for pip, npm, GitHub Actions, and pre-commit with a 30-day default cooldown. Plugins uses Renovate, including vulnerability/OSV alerts and one-day npm release age. | Configuration evidence only; issue/PR response time is not measured. |
| Dependency churn proxy | Since 2025-10-26, 117 APME commits touched the selected Python/frontend manifests or lockfiles; 85 plugins commits touched Yarn lock or APME plugin manifests. | Includes code changes that also touched these files; not a count of dependency-only PRs or maintainer hours. |
| Potential overlap | Native frontend has a 697-line Gateway API client; Portal has a separate 610-line APME API client. Shared workflow code is already distributed as an APME package. | Candidate duplication for a function-level comparison; line counts do not prove duplicated logic. |
| Declared support | Current native SPA and Backstage plugin are two existing UI paths. The plugins app declares Backstage 1.49.4, React 18, and one RHDH development baseline, next-2.1. | No ratified supported host/version matrix or production certification window found. |
| Native UI image | ghcr.io/ansible/apme-ui:2026.10.4 manifest digest sha256:5c704d9b27e857affdfc9bf0a0f432b2f5899579698d7511a808f12180fb9ed9; linux/amd64 child sha256:82dac8fe86054d31466e8f4137b5e0c9db22d941ef97f80e81b66be8bd48dcf4; 105,554,830 compressed layer bytes. | Registry image includes the base/runtime layers; this is not the Vite bundle size. |
| Candidate RHDH Local image | next-2.1 manifest digest above; linux/amd64 compressed layers total 292,624,688 bytes. | Community image and one platform's compressed layers; not runtime memory, startup time, or a supported production image. |
| Release workflow | APME has 6 tracked workflows, including UI artifact and multi-platform container publishing. Plugins monorepo has 5 tracked workflows. | Workflow count does not measure maintainer hours or release incidents. |
| Release contract | APME attaches the workflow tarball to product releases and builds/publishes multi-platform images. Plugins release documentation describes tagged source releases without binary assets; its APME packages have dynamic export scripts. | Exact artifact ownership, publication process, and compatibility tuple need agreement. |
| Install/start time and resources | Not measured. Local Node is v26.12.0; plugins monorepo declares Node 22 or 24. No Podman or Docker runtime is installed in this environment. | Need a supported Node/runtime and clean environment. Do not compare total operating cost from image transfer sizes alone. |
| Maintainer burden and support window | No historical hours, update effort, incident/support data, or deprecation window found in the checked-out repositories. | APME maintainers and product/UX owners must supply and ratify these values. |

## G0 review items

1. Ratify or revise the capability matrix, including audience, priorities,
   accessibility, identity/permissions, API dependencies, and retirement
   disposition.
2. Select the supported RHDH Local image/runtime and exact Backstage/React/BUI/
   Node compatibility tuple. The digest above is a candidate development input,
   not an approved support target.
3. Obtain Portal SDK maintainers' agreement on external artifact distribution
   and the package/version/provenance contract.
4. Measure clean install/build/start time, peak memory, disk/image sizes, and
   workflow performance for supported baselines. Agree acceptance budgets.
5. Define the native SPA support/deprecation window and rollback criteria.
6. Complete the transport/security inventory for REST, upload WebSockets, SSE,
   SCM effects, and any scheduled/background path.

Until these items are reviewed and G0 is explicitly ratified, Phase 0 remains
open and production implementation work should not start.
