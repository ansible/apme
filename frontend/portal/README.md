# APME Portal adapters

APME owns the source in this directory. It preserves the original Portal guest
integration while keeping the standalone Local packages free of Portal SDK/AAP
dependencies. Do not install these adapters in the personal Local image.

- `@apme/portal-plugin/alpha`: static NFS contributions, manifest, catalog actions,
  entity tabs/cards, fleet views, settings and scaffolder field/workflow.
- `@apme/portal-plugin/api`: API factory for the legacy development shell.
- `@apme/portal-plugin-backend/catalog`: entity/Gateway sync, authorized HTTP/SSE
  proxy, health, settings, repository lifecycle and Portal operations.
- `@apme/portal-plugin-backend/scaffolder`: repository registration action.

Both adapters use plugin owner `apme`. Capability and entry-point IDs keep their
`apme.` prefixes. The catalog module continues to mount `/api/catalog/apme/*`;
`apme` remains the settings resource identifier recognized by Portal. This keeps
existing clients, permissions and host OpenAPI documentation compatible.

The frontend declares config, entitlement and content-type restrictions. Entity
contributions check source annotations; deregistration retains its stricter
manual-registration condition. Catalog loaders also enforce the entitlement
before loading their content because not every 0.1.0 SDK descriptor has a native
entitlement field. Backend operations independently enforce permissions and
entitlements. A UI declaration never grants server access.

Portal runtime manifest discovery currently validates/logs but does not mount
surfaces. Install the `/alpha` static NFS feature. Settings remain contributed
through the existing catalog settings surface; navigation is host-configured.
The Local Settings tab and sidebar branding are independent of these adapters.

## Build and pack

Install the SDK checkout using its pinned Yarn lockfile, then run from APME:

```sh
export APME_PORTAL_CHECKOUT=/path/to/ansible-backstage-plugins
tox -e frontend-ci,portal-sdk-install,portal-build
tox -e portal-pack -- --version X.Y.Z --output dist/ui-packages
```

`portal-sdk-install` disables bulk SDK lifecycle scripts, then explicitly rebuilds
`better-sqlite3` for the catalog integration tests. This needs the native compiler
toolchain when a prebuilt binary is unavailable. Tests and builds prepare the same
SDK module link, so running tests first works on a fresh checkout.

The four standalone artifacts are built first. Two Portal-only tarballs are added
to the same release manifest/checksums. Adapter dependencies on APME artifacts
use immutable URLs for that release. SDK/AAP packages are host-provided peer
dependencies, not `workspace:^` references. No private SDK publication is needed
to build Local. `--local` replaces APME dependency URLs with local tarball paths
for installation verification only.

The build checks real SDK source/types, emits portable declarations, bundles
APME's internal common contracts into each adapter, and leaves host packages
external. The generated `dist/build.json` records the SDK checkout revision.
The development-only `node_modules` link resolves the installed SDK checkout;
it is never packaged. A fresh build rejects a missing SDK checkout.

Run authorization/lifecycle regressions with `tox -e portal-unit`, native UI and
frontend regressions with `tox -e portal-unit -- --frontend`, and clean tarball
installation/browser/backend/type checks with `tox -e portal-pack-smoke`.

Prepare Portal's dependency/import changes after choosing a release version:

```sh
tox -e portal-adoption -- --version X.Y.Z
git -C "$APME_PORTAL_CHECKOUT" apply --check /path/to/apme/dist/portal-adoption.patch
```

The patch uses pinned release URLs, keeps the native static composition, and
removes the obsolete APME dynamic overlay entries. It does not change the selected
Portal checkout. To check an unreleased build, first run `portal-pack-smoke`, then
generate a patch with `--version 0.0.0-portal --artifacts dist/portal-packages` and
apply it to an isolated Portal worktree. Update its Yarn lockfile and run its
composition checks before proposing the downstream change. Removing the original
workspace and its root workspace/TypeScript globs is a release-dependent follow-up.

## Provenance

The `src/common`, `src/frontend`, `src/catalog`, `src/scaffolder` and registration
template were transferred from `ansible/ansible-backstage-plugins`, branch
`feat/apme-shared-host-integration`, revision
`f8bb34f25e63182a26c3ed653024347e2a2e3370` (based on upstream
`6ab7fce5a37406c8958d1dc0291a4bc96eb1d3f5`). Apache-2.0 notices and the existing
tests are preserved. Internal common imports now use relative paths so release
artifacts require no downstream APME workspace package.

The adapter source is authoritative here after adoption. Portal should retain
only generic SDK/composition code and pinned APME package references. Release the
artifacts before switching production dependencies and delete the downstream
APME workspace only after packed-artifact and host workflow acceptance.
