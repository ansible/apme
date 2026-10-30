# ADR-073: APME UI Package Ownership and Host Composition

## Status

Partially Implemented

## Date

2026-10-30

## Context

PR #810 proposed consolidating APME domain UI in APME. The existing Portal plugin
already consumes `@apme/ui-workflow`, but its integration depends on private
Portal/AAP workspaces. In the implementation discussion the user approved APME
ownership, both hosts, all non-fleet features in both hosts, personal Local
deployments, Portal RBAC integration and versioned APME plugin releases.

The user authorized implementation and removal of the native SPA once the
replacement works, with no required migration or support period. This supersedes
the earlier proposal's mandatory native transition window. It does not claim
that the replacement is already complete or that SDK maintainers agreed to publish.

## Decision

**APME owns its client, shared workflow and standard Backstage plugin artifacts.
Local and Portal compose those packages using their respective host adapters.**

Keep Portal SDK consumption in the host integration boundary while extracting
domain packages. The standard install/build must never require private SDK or
AAP packages. Portal builds pinned APME release artifacts into its host; private
SDK publication is not a prerequisite for extracting the standard package.

Local is one deployment per person with full instance access. Portal owns
authentication, resource permissions and shared-setting RBAC. Host backend URLs
and authenticated fetch are injected explicitly, including SSE; never infer
authorization policy from a URL or fall back around a denied request.

Use BUI for new ordinary host layout and controls. Keep the existing specialist
PatternFly workflow during the incremental port, with its dependency explicit.
Use one current Gateway client and one workflow source. Preserve Gateway state,
`/api/v1`, structured results and Gateway-owned SCM writes.

This ADR amends ADR-030's long-term dual SPA/plugin support: RHDH Local becomes
the personal UI and the native SPA is removed after feature acceptance. No native
support window, route migration or migration path is required. Backend topology
and the CLI remain governed by their existing ADRs.

## Alternatives Considered

### Keep separate UI ownership

Preserves the existing integration with the least initial work, but keeps domain
changes split across repositories and does not meet the approved ownership goal.

### Make Portal the sole host

Avoids a Local composition but introduces Portal prerequisites for independent
developers and does not meet the approved two-host requirement.

## Consequences

### Positive

- Independent APME package development and one domain behavior source.
- Host identity, catalog and RBAC stay with the appropriate platform.
- Native UI can be removed as soon as the replacement is accepted.

### Negative

- Host compatibility and package releases require explicit verification.
- Full migration is incremental; the existing Portal integration remains active
  until released replacement exports cover its domain capabilities.

### Neutral

- Fleet can initially be Portal only. All other agreed features target both hosts.
- No numeric resource budget has been requested; measure and optimize resources.

## Implementation Notes

See [REQ-021](../specs/REQ-021-ui-host-consolidation/requirement.md). Begin with
the existing client, plugin transport and workflow. Prove a clean install and a
project workflow before porting the remaining pages or removing the native UI.
The temporary native workspace/lockfile remains until replacement acceptance.

## Related Decisions

- ADR-030: presentation choice amended as described above.
- ADR-052 and ADR-065: operation streaming and Gateway state ownership preserved.
- ADR-056 and ADR-060: SCM and public API boundaries preserved.
- ADR-066: extend product-release tarballs to the client and standard plugin.
- ADR-054 and ADR-069: external Operator for Kubernetes; no Helm chart restored.

## References

- [PR #810](https://github.com/ansible/apme/pull/810)
- [Baseline](../research/apme-rhdh-ui-phase-0-baseline.md)

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-10-30 | Codex | Recorded user-approved direction and implementation authorization |
