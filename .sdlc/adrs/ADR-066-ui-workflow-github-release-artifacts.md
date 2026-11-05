# ADR-066: Publish `@apme/ui-workflow` via GitHub Release Artifacts

## Status

Implemented

Extended by [ADR-073](ADR-073-ui-host-composition.md): the same product release
also carries the Gateway client, standard Backstage frontend/backend and
Portal integration adapters. The workflow
tarball asset name remains compatible; its manifest pins the client artifact
from that same release.

## Date

2026-07-23 (revised 2026-11-04)

## Context

`@apme/ui-workflow` is the shared PatternFly scan → pause → choose → remediate
UI used by the native APME SPA and the thin Portal Quality host
(`@ansible/plugin-backstage-apme`). Until a registry publish existed, Portal
**vendored** a copy under `plugins/apme-ui-workflow`, which drifted from `main`
(missing CSS, delayed fixes).

Constraints and drivers:

- Portal (Yarn Berry / Backstage) must install a consumable package without
  cloning the APME monorepo as a workspace.
- No npmjs org/process is required for the interim EAP path.
- ~~UI packages iterate faster than full APME product releases (`vX.Y.Z`).~~
  In practice, ui-workflow releases track product releases anyway.
- Native SPA must keep using the in-repo npm workspace (`workspace:*`).

## Decision

~~**We will publish `@apme/ui-workflow` as an `npm pack` `.tgz` attached to
dedicated GitHub Releases tagged `ui-workflow-vX.Y.Z`.**~~

**Revised:** The `@apme/client` and `@apme/ui-workflow` tarballs are attached as
assets to the main APME product release (`vX.Y.Z`). There is no separate
versioning or tag convention — package versions are derived from the release
tag at build time. Portal (and other hosts) pin the HTTPS release asset URLs on
the product release. npmjs and GitHub Packages remain deferred.

## Alternatives Considered

### Alternative 1: Public npmjs (`@apme/ui-workflow`)

**Description**: Publish to the public npm registry on each tag.

**Pros**:
- Semver ranges (`^0.1.0`) and Dependabot
- Familiar install UX

**Cons**:
- Org/scope policy and npm credentials
- More process than needed for EAP

**Why not chosen**: Heavier than necessary for the interim dual-shell path.

### Alternative 2: GitHub Packages (`npm.pkg.github.com`)

**Description**: Publish to GitHub Packages under the `@apme` scope.

**Pros**:
- Real registry semantics with semver
- Stays inside GitHub

**Cons**:
- Every consumer CI needs `NODE_AUTH_TOKEN` / `.npmrc` for `@apme`
- Still more moving parts than a public Release asset

**Why not chosen**: Auth friction for Portal CI and local `make react` loops.

### Alternative 3: Continue vendoring in ansible-backstage-plugins

**Description**: Keep copying `frontend/packages/ui-workflow` into the Portal
monorepo.

**Pros**:
- No publish pipeline

**Cons**:
- Drift (CSS, SSE, expand defaults) already observed
- Manual sync tax on every APME UI merge

**Why not chosen**: Vendoring failed as a durable dual-shell strategy.

### Alternative 4: Separate `ui-workflow-vX.Y.Z` tag convention (original ADR-066)

**Description**: Publish via dedicated GitHub Releases tagged
`ui-workflow-vX.Y.Z` with independent semver.

**Pros**:
- UI can ship between product releases

**Cons**:
- Extra tag lifecycle and version bookkeeping
- In practice, releases always tracked product cadence anyway
- Consumers must track two release streams

**Why not chosen**: Complexity not justified — product releases are frequent
enough and the UI package tracks them naturally.

## Consequences

### Positive

- Portal installs from a pinned Release download URL; no vendor tree.
- Single release cadence — no separate tag lifecycle to manage.
- Package version always matches the product version (no drift).
- Native SPA unchanged (`workspace:*`).

### Negative

- Portal version bumps require editing the dependency URL (no `^` range).
- Package ships compiled ESM `dist/` (+ CSS); consumers must bump the Release
  URL (no semver range) until a registry exists.
- A Release download URL is only byte-stable if the repository enables
  immutable releases / protected tags. Without that, maintainers can still
  replace an asset (delete + re-upload); consumers relying on lockfile
  checksums should treat unexpected hash changes as a supply-chain signal.
- UI-only fixes must wait for the next product release (no out-of-band ship).

### Neutral

- Migration to GitHub Packages or npmjs remains a follow-up without changing
  the package's public API.

## Implementation Notes

- Packages: `@apme/client` and `@apme/ui-workflow` are packed from the built
  workspace. The packer stages manifests and injects the release-tag version
  without modifying the source manifests.
- CI: `.github/workflows/ui-workflow-release.yml` triggers on
  stable `release: published` events with `vX.Y.Z` tags. It runs the local
  packed-consumer smoke check, packs release-mode artifacts, verifies their
  manifest dependency URLs and checksums, then uploads the tarballs,
  `manifest.json`, and `SHA256SUMS` to the existing release.
- Portal: depend on
  `https://github.com/ansible/apme/releases/download/vX.Y.Z/apme-ui-workflow-X.Y.Z.tgz`
  and `--embed-package @apme/ui-workflow` for dynamic plugin export.
- Release steps: create the main APME release as usual — the workflow
  automatically attaches the client/workflow tarballs, manifest, and checksums.
  No manual package version bump is needed.

## Retry behavior

Release retries verify already published asset checksums and upload only missing
files. An existing asset with different bytes fails the job and is never replaced
under the same immutable URL. The upload operation is exposed through
`tox -e frontend-release-upload`; a completed retry is a no-op.

The release manifest records the package version, source commit, dependency
mode, and artifact metadata. It omits the build runner's Node patch version so
retries for the same commit produce identical manifest bytes across supported
Node patch releases.

## Related Decisions

- ADR-030: Frontend Deployment Model (SPA + Portal plugin)
- ADR-065: SPA vs Gateway live-operation state ownership (Portal shares Gateway
  API; shared UI package was noted as future work — this ADR delivers the
  publish path)

## Revision History

| Date | Change |
|------|--------|
| 2026-07-23 | Initial — GitHub Release tarball publish for `@apme/ui-workflow` |
| 2026-07-31 | Consolidated into main APME release; removed separate `ui-workflow-v*` tag convention |
| 2026-09-22 | Agent: Status → Implemented — `ui-workflow-release.yml` and `tox -e ui-workflow-pack` publish `@apme/ui-workflow` with main release |
| 2026-11-02 | Codex: extend the package set under ADR-073 and verify immutable published bytes before completing partial-release retries |
| 2026-11-04 | Codex: make release manifests deterministic across supported Node patch releases |
