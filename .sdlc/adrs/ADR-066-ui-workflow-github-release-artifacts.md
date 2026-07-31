# ADR-066: Publish `@apme/ui-workflow` via GitHub Release Artifacts

## Status

Implemented

## Date

2026-07-23

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
- UI packages iterate faster than full APME product releases (`vX.Y.Z`).
- Native SPA must keep using the in-repo npm workspace (`workspace:*`).

## Decision

**We will publish `@apme/ui-workflow` as an `npm pack` `.tgz` attached to
dedicated GitHub Releases tagged `ui-workflow-vX.Y.Z`.** Portal (and other
hosts) pin the HTTPS release asset URL. npmjs and GitHub Packages are deferred.

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

## Consequences

### Positive

- Portal installs from a pinned Release download URL; no vendor tree.
- UI can ship between product releases via `ui-workflow-v*` tags.
- Native SPA unchanged (`workspace:*`).

### Negative

- Portal version bumps require editing the dependency URL (no `^` range).
- Package ships compiled ESM `dist/` (+ CSS); consumers must bump the Release
  URL (no semver range) until a registry exists.
- A Release download URL is only byte-stable if the repository enables
  immutable releases / protected tags. Without that, maintainers can still
  replace an asset (delete + re-upload); consumers relying on lockfile
  checksums should treat unexpected hash changes as a supply-chain signal.

### Neutral

- Product `vX.Y.Z` releases may optionally attach the same asset later.
- Migration to GitHub Packages or npmjs remains a follow-up without changing
  the package’s public API.

## Implementation Notes

- Package: not `private`; `prepack` runs `tsc` + copies CSS into `dist/`;
  `files` includes `dist/` and README.
- CI: `.github/workflows/ui-workflow-release.yml` on tag `ui-workflow-v*`:
  assert version matches tag → `npm pack` → `gh release create` with `.tgz`.
- Portal: depend on
  `https://github.com/ansible/apme/releases/download/ui-workflow-vX.Y.Z/apme-ui-workflow-X.Y.Z.tgz`
  and `--embed-package @apme/ui-workflow` for dynamic plugin export.
- Release steps: bump `frontend/packages/ui-workflow/package.json` version,
  merge, tag `ui-workflow-vX.Y.Z`, push tag.

## Related Decisions

- ADR-030: Frontend Deployment Model (SPA + Portal plugin)
- ADR-065: SPA vs Gateway live-operation state ownership (Portal shares Gateway
  API; shared UI package was noted as future work — this ADR delivers the
  publish path)

## Revision History

| Date | Change |
|------|--------|
| 2026-07-23 | Initial — GitHub Release tarball publish for `@apme/ui-workflow` |
