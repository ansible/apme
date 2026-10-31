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

Still required: Portal release artifact pinning/composition and upload router
mounting with tenant/user/scan authorization; actual scan/remediation/SCM flows
in both hosts, keyboard/theme acceptance and measured runtime resources. Local
image build/startup is implemented below. Native source retirement remains gated
on workflow acceptance rather than inferred from package checks.

## Local build integration

User clarified the delivery contract: the existing `tox -e build` must build
the checkout plugins and an RHDH image containing them; `tox -e up` must start
that image as the local UI. Keep the existing localhost port 8081 and pod-local
Gateway address. Export frontend and backend dynamic plugins for a pinned RHDH
image, include authenticated guest sign-in for this personal-only deployment,
and wire REST/SSE proxy and upload discovery. Portal uses versioned standard
release artifacts in its own image build. Do not require a release for Local.

Implementation: `containers/rhdh/Dockerfile` builds the four standard packages,
exports the NFS Local frontend and a self-contained upload backend, installs them
and the host guest provider into pinned RHDH. The existing Podman build now uses
this Dockerfile and the pod publishes port 8081 on loopback. The native image
recipe/source remain for pending retirement; the Local build no longer uses it.

Build evidence: all dynamic plugins installed during an arm64 image build.
The built image started RHDH, initialized the `apme` backend and guest auth,
served the app and exposed the frontend federation remote. Readiness and guest
refresh returned HTTP 200. This workspace required Buildah with VFS/chroot through
tox because nested Podman's single-UID/fuse setup cannot perform a normal build.
Full pod startup, browser rendering and real Engine/SCM workflows are not claimed.

### Build dependency security follow-up (2026-10-31)

Scoped build-tool overrides patch the pinned Undici dependency and use upstream
patch releases of Backstage CLI auth/new with Zod 4. Workspace audit findings
fall from 41 (five high) to 32 (18 low, 14 moderate, no high/critical).
Production-only audit remains seven moderate findings through the host-compatible
Backstage/Router 6 dependency chain. Deprecation warnings remain visible.
`tox -e frontend-audit` makes both full and `-- --omit=dev` reports reproducible.
These are npm workspace findings, not an assessment of the prebuilt RHDH image.
The shared host baseline was not changed. Remaining host/dependency findings
still need review before claiming security acceptance.

### Direct repository entry

Preserve the current APME UI's URL/branch entry without any in-repository
configuration or catalog metadata file. The shared Local page already posts
straight to Gateway `/api/v1/projects`; restore native name suggestions and
optional explicit SCM provider selection. This creates a Gateway project and
selects its normal workflow, including for one-off use. It is not a new transient
scan protocol. Portal must expose equally simple entry through authorized
host-side repository records; its current project-operation authorizer requires
a readable catalog repository subject. Do not bypass that check or treat an
arbitrary project ID as owned by the current user.

Portal source inspection confirms an existing GitHub.com-only no-file entry:
`ApmeAddRepositoryHeaderAction` opens the `apme-register-git-repository` template,
which executes the authorized `ansible:register:git-repository` action and manual
provider. Ownership is derived from the initiating user, not a submitted owner.
Preserve that flow during shared package adoption and track extension to other
SCM providers; no user-authored repository descriptor is required today.

Local parity changes pass package builds, RHDH federation export, TypeScript,
format checks and repository lint. No additional test suites were run for this
form change; real-host interaction remains in this acceptance task.

### Local navigation correction

The user encountered RHDH's catalog importer asking for a descriptor PR. Renaming
`page:scaffolder` to Projects had mislabeled a template page and left the generic
catalog importer reachable. Local now disables `page:catalog`,
`page:catalog-import` and `page:scaffolder`; `page:apme` owns the Projects label
and root route. Projects → Repositories → Add repository opens the shared direct
URL form and posts to the Gateway via the authenticated proxy. No catalog import,
descriptor generation or registration PR is part of that flow. This correction
is scoped to the personal Local host.

Built the corrected arm64 Local image from the checkout through tox (Buildah
VFS/chroot fallback). In a headless Chromium preview of that clean image, guest
sign-in loaded Projects as the APME workbench, and clicking Add repository opened
the direct URL/name/provider/branch/token form with the no-repository-config
explanation. The generic Catalog, catalog import and scaffolder navigation were
absent. The preview has no Gateway, so repository listing showed an upstream
connection error; no repository was submitted and no scan/remediation is claimed.
Package/RHDH export, type/format checks and repository lint passed.

The initial host-export preview resolved the transitional native workspace's
React 19 runtime and failed to render in the React 18 host. The clean container
build excludes native node_modules and rendered correctly. Host-side export
isolation remains a development-path limitation until addressed or native
retirement; do not infer browser acceptance from export success alone.

### Local loopback origin loading

The user reported only built-in navigation and ongoing audit warnings. Browser
reproduction at `http://localhost:8081` showed CSP blocking APME federation
scripts advertised at the configured `http://127.0.0.1:8081` backend URL. Local
now explicitly permits these two loopback origins for federation assets,
styles/fonts/images, API connections and credentialed CORS. It does not add
arbitrary origins. Export packaging also fails if the frontend feature metadata,
module federation manifest or remote entry is missing.

Fresh workspace audit reports 88 package entries (7 low, 81 moderate, no high or
critical); production-only audit remains seven moderate entries. Reports now flag
glob/minimatch build-tool chains as well as the previously documented remaining
issues. This supersedes the earlier 32-entry snapshot rather than claiming all
warnings were fixed. The remaining audit proposals do not provide compatible
non-breaking remediation for those dependency chains. Keep warnings visible and
track dependency/host upgrades; do not force legacy glob/minimatch APIs to their
new major versions or Router 6 to Router 7 simply to lower the count. These are
npm workspace reports, not a scan of the prebuilt RHDH/OS image.

Built the updated Local image through tox using the VFS/chroot fallback, then
opened that image in Chromium at both `http://localhost:8081/` and
`http://127.0.0.1:8081/`. Guest sign-in, Projects navigation and the direct Add
repository form rendered at both origins. The remotes response includes the
APME frontend and reflects localhost in its CORS header. The preview has no
Gateway, so no repository registration or Engine/SCM workflow was exercised.
Package type/format checks, federation export guards and repository lint pass.
