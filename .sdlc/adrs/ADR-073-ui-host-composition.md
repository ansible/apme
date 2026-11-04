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

The same domain artifact serves two audiences: a developer working on personal
projects and enterprise users working on a shared fleet. Local targets the
personal-project experience; Portal adds fleet scope, catalog registration and
RBAC. This describes audience and host scope, not a capability waiver or a smaller
product. Fleet is available when the host provides fleet entitlements.

Export independently composable project views at `./workflow`, `./findings`,
`./activity`, `./dependencies` and `./graph`, with capability descriptors at
`./capabilities`. Hosts explicitly advertise `projectViews` through the host API
or embedding props and independently evaluate write permissions. Host-specific
chrome, fleet/catalog scope and genuine experience variants are separate entry
points or props. Domain components must not detect a host through globals,
URL sniffing or `isPortal` branches. The full-page entry remains a convenience.

Use BUI for new ordinary host layout and controls. Keep the existing specialist
PatternFly workflow during the incremental port, with its dependency explicit.
Use one current Gateway client and one workflow source. Preserve Gateway state,
`/api/v1`, structured results and Gateway-owned SCM writes.

This ADR amends ADR-030's long-term dual SPA/plugin support: RHDH Local becomes
the personal UI and the native SPA is removed after feature acceptance. No native
support window, route migration or migration path is required. Backend topology
and the CLI remain governed by their existing ADRs.

### Local image build contract

The RHDH-based image below is the target Local build contract after the final
PR in this stack merges. Until then, Local retains the native deployment
described in [Staged delivery](#staged-delivery).

`tox -e build` builds the current checkout into an RHDH-based `apme-ui`
image, including frontend and upload backend dynamic plugins and personal-host
configuration. `tox -e up` starts this image in the existing local Podman pod on
port 8081. No published APME release or manual package installation is required
for Local. Portal continues to pin standard package artifacts from an APME
release and compose them with its catalog/RBAC during its own image build.
The development harness is not the Local runtime.

### Portal integration artifacts

The four standalone artifacts remain independent of private Portal packages.
The standard frontend gains a Backstage NFS `./alpha` export; the standard
backend's release name is `@apme/backstage-plugin-backend`.

Two additional, explicitly Portal-only adapters preserve the existing guest
integration: `@apme/portal-plugin` (manifest and static NFS surfaces) and
`@apme/portal-plugin-backend` (catalog module and scaffolder sub-entry). Their
shared integration contracts are internal source, bundled into the adapters;
they are not a new public common package. Portal SDK/AAP packages are host-provided
peers, never workspace dependencies in release artifacts. The adapters build in
an isolated tree against a pinned Portal SDK checkout; Local installation and
its image build do not load that tree. This avoids adding private dependencies
to the four standard packages while transferring APME source ownership.

Keep `/api/catalog/apme/*` and the `apme` settings resource identifier compatible.
The APME adapter owns route handlers and operation descriptors; Portal retains
generic transport, permission-resource infrastructure and compatibility OpenAPI
documentation. Static descriptors provide activation until the host implements
runtime manifest mounting. A release precedes the Portal dependency switch and
workspace deletion; those changes must not reference nonexistent release assets.

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

- Local targets personal projects; Portal adds fleet scope, catalog registration
  and RBAC. Capabilities are selected by the host; fleet requires host entitlements.
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
- ADR-074: optional authenticated host upload channel and backend package.
- ADR-054 and ADR-069: external Operator for Kubernetes; no Helm chart restored.

## References

- [PR #810](https://github.com/ansible/apme/pull/810)
- [Baseline](../research/apme-rhdh-ui-phase-0-baseline.md)

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-10-30 | Codex | Recorded user-approved direction and implementation authorization |
| 2026-11-02 | Codex | Accepted cidrblock's audience and composition guidance; added public view subpaths and host-declared project view capabilities; runtime acceptance remains in progress |
| 2026-11-02 | Codex | Added explicit host opt-in for project-scoped dependency acknowledgment; Local exposes the host's persisted theme preference; shared dependency views retain existing suppression semantics |
| 2026-11-02 | Codex | Audited ordinary controls and restored discoverable rule configuration and project summaries/deletion with an independent host permission; text trees share a lightweight presentation while D3 remains optional. Live acceptance and remaining feature gaps are tracked in #833. |
| 2026-11-03 | Codex | Corrected project attachment to explicit Start/Resume per ADR-065; dependency diagrams now reuse D3/Dagre by default, and Local styles load with the plugin entry to avoid lazy Settings CSS failures. |
| 2026-11-03 | Codex | TASK-021 moves dependency diagrams below inventory, uses Portal's existing Ansible Core minor choices in advanced scan options, and applies ADR-043 ordering to nonzero project severity counts. Live acceptance remains in #833. |
| 2026-11-04 | Codex | Clarified that the RHDH Local image contract takes effect after the final staged PR; intermediate merges retain the native deployment. |
| 2026-11-04 | Codex | Kept plugin capabilities explicit, retained rule data during refresh, preserved independent project deletion permission, and required a supported user-selected severity when Portal omits numeric severity. Live two-host acceptance remains in #833. |

## Staged delivery

Implementation is split from #832 under [TASK-022](../specs/REQ-021-ui-host-consolidation/tasks/TASK-022-staged-delivery.md). The complete target design below is delivered in order; the Local default switches only in the last PR. Intermediate merges retain the native deployment. Live two-host acceptance remains in #833.

Stage 2 adds host-composable project/data/settings views and the development
harness. Upload administration, Portal adapters and Local activation follow.
