# REQ-021: APME UI Host Consolidation

## Status

In Progress

## Metadata

- **Phase**: PHASE-003 - Enterprise Dashboard
- **Created**: 2026-10-30
- **Decision**: [ADR-073](../../adrs/ADR-073-ui-host-composition.md)

## Purpose

APME owns and releases reusable UI packages for personal RHDH Local deployments
and Automation Portal. Build on the existing APME Backstage plugin and shared
workflow rather than create another scan/remediation implementation.

## User Stories

- As a developer, I can assess and remediate repositories in my own Local
  instance without AAP or Portal dependencies.
- As a Portal user, I use the same APME behavior within Portal's catalog and RBAC.
- As a maintainer, I release versioned APME artifacts that Portal builds into its
  host, and maintain one source for APME domain behavior.

## Acceptance Criteria

### AC1: Independent packages

- **GIVEN** a clean APME checkout
- **WHEN** its standard Backstage packages are installed, built and packed
- **THEN** no Portal SDK or AAP workspace is required, the Gateway client has no
  React/Backstage dependencies, and workflow controllers have a CSS-free export.

### AC2: Shared repository workflow

- **GIVEN** a configured Gateway connection
- **WHEN** a user selects or registers a repository and starts a check
- **THEN** existing Gateway state and workflow controllers drive findings,
  proposal review, remediation, cancellation, reconnect and structured results.

Direct repository entry must accept a repository URL and branch without requiring
`catalog-info.yaml`, an APME configuration file, or other changes inside the
repository. In Local, **Projects** must open the APME workbench; generic catalog
import/template pages must not be offered as its repository entry flow.
Preserve the native form's automatic name suggestion and optional
SCM provider/token fields. Portal may maintain host-side repository records to
enforce ownership and RBAC; users must not need to commit catalog metadata.

Local must expose Projects as a persistent sidebar destination, including when
another host page is open, with no clipped navigation labels. Local navigation
groups Projects, Rules, Health, Paste / upload and Settings beneath the APME name
and existing product icon; disable the unused host Catalog Graph and Search pages.
Keep APME's project-scoped graph available. A selected project
must have its own Settings tab for updating its name, URL, branch, SCM provider
and credential. Unchanged credentials must be preserved and never fetched back
into the form. Global APME settings belong under APME, separate from project
settings and the host's User settings. Portal keeps its host-owned navigation and
authorization; project editing must require an explicit host capability.

### AC3: Explicit host integration

- **GIVEN** a Local or Portal host
- **WHEN** it provides discovery and authenticated fetch
- **THEN** all HTTP/SSE requests use its configured backend path. Local has one
  owner with full instance access; Portal enforces its RBAC on its backend.
  Portal catalog registration and SDK composition remain host responsibilities.

Expose independently reusable project workflow, findings, activity, dependency
and graph entry points with declared capabilities. Hosts advertise supported
views and provide scope, entitlements and write permissions through APIs/props.
Do not infer the host from globals or URLs. One domain artifact serves personal
projects and shared enterprise fleets, with host-selected surrounding chrome.

### AC4: Feature coverage and replacement

- **GIVEN** the completed replacement
- **WHEN** both hosts pass their agreed feature acceptance
- **THEN** both hosts compose the same APME domain capabilities: Local targets
  personal projects, while Portal adds fleet scope, catalog registration and
  RBAC. Fleet requires host-provided entitlements. Remove the native UI at that point without a required
  support period, data migration, route migration or rollback window.

### AC5: Reproducible releases and efficiency

- **GIVEN** a product release
- **WHEN** UI artifacts are packed
- **THEN** inter-package dependencies reference immutable artifacts for that
  release, with build/type/unit/package checks through tox. Measure build size
  and startup/resource use as implementation proceeds; optimize avoidable work
  and raise material resource tradeoffs rather than inventing numeric budgets.

## Inputs / Outputs

Inputs: configured host backend URL, authenticated fetch, repository URL/branch,
Gateway public API and operation streams. Outputs: released client/workflow/plugin
packages and host-composed APME pages.

## Behavior

Select/register repository → check → inspect findings → review/remediate → obtain
structured results or a Gateway-created PR. Reopening a project can attach to its
existing operation. Permission and transport failures stay visible; never fall
back from a Portal authorization failure to a direct Gateway connection.

## Dependencies

- Architectural compatibility verified: backend service, validator, persistence,
  API versioning and SCM invariants remain intact. ADR-073 amends the presentation
  choice in ADR-030 and extends ADR-066 artifact packaging.
- Governing plugin-source revision for this requirement:
  `ansible/ansible-backstage-plugins` at
  `6ab7fce5a37406c8958d1dc0291a4bc96eb1d3f5`. The Phase 0 baseline records
  `f3b1bf911c99f7104249105a3055b38260d52d59` as its earlier historical snapshot;
  the requirement's revision governs implementation work.
- Existing Gateway client, `@apme/ui-workflow`, RHDH/Backstage and Portal RBAC.

## Non-Functional Requirements

- **Performance**: lazy page loading, bounded lists, stop subscriptions on unmount.
- **Security**: retain server authorization and SCM ownership; no raw file bundles.
- **Compatibility**: start from the latest shared declared host tuple, document
  evidence and distinguish package checks from actual host certification.

## Security Considerations

Host credentials must travel through authenticated fetch, including SSE. The
Local proxy is for a personal deployment; never install it as a Portal RBAC
bypass. Portal adapters continue enforcing resource and operation permissions.

## Open Questions

No unresolved product question blocks foundation work. Runtime compatibility,
Portal permissions and complete feature coverage require implementation evidence.

## References

- [Proposal](../../research/apme-rhdh-ui-consolidation-proposal.md)
- [Baseline](../../research/apme-rhdh-ui-phase-0-baseline.md)
- [PR #810](https://github.com/ansible/apme/pull/810)

## Change History

| Date | Author | Change |
|------|--------|--------|
| 2026-10-30 | APME maintainer direction, recorded by Codex | Product answers and authorization to implement |

## Staged delivery

Implementation is split from #832 under [TASK-022](tasks/TASK-022-staged-delivery.md). The complete target design below is delivered in order; the Local default switches only in the last PR. Intermediate merges retain the native deployment. Live two-host acceptance remains in #833.
