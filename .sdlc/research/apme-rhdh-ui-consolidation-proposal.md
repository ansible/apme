# Proposal: APME UI Consolidation and Host Composition

**Status:** Open for review. This is a proposed direction, not a ratified ADR,
requirement, support commitment, or authorization to begin implementation.

**Source plan:** [One APME UI, Two Experiences (PR 785)](https://github.com/ansible/apme/blob/4820cceb/.sdlc/research/apme-rhdh-ui-consolidation-plan.md)

**Evidence:** [Phase 0 baseline](apme-rhdh-ui-phase-0-baseline.md), collected
against APME `main` at `9c4372f` and `ansible-backstage-plugins` `main` at
`f3b1bf9` on 2026-10-26.

## Proposed decision for feedback

Make the APME repository the authoritative home for APME domain UI and shared
workflow packages. Compose those packages into two audience-specific hosts:

1. A standard RHDH Local developer experience that can be installed and built
   without Portal SDK or AAP-only dependencies.
2. An optional Portal experience that consumes the same APME domain UI through
   immutable, versioned artifacts and an explicitly supported Portal adapter
   contract.

Share domain workflow and presentation where that serves both audiences, but
allocate routes, registration, fleet views, identity, permissions, and
administration by host. Do not require feature parity between the two hosts.
Prefer Backstage UI (BUI) for standard host components after proving a
supported Backstage/React/Router/BUI/Node version tuple. Keep specialist
components only where BUI is unsuitable and document each exception.

Keep the Gateway authoritative for operation state and public API behavior.
Preserve the APME SCM boundary in ADR-056. Keep the existing native SPA and its
deployment paths available until both host experiences pass agreed acceptance
criteria and a support, deprecation, and rollback window has been approved.

This direction follows the plan's repository-ownership goal while making the
external SDK distribution requirement a hard gate. If Portal SDK maintainers
cannot support an immutable external-consumption contract, pause before
implementation and bring a documented alternative back for review.

## Why this direction

- There is already an APME-owned `@apme/ui-workflow` package used by both the
  native UI and the Portal plugin, so the two experiences already share a
  domain workflow boundary.
- Maintaining the APME domain UI in APME gives upstream developers a
  standalone path and avoids making the Portal plugin repository the source
  of APME product behavior.
- A single shared workflow source can reduce behavioral drift while host
  composition preserves Portal-only fleet and catalog experiences.
- This keeps the Gateway, operation lifecycle, REST compatibility, and SCM
  ownership boundaries intact while the presentation layer changes.

## Capability allocation for review

| Capability | Shared APME domain | RHDH Local developer host | Portal host |
| --- | --- | --- | --- |
| Assessment and remediation workflow | Findings, operation lifecycle, proposals, approval, progress, structured results | Paste/upload or individual repository workflows | Catalog-backed scan actions and eligible project workflows |
| Project data | Project history, findings, dependency details, rules; graph/SBOM only where verified | Focused individual project view and local history | Entity-linked tabs and summaries |
| Repository lifecycle | Domain operations and normalized project identity | Individual repository selection and local setup | Catalog registration/removal and Portal scaffolder workflow |
| Fleet data | Gateway contracts and aggregate data that are verified | No fleet administration by default | Fleet metrics, trends, rankings, and cross-repository inventory where supported |
| Settings and health | Shared domain APIs and workflow behavior | Developer-scoped service/AI/Galaxy diagnostics and settings | Central administration and entitlement-aware integration |
| Host integration | Host-neutral callbacks and domain packages | Backstage/RHDH navigation, identity, permissions, notifications | Portal identity, permissions, entitlements, catalog, and composition |

This matrix is intentionally a draft. In particular, current plugin
documentation excludes Fleet Analytics and Portal-owned SCM publishing; current
Gateway endpoints alone do not establish that every proposed aggregate or
journey is ready to promise.

## Package and release boundary

- APME owns the source and release contract for its domain client, workflow,
  and reusable UI packages.
- The standard RHDH package must have a clean dependency closure with no Portal
  SDK, `@backstage-rhaap-common`, or AAP-only requirement.
- Portal-specific adapters remain host-specific. Portal consumes pinned,
  immutable APME artifacts; it does not import APME source or carry a copied
  production APME UI tree.
- Portal SDK consumption must use an approved external artifact or registry
  contract with versioning, license, and provenance. Current SDK workspaces are
  marked private, so this agreement does not exist in the selected baseline.
- Keep artifact publication and compatibility policy in APME's release flow;
  choose a public registry or immutable release tarballs only after maintainers
  agree on ownership and support.

## Migration and safety boundaries

- Do not remove the native SPA, UI image, Podman setup, or bootc Quadlet during
  the initial package/host work.
- Do not change or delete `/api/v1` behavior, Gateway-owned operation state,
  stored project/session identity, or SCM effects as a side effect of moving
  the UI.
- Preserve ADR-056: APME owns commit/push effects. Do not expose raw original or
  patched file bodies or bundles through Gateway REST.
- Review REST, WebSocket upload/session flows, SSE, authorization, resource
  scope, and entitlement checks before accepting a host integration.
- Do not reintroduce Helm. Helm was removed; Kubernetes/OpenShift deployment
  uses the external APME Operator under ADR-054/069. Podman and bootc paths
  remain part of the current native UI inventory.
- Treat RHDH Local as a development/evaluation target until a separate product
  and support decision says otherwise.

## Current evidence and blockers

The selected plugin `main` is newer than the plan's cited plugin revision and
uses a workspace layout under `workspaces/apme/plugins/`. Its APME UI package
pins `@apme/ui-workflow` v2026.9.3, while APME `main` has v2026.10.4 with
substantial changes to operation/session streaming behavior. Reconcile those
artifacts before using the Portal integration as a behavioral baseline.

The current native frontend declares React 19 and React Router 8. The selected
Backstage host is on React 18 and the APME plugin peers React 17/18 and Router
6. The host declares `@backstage/ui` 0.6.0. No compatible target tuple has been
selected or demonstrated. The current plugins SDK packages are private
workspaces at version 0.1.0, so standalone builds and external Portal artifact
consumption are not yet proven.

The PR 785 plan also refers to an in-repository Helm profile that has since
been removed. ADR-069 records the removal; Kubernetes/OpenShift deployment is
handled by the external APME Operator. This UI proposal does not restore that
chart.

See the baseline for revision pins, package inventory, route/API/transport
inventory, static size measurements, and evidence limitations. Runtime,
performance, and clean installation measurements have not yet been collected.

## Alternatives considered

| Option | Benefits | Costs / risks | Assessment |
| --- | --- | --- | --- |
| Keep current split and continue publishing `@apme/ui-workflow` | Smallest immediate change; preserves current Portal integration | Two repositories remain involved in APME UI evolution; current SDK workspace and artifact version gaps remain | Viable fallback if the ownership or SDK gate cannot be resolved |
| Move all APME UI behavior into Portal plugins | One Portal-native implementation | Makes upstream developer experience depend on Portal; increases coupling and conflicts with maintaining a standalone upstream path | Not recommended |
| APME-owned domain packages composed by both hosts | One APME behavior source; supports upstream and Portal audiences with host-specific composition | Requires SDK artifact agreement, supported version tuple, package boundaries, and staged migration | Recommended for review, contingent on G0 gates |

## Questions for reviewers

Please comment on the proposal itself, including alternatives or changes:

1. Does the APME repository ownership and two-host model match the intended
   audiences? Which capabilities should move between the shared, Local, and
   Portal columns?
2. Is any current native or Portal journey missing, misclassified, or
   incorrectly described as available? Which graph/SBOM/fleet flows are
   supported today?
3. What exact Backstage/RHDH, React, Router, BUI, and Node tuple should be the
   initial supported baseline, and what evidence is required before selecting
   it?
4. Can the Portal SDK be published or otherwise consumed as immutable,
   versioned artifacts? Who owns that contract, and what license/provenance
   requirements apply?
5. Are the authorization boundaries, Portal entitlements, upload/WebSocket,
   SSE, SCM ownership, and Gateway API safeguards sufficient? What review is
   still needed?
6. What rollout, native SPA support/deprecation window, success measures, and
   rollback criteria should be agreed before retirement work starts?
7. Is there another material dependency or migration risk that should block
   G0?

## Gate before implementation

Phase 0 remains open until reviewers and the relevant APME, UX, Portal SDK, and
product owners ratify the capability allocation and package/host direction;
the SDK contract and supported runtime tuple are agreed; security and transport
boundaries are reviewed; and baseline acceptance budgets and the native UI
transition policy are defined. Only then should formal REQ/TASK/ADR artifacts
be created and Phase 1 compatibility prototypes begin.

No production implementation is included in this proposal PR.
