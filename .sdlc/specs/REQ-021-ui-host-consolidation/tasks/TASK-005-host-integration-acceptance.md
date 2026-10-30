# TASK-005: Portal and RHDH Runtime Acceptance

## Parent Requirement

[REQ-021](../requirement.md), AC2, AC3 and AC5.

## Status

In Progress

## Description

Consume APME artifacts in both actual hosts, finish Portal composition/RBAC
adapters, and verify real Gateway/Engine workflows and resources.

## Prerequisites

TASK-003; consumes TASK-004 feature families incrementally.

## Implementation Notes

Portal builds released artifacts into its host and keeps SDK integration at the
host boundary. Preserve the existing catalog contributions and resource checks.
Verify identity mapping, settings administration and operation authorization on
every HTTP/SSE/upload/background path. Upload WebSockets require an authenticated
host upgrade path before Portal exposure; no bearer tokens in query strings.
Measure install/start time, memory and workflow latency in actual hosts and
optimize avoidable work. Document supported pinned runtime/image versions.

### Observed Portal contract gaps

Source baseline: `ansible-backstage-plugins` commit
`6ab7fce5a37406c8958d1dc0291a4bc96eb1d3f5`.

- Project/history routes omit Gateway totals/pagination. The shared adapter marks
  the returned bounded entries as available results; forward pagination/total
  before claiming complete history access.
- Rules are normalized into Portal labels: ERROR and CRITICAL collapse into
  `critical`, `aap` becomes `modernize`, and scope/registration time/numeric
  severity are lost. Preserve the full catalog data in the backend contract;
  the shared adapter currently keeps unavailable metadata unspecified. Existing
  rule-detail/stats routes are absent, so shared rules use the catalog directly.
- Galaxy create/update routes discard `validate_certs`. Forward and validate its
  boolean/null policy and extend the consumer contract before enabling
  `supportsGalaxyCertificateSettings`. The shared embedded form defaults it off.
- Evaluate settings view/manage permissions before passing shared settings
  props, and settings-manage before passing rule editing props. Defaults for
  embedded components are read-only/no Galaxy access. Verify that HTTP failures
  remain visible and never trigger a request to a different backend.
- Graph and SBOM have no catalog proxy endpoints at this baseline. Add authorized
  project-scoped forwarding for `/graph` JSON and `/sbom` CycloneDX bytes/headers,
  including denied/missing project behavior. Shared components use the host
  transport and currently show the missing-route error without direct fallback.

## Files to Create/Modify

APME host adapters, external Portal consumer manifests/composition, Local install
configuration and host acceptance checks. Avoid a copied production UI tree.

## Verification

Real two-host check → findings → remediation → approval → Gateway SCM results;
denied resource/mutation/settings actions; reconnect/expiry/unmount; supported
upload paths; clean install and measured runtime resources. Add reproducible tox
host test environments before claiming runtime acceptance.

## Acceptance Criteria Reference

AC2 authoritative workflow, AC3 host permissions, AC5 measured runtime efficiency.

## Current Implementation Evidence

2026-10-30: companion branch `feat/apme-shared-host-integration` implements
authorized lossless project/history/findings/dependency/graph/SBOM and rule routes,
project-bound activity details, settings-authorized AI discovery and Galaxy
certificate-policy forwarding. Existing Portal envelopes remain stable.
Portal full TypeScript checks, backend build, lint, OpenAPI drift/lint and
110 route tests pass. APME supplies the matching project adapter and optional
authenticated upload backend (ADR-074), plus focused channel and UI checks.

Still required: release artifact installation/pinning, Portal composition and
mounting the upload backend with tenant/user/scan authorization, actual RHDH and
Portal workflows, keyboard/theme and measured runtime resources. No running hosts,
container runtime or connected browser renderer exist in this workspace. The user
has been asked for runtime URLs/access; no answer has arrived. Native retirement
remains gated on that acceptance rather than inferred from unit/package checks.
