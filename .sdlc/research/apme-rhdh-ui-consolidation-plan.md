# Plan: One APME UI, Two Experiences

## Status and agreed direction

Planning only; no implementation, branch changes, builds, deployment changes, or native retirement authorized. User requests a human-readable plan with agent-actionable work packages. User selected moving all APME UI packages to the APME repository. Upstream generally serves individual developers; Automation Portal customers are fleet-focused. Evaluate each feature by audience rather than impose host parity. BUI wherever suitable; preserve an eventual independent upstream developer community and reduce steady-state operations/maintenance overhead.

## Long-term benefit

Upstream users receive a focused developer workbench, paste/upload assessment, individual-repository remediation and understandable results without AAP/Portal prerequisites. Upstream contributors work in the APME public repository with documented standalone development, fixtures and release artifacts. Customers get APME integrated into Portal-owned fleet experiences, identity, permissions, entitlements and contribution contracts instead of a disconnected console. Maintainers consolidate domain components, workflow behavior, dependency maintenance, tests and releases; retire the native SPA container/build/theme only after acceptance. Savings are hypotheses to measure: RHDH Local adds host/platform overhead compared with a small SPA. Preserve CLI/headless use; no claim of lighter local deployment or lower total cost without measurement.

## Evidence and branch discipline

- Existing workflow sources: `ansible/apme` — `frontend/packages/ui-workflow`; reusable controller/transport hooks plus PatternFly presentation.
- Native routes: `ansible/apme` — `frontend/src/shell/useApmeNavigation.tsx`. Native API: `frontend/src/services/api.ts`. Native Playground supports uploads; paste assessment is requested target functionality, not verified existing functionality.
- Existing plugin review source: `ansible/ansible-backstage-plugins`, branch `feat/apme-eap-next-ui-workflow`, commit `f6c5bfe5aa5e6ad1eb8f66eecb41a0a32f29da80`. Working checkout differs. Do not copy ignored dist/node_modules from plugin directories. No checkout in planning.
- Future integration source: `ansible/ansible-backstage-plugins` contains `docs/next/portal-sdk-architecture.md`, `docs/next/portal-sdk-plugin-author-guide.md`, and the APME plugin sources under `workspaces/apme/plugins/`. The branch `feat/portal-plugin-factory-sdk` could not be verified; use only pinned revisions with verified paths and reconcile branch divergence before treating any source as a migration input. Phase 0 pins exact immutable revisions.
- SDK: experimental host contract 0.1.0, baseline next-2.1; SDK private workspace packages; external consumption/public packages unsupported by current preview contract. APME relocation needs SDK-owner agreement. No promises of stable release support.
- Backend-only Helm profile already exists: `ansible/apme` — `deploy/helm/apme/values-portal.yaml` sets `ui.enabled false`.
- ADR-030 currently supports dual SPA/plugin paths and PatternFly; new ADR must supersede affected choices before implementation. ADR-066 governs current release tarball packaging. ADR-065/Gateway live state, ADR-052/SSE, ADR-056/SCM, ADR-060/API compatibility, ADR-069/deployment are governing references.

## Target architecture and scope

One product-owned domain UI with separate composition profiles and explicit capability allocation. Source of truth is ansible/apme. Proposed layout under frontend/: packages/apme-client, existing packages/ui-workflow evolved toward headless controllers, packages/apme-ui for BUI domain presentation; plugins/backstage-apme (standard host), plugins/backstage-apme-portal (optional NFS Portal contributor), plugins/backstage-apme-common and plugins/catalog-backend-module-apme (APME Node integration), plugins/backend-module-apme-portal (optional Portal policy/operation service consumer); dev app as a development/test harness, not a supported replacement SPA. These are proposed new paths, names must be settled once at architecture gate; preserve existing package IDs/public entry points unless versioned migration is justified. Backend decomposition follows policy boundaries, not a compulsory wholesale rewrite.

Core client has no React/Backstage/Portal imports. Workflow controllers have no PatternFly/BUI/Portal imports. BUI UI has no Portal imports or host URL assumptions. Standard Backstage integration provides discovery, authenticated fetch, routes, catalog context and standalone permissions. Optional Portal frontend/backend features alone import documented SDK contracts. Build/package dependency closure for standalone must not need SDK packages; separate workspace build/install filters or workspace root organization must be proven, not merely separate runtime imports. All APME-owned Node integration packages needed to independently build/distribute plugins should move with UI; retain generic Portal SDK/platform code in Portal repository. Portal host consumes APME release artifacts/config, not source imports, copied UI or a second authoritative APME tree.

Browser -> authenticated host backend -> Gateway /api/v1 -> Engine via FixSession; existing pod-internal gRPC invariants unchanged. Gateway owns sessions, state, proposals, persistence, clone/discovery/file pipeline and SCM commit/push/PR. Node backend adapts identity, resource policy and transport, not a parallel workflow engine/database. Keep current catalog backend discovery initially where viable; extraction to independent backend only if prototype shows required. External HTTP to Gateway is existing public edge, not permission to add backend-to-engine REST.

Capabilities are separately installed, enabled, applicable and authorized. Host profile is explicit composition, not pathname/branding detection or fail-open fallback. Portal knows manifests with actual plugin IDs, owner-prefixed capability/entrypoint/operation IDs, known experienceId and canonical content types; static ./alpha declarations use supported Blueprints, semantic attachments and isApplicable predicates. Do not clone a registry or import portal-extension-host as contributor production code. Portal owner supplies provider/composition. No generic universal launch-chain assumption.

## Proposed capability allocation (human ratification required)

- Shared: assessment/findings; deterministic remediation, AI escalation where configured; proposal review/approval/cancel/resume; structured result summaries, diff previews and PR URLs; repository/branch workflow; rule explanations; project-local dependency health, graph/SBOM where supported; own project activity/session details. Exports/downloads must remain within ADR-056: do not serve raw original or patched file bodies or bundles through the Gateway REST API; raw content stays inside the APME system boundary.
- RHDH Local primary: paste/upload assessment, individual repository setup/selection, focused developer overview, local service/AI/Galaxy diagnostics/config appropriate to a developer. No forced catalog registration for transient paste/upload sessions. Browser files are uploaded; no arbitrary browser access to host filesystem. Paste target is new editor -> virtual YAML file -> existing upload/session pipeline, not a new scan engine.
- Portal primary: fleet dashboard/trends/rankings, cross-repository dependency inventory, fleet activity, host catalog columns/cards/actions, shared administrative controls, centrally managed scan policy, entitlement-aware service integration.
- Host-replaced: navigation/theme/user notifications/identity; repository registration and removal use local APME UX versus Portal registered scaffolder workflows/domain operations; health integrates injected reporting, no assumption SDK installs a dashboard.
- Portal paste/upload, standalone fleet analytics, and automatic fleet scheduling are not required initial host surfaces. They can be consciously allocated later. No silent API/data deletion when omitting a surface; underlying aggregate APIs remain public contract. No feature silently retired.

## Delivery phases / agent work packages

Each work item must have task ID, owner role, exact paths/symbols, dependencies, accepted behavior, exclusions, test commands/evidence and handoff record. Agent may research alternatives; may not silently decide scope, policy, licensing or destructive migration. Stop/escalate architectural blockers. Do not assign numeric ADR/REQ IDs without checking latest indexes.

### Phase 0 — Baseline, scope and decisions

Owner APME lead + UX + Portal SDK maintainer; prerequisite to production work.

**P0.1** Pin both plugin branch inputs, APME revision, SDK revision, RHDH image digest, React/Backstage/NFS/BUI/Node versions and packaged artifacts. Compare feature divergence; inventory native routes, components, API/streams, tests, data, auth and deployment consumers. Record confirmed versus unimplemented features and known targeted-review risks, privately handle exploitable details as security policy requires.

**P0.2** Produce capability decision matrix with audience, shared component, host-specific surface, identity/permission/resource model, entitlement, API dependencies, priority, accessibility, automated evidence and retirement disposition. Review actual support for graph/SBOM/inventories rather than promise proposed ADR features.

**P0.3** Draft proposed requirements/tasks plus ADR covering UI consolidation, APME repository ownership, BUI, dual composition, package/release/version contract, supported hosts and SPA deprecation. Supersede affected ADR-030 choices, review ADR-066 and related deployment/test docs. Phase 0 produces drafts for human review; create the formal SDLC artifacts only after the architecture gate is approved, using the relevant skills.

**P0.4** Agree SDK external-consumption model with maintainers: preferred immutable versioned public package artifacts with licenses/provenance; interim immutable approved tarballs/registry acceptable if external use is supported explicitly. No copy/paste vendoring, floating source dependencies or mandatory separate Portal checkout for standalone developers. If external adapter build cannot be agreed, stop before Phase 1; resume only after agreement or an explicit human-approved deviation for temporary version-pinned thin adapter co-location. Never silently abandon chosen ownership.

**P0.5** Baseline current maintainer burden and developer install/start resources/time, image/build size, packages/security updates, CI/release steps, duplicate code and supported host/version count. Agree acceptance budgets and deprecated-UI support window before experiments.

**Exit G0:** feature matrix and architecture ratified, SDK dependency contract agreed, measured baseline, chosen version pairing. If the SDK contract is blocked, G0 is not exited and Phase 1 does not start unless a human approves a documented alternative. No retirement commitment.

### Phase 1 — Prove riskiest boundaries

Owner integration/QA; depends on G0; P1.1/P1.2 can run in parallel after minimal common contracts.

**P1.1** Standard RHDH Local packed-plugin vertical slice: start from fresh APME checkout, install signed/version-pinned dynamic frontend/backend with no Portal SDK/AAP; select/register one repository; assess, stream state, review deterministic proposal, approve and obtain structured results, permitted diff previews, or a PR URL through configured SCM. Demonstrate BUI layout and one representative form/table/dialog plus theme/keyboard behavior. Validate no unexpected SDK imports/dependency resolution.

**P1.2** Portal packed-plugin slice: externally built adapter contributes eligible tab, overview card and scan action via native Blueprints; injected registerApmeOperations equivalent checks subject resource and invokes same domain/Gateway path. Validate disabled, unsupported content, invalid manifest, denied permission, missing entitlement, absent SDK/version mismatch behavior and long-running acceptance versus completion. Test real auth path, not only mock AAP.

**P1.3** Prove authenticated project SSE, reconnect/session-expiry, mount/unmount/navigation continuity; prototype paste -> uploaded YAML session without forcing entity identity. Evaluate upload WebSocket auth; either retain with explicit secure upgrade/auth proxy or add new supported streaming transport under an approved contract. Do not invent bearer query-string auth or break existing API.

**P1.4** Compare install/start and workflow performance to baseline; record BUI gaps and host runtime compatibility. RHDH Local development/testing use is not production certification. Do not build a permanent parallel UI just for prototype.

**Exit G1:** reproducible artifacts and two-host demonstrations, policy denial evidence, compatibility/cost decision; failed SDK/runtime/local-cost gate triggers explicit design review before larger migration.

### Phase 2 — Establish APME-owned package foundation

Owner frontend/build/backend; depends on G1.

**P2.1** Import APME-owned source/tests/licenses/config from reconciled SDK/workflow branch versions, preserving provenance/history where feasible. Create approved frontend package workspace and development harness. Preserve public plugin IDs and API surface first; document allowed breaking transitions. Retain old consumers on pinned release until new artifacts validated; never dual-write authoritative source.

**P2.2** Separate client, workflow controllers, BUI domain components, standard Backstage integration and optional Portal frontend/backend adapters. Reuse useProjectWorkflow, useProjectOperationState, useProjectOperationActions, readSseStream, ApmeApiAdapter, createApmeUiWorkflowAdapter, registerApmeOperations. Existing index.ts imports workflow.css; provide truly headless entrypoint without CSS/UI side effects. Remove hard backstage-rhaap-common dependency from generic domain via APME-owned utilities or documented neutral contracts; do not copy Portal implementation internals.

**P2.3** Add dependency-boundary tests, separate standalone/Portal install-build profiles, typechecks, unit/component tests, package/export/artifact checks and dynamic builds under tox environments. Name new environments frontend-lint, frontend-unit, frontend-build, frontend-export, frontend-e2e; these are new proposed environments, not existing commands. Pin Node/package manager and use one intentional frontend workspace lockfile (legacy native transition exception documented); preserve Python tooling. SDK distribution remains separately versioned.

**P2.4** Release immutable APME packages/dynamic artifacts, checksums/provenance/SBOM under agreed license; define compatibility tuple and SDK upgrade policy, stable IDs, supported version window, changelog/deprecation. Extend existing workflow release rather than duplicate publishers. Public registry choice is human-approved; release tarballs acceptable if reproducible. Preserve ADR-066 compatibility until consumers transition.

**Exit G2:** external clean-clone builds, standalone dependency closure proven, Portal consumes same published domain UI, no copied production APME tree, reproducible export/installation.

### Phase 3 — Shared BUI capability migration

Owner frontend; depends on G2. Packages of work can proceed in parallel by component family with shared controller changes serialized.

**P3.1** Map each MUI/PatternFly use to BUI equivalent verified for chosen host runtime. Replace ordinary shell-free layout/forms/tables/tabs/buttons/dialogs/alerts; use BUI tokens and documented theme integration. Retain specialist code/diff/graph/chart editors only with explicit exception, accessible behavior, test and removal rationale. No all-page PatternFly wrapper/global theme manipulation as end state; remove useSyncPatternFlyTheme when last consumer gone. Do not substitute libraries randomly.

**P3.2** Port shared workflow: check options, live progress, assessment filtering, deterministic/AI decisions, diff/proposals, approve/reject/cancel, reconnect, structured results, diff-preview/PR-URL export and SCM publishing; preserve Gateway state and structured error/recovery semantics. Do not expose raw original or patched file bodies or bundles through Gateway REST. Keep AI optional and secret handling server-side.

**P3.3** Port project-level history/findings, dependency drill-down, rules/explanations, graph/SBOM if verified supported. Keep repository identity as normalized URL+branch or trusted entity mapping, not name-only fallback. Host route callbacks avoid fixed /self-service URLs. Clean disabled/empty/loading/failed/forbidden states.

**Exit G3:** representative golden workflow fixtures/test matrix both hosts, accessibility keyboard/focus/screen-reader checks and light/dark/responsive tests; documented BUI exceptions.

### Phase 4 — Audience-specific experiences

Owner developer experience and Portal integration; depends on G2, consumes G3 incrementally; P4.U and P4.P parallel.

**P4.U** RHDH Local developer workbench: concise home; paste/editor/upload assessment; file/repository check/remediation; local history and dependency details; local service health and scoped AI/Galaxy settings; structured results and diff-preview/PR-URL export. Any download must obey ADR-056: no raw original or patched file bodies or bundles through Gateway REST. Bootstrap RHDH Local and backend-only APME with pinned public artifacts and documented Podman/Docker support, volume/ports/cleanup/rebuild/troubleshooting. Mock fixture mode enables UI contributions without real AAP/AI/SCM; real-engine acceptance still required. Do not depend on planned ADR-049 daemon Gateway embedding. CLI remains independently usable.

**P4.P** Portal fleet integration: fleet metrics and aggregate activity/inventories where APIs exist; Quality/activity/dependency tabs; overview summaries/columns; scan actions; host registration scaffolder/workflow and removal semantics. Use static ./alpha and current manifests; host owns experiences and composition. Register operations with schema, permission, required entitlement and scope; authorize entity read/content/domain access independently. Use trusted organization resolver, shared repositories have no invented AAP owner, never first-membership selection. Forward progress through contributor routes with resource/entitlement enforcement and distinct read vs mutation permissions. Integrate documented host notifications/cache/health only as available; no unsupported health dashboard promise. Background scans/schedulers need independently defined policy or remain disabled; interactive dispatcher does not secure them automatically.

**P4.S** Backend transport/security audit common to both: enumerate all REST, uploads, WebSockets, SSE and scheduled paths; schema/URL/path allowlisting, TLS dispatcher consistency, protected settings, resource access and storage concurrency. Resolve or explicitly disposition earlier targeted-review findings and add regression tests. Local permissions profile cannot be selected by browser input; Portal never exposes a bypass standalone mutation route to same users. Gateway origin network/auth boundary explicitly documented; do not assume cluster networking alone authorizes users. No new tenancy, scheduler engine or durable audit/idempotency guarantees claimed.

**Exit G4:** signed capability matrix dispositions, all agreed local and fleet journeys pass with distinct host policies, no hidden native-only required journey.

### Phase 5 — Operational transition and retirement

Owner release/operations; depends on G4; rollout before removal.

**P5.1** Publish dual install guides, capability differences, compatibility matrix, migration and rollback runbooks, community contributor guide and release ownership. Portal consumes artifacts; no dependency on APME source tree or private host internals. Document RHDH Local as dev/evaluation rather than customer production replacement.

**P5.2** Opt-in preview with native UI still available; capture usability/support and baseline overhead measurements. Keep existing project/history IDs/database and public /api/v1 contract; no destructive migration as a side effect of UI move. Provide route/deep-link mapping appropriate to profiles; handle unknown/historical routes.

**P5.3** Update backend-only defaults/profiles only after accepted rollout; new ADR authorizes deprecation of standalone SPA model and placement of external UI host relative to APME pod. Keep Engine/validators/Proxy scaling invariant. Helm only for K8s/OpenShift; Podman for local non-K8s. Do not stuff RHDH into existing engine pod as incidental refactor. Audit chart services/routes/network policies, Podman build/pod/up, bootc Quadlets, UI container/nginx, CI/releases, documentation/screenshots. Remove obsolete SPA build/dependencies/container only after window and supported consumers migrate. Keep API packages/controllers/BUI UI and headless CLI, not deleting entire frontend directory.

**P5.4** Rehearse rollback using previous immutable plugin/host/backend artifacts and configuration and compatible database; native image fallback only during declared window. Validate in-flight session handling and deploy drain behavior; no promise UI rollback undoes SCM effects. Treat schema-changing work separately with explicit backup/downgrade policy.

**Exit G5:** maintainer and product approval, two-host real-workflow acceptance, migration/rollback evidence, security closure, local-cost/community independence checks, measurable steady-state maintenance improvement. Until then native stays supported or explicitly deprecated under approved policy.

## Verification reference

Existing APME gates: tox -e lint; tox -e unit; tox -e ui-workflow-pack while legacy package remains; tox -e openapi -- --check / regeneration when routes or schema change; tox -e helm for chart; tox -e ui for UI tests and tox -e e2e for the existing Podman scan only. The current tox -e e2e does not verify RHDH or Portal host integration; add dedicated frontend/plugin export and host e2e tox environments before claiming that coverage. Current Python lint/unit do not prove TypeScript compatibility. Before/after tests use SOP daemon isolation exception if relevant. No direct pytest/ruff/mypy/npm/Yarn quality commands in APME; tox wraps package manager tools. Portal repository integration checks remain its documented Yarn commands when its host/config files change, not a blanket imposition of APME tox on that repository.

Test layers: controller unit fixtures, BUI component accessibility/state tests, contract tests against Gateway OpenAPI, real backend policy denial tests, packed artifact dependency/exports checks, clean-clone standard RHDH Local no Portal/AAP, packed Portal with realistic auth/resource membership, full scan/remediation/approval/SCM test, upload/paste and WebSocket/SSE authentication, reconnect/expired/conflict/navigation cases, deployment migration and rollback. Version matrix is explicitly supported tuple, not unbounded latest versions.

## Critical source paths

- `ansible/apme`: `frontend/src/shell/useApmeNavigation.tsx` — native inventory/retirement audit.
- `ansible/apme`: `frontend/src/pages/PlaygroundPage.tsx` — upload behavior/reference, new paste adaptation.
- `ansible/apme`: `frontend/src/services/api.ts` — missing API surface inventory.
- `ansible/apme`: `frontend/packages/ui-workflow/src/index.ts` — entrypoint/style boundary.
- `ansible/apme`: `frontend/packages/ui-workflow/src/useProjectWorkflow.ts` and `frontend/packages/ui-workflow/src/hooks/useProjectOperationState.ts`, `useProjectOperationActions.ts` — controller reuse.
- `ansible/apme`: `src/apme_gateway/api/` and `operation_registry.py` — only scoped additive transport work; state ownership preserved.
- `ansible/apme`: `tox.ini`, `frontend/package.json` — package workspace and gate orchestration.
- `ansible/apme`: `.github/workflows/ui-workflow-release.yml`, `test.yml` — extend release and CI, verify actual current names before edit.
- `ansible/apme`: `deploy/helm/apme/values-portal.yaml`, `containers/ui/`, `containers/podman/`, `deploy/bootc/quadlet/` — staged deployment cleanup.
- `ansible/ansible-backstage-plugins` at `f6c5bfe5aa5e6ad1eb8f66eecb41a0a32f29da80`: `plugins/backstage-apme/`, `plugins/backstage-apme-common/`, `plugins/catalog-backend-module-apme/` — original branch source paths, not assumed available in current checkout.
- `ansible/ansible-backstage-plugins`: `workspaces/apme/plugins/backstage-apme/src/alpha.tsx` and `workspaces/apme/plugins/catalog-backend-module-apme/src/operations.ts` — current source paths verified in the repository; not confirmed at the earlier pinned commit. The cited `feat/portal-plugin-factory-sdk` branch remains unverified.
- Proposed new paths frontend/packages/apme-client/, frontend/packages/apme-ui/, frontend/plugins/backstage-apme[-portal]/ and supporting backend packages — create only after G0 naming/layout approval.

## Agent handoff contract

For each work package provide human goal, accepted scope, pinned inputs, exact files/symbols, dependencies and parallel-safe ownership, tests and recorded outputs/artifact versions, pass/fail gate, docs and rollback impact. Small reviewable PRs, one authoritative source; do not remove native or weaken auth/tests to get green. Human gates G0-G5 cannot be auto-approved by an agent. Architecture changes require ADR, unresolved scope/policy issues DR. No fixed effort estimates requested; estimate only after G1 prototype and signed feature matrix.