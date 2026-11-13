# Vendored Dependencies

## Molecule configuration schema and defaults

**Location:** `src/apme_engine/molecule/data/`

**Source:** `ansible/molecule` released snapshots:

- v24.9.0, commit `4e2592e5d77b181133243b8b685933f6daff3897` (legacy).
- v26.11.0, commit `ada645a132b9332c291696f553d93cce788b5410` (native/shared state).

Core and default/delegated driver schemas are copied unchanged. Defaults JSON comes from the literal mapping in
24.9.0 `Config._get_defaults` and 26.11.0 `constants.DEFAULT_CONFIG`, extracted
without importing or executing Molecule code.

**License:** MIT, with Cisco Systems and Red Hat copyright notices retained in
the adjacent `LICENSE`. `provenance.json` records the source commits and hashes.

**Why vendored:** Reproducible offline validation for Molecule 24.9.0+ covering
legacy and native syntax. The minimum baseline is documented with AAP 2.5;
newer upstream compatibility does not establish Red Hat support for every
release or driver. See `docs/guides/MOLECULE.md` for updates.

**Validation dependency review (ADR-019):** `jsonschema` implements JSON Schema
references, conditionals, and type validation, which are unsuitable for a small
in-house replacement. It is an actively maintained MIT-licensed Python package
with a small dependency set (attrs, referencing, specifications, rpds). No
existing APME dependency provides equivalent general schema validation. Type
coverage is supplied by `types-jsonschema` in development and mypy hooks. The
stdlib has no JSON Schema implementation.

Measured installed module footprint on Linux arm64: approximately 611 KiB for
jsonschema and 1.6 MiB for its dependency module directories (excluding package
metadata). Upstream maintenance and licensing are tracked at
[python-jsonschema/jsonschema](https://github.com/python-jsonschema/jsonschema).

## `@ansible/ansible-ui-framework`

**Location:** `frontend/vendor/ansible-ui-framework/`

**Source:** `ansible/ansible-ui` — `framework/` directory

**Why vendored:** The upstream repository is private. Vendoring avoids git
submodule complexity in CI (deploy keys, token forwarding) and gives us full
control over patches.

### Patches applied

1. **`PageApp/PageApp.tsx`** — Removed the `ChatbotSideBar` import and wrapper
   (APME does not use the `@ansible/chatbot` package).
2. **`index.ts`** — Trimmed to re-export only the modules APME actually
   consumes. This prevents Rollup from trying to resolve unused heavy
   dependencies like `monaco-editor`.
3. **`tsconfig.json`** — Replaced `"extends": "../tsconfig.json"` with a
   standalone, relaxed configuration so the vendored code builds inside the
   APME frontend tree.

### Exports used by APME

The trimmed `index.ts` exports only:

- `PageApp`, `PageFramework`, `PageLayout`, `PageHeader`, `PageBody`
- `PageMasthead`, `PageThemeSwitcher`, `PageNotificationsIcon`
- `PageNavigation`, `PageNavigationItem`, `PageNavSidebar`
- `PageDashboard`, `PageDashboardCard`, `PageDashboardCount`
- `PageNotificationsDrawer`, `usePageNotifications`
- `PageSettingsProvider`, `PageTabs`
- `PageNotFound`, `LoadingPage`, `Scrollable`, `useBreakpoint`
- `useFrameworkTranslations`, `useGetPageUrl`, `usePageNavigate`

### How to update

```bash
# From the repo root, with a fresh clone of aap-ui alongside:
rsync -av --delete \
  --exclude node_modules \
  --exclude dist \
  --exclude .git \
  ../aap-ui/framework/ frontend/vendor/ansible-ui-framework/

# Re-apply patches:
# 1. Remove ChatbotSideBar from PageApp/PageApp.tsx
# 2. Restore the trimmed index.ts (keep only APME-used exports)
# 3. Restore the standalone tsconfig.json

# Verify:
cd frontend && npm run build && npx vitest run
```

### Peer dependencies

The framework expects these as peers (installed in `frontend/package.json`):

- `@patternfly/react-core` 6.x
- `@patternfly/react-icons` 6.x
- `@patternfly/patternfly` 6.x
- `react` 18.x, `react-dom` 18.x, `react-router-dom` 6.x
- `swr`, `react-i18next`, `i18next`, `styled-components`
- `@react-hook/resize-observer`
