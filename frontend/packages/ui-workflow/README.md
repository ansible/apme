# `@apme/ui-workflow`

Shared APME **scan → pause → choose → remediate** UI (PatternFly).

Hosts:

- Native APME SPA (npm workspace)
- Portal Quality tab (`@ansible/plugin-backstage-apme`)

## Install (Portal / external)

Published as an **`npm pack` tarball** attached to the main APME GitHub Release
(ADR-066) — not npmjs yet.

The tarball is automatically built and attached when a `vX.Y.Z` release is
published. No separate tagging or version bump is needed — the package version
is derived from the release tag at build time.

Consumers pin the release download URL:

```json
"@apme/ui-workflow": "https://github.com/ansible/apme/releases/download/vX.Y.Z/apme-ui-workflow-X.Y.Z.tgz"
```

## Package contents

- Compiled ESM + `.d.ts` (`dist/`) for Portal / `export-dynamic` consumers.
- Workflow CSS (`dist/styles/workflow.css`) — imported from the package entry.
- Native SPA continues to resolve the workspace package via Vite path alias to
  `src/` (no pack required).

## Native SPA

```json
"@apme/ui-workflow": "workspace:*"
```

No registry install required inside the APME frontend monorepo.
