# `@apme/ui-workflow`

Shared APME **scan → pause → choose → remediate** UI (PatternFly).

Hosts:

- Native APME SPA (npm workspace)
- Portal Quality tab (`@ansible/plugin-backstage-apme`)

## Install (Portal / external)

Published as an **`npm pack` tarball** on GitHub Releases (ADR-066) — not npmjs yet.

```bash
# After bumping version in package.json and merging to main:
git tag ui-workflow-v0.1.1
git push upstream ui-workflow-v0.1.1
```

CI runs `npm pack` (builds `dist/` via `prepack`) and attaches
`apme-ui-workflow-<version>.tgz` to the release. Consumers pin:

```json
"@apme/ui-workflow": "https://github.com/ansible/apme/releases/download/ui-workflow-v0.1.1/apme-ui-workflow-0.1.1.tgz"
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
