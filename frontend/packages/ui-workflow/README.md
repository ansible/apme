# `@apme/ui-workflow`

Shared APME **scan → pause → choose → remediate** UI (PatternFly).

Hosts:

- Native APME SPA (npm workspace)
- Portal Quality tab (`@ansible/plugin-backstage-apme`)

## Install (Portal / external)

Published as an **`npm pack` tarball** on GitHub Releases (ADR-066) — not npmjs yet.

```bash
# After bumping version in package.json and merging to main:
git tag ui-workflow-v0.1.0
git push upstream ui-workflow-v0.1.0
```

CI attaches `apme-ui-workflow-<version>.tgz` to the release. Consumers pin:

```json
"@apme/ui-workflow": "https://github.com/ansible/apme/releases/download/ui-workflow-v0.1.0/apme-ui-workflow-0.1.0.tgz"
```

## Package contents

- TypeScript source (`src/`) — hosts transpile (Vite / Backstage).
- Workflow CSS (`src/styles/workflow.css`) — imported from the package entry so inventory cards and review chrome render outside the native SPA theme.

## Native SPA

```json
"@apme/ui-workflow": "workspace:*"
```

No registry install required inside the APME frontend monorepo.
