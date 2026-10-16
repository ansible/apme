---
name: apme-format
description: >
  Normalize Ansible YAML style via apme format (CLI --apply/--check) or
  Gateway read-only format preview. Use this skill when normalizing
  formatting, gating on format in CI, or previewing diffs before apply.
argument-hint: "[path]"
user-invocable: true
metadata:
  author: APME Team
  version: 1.0.0
---

# apme-format — Normalize YAML Style

No scan; YAML normalization only (indentation, key order, Jinja spacing).
Two topologies: CLI-local writes files, Gateway preview only reads.

## Discovery (Gateway-managed flows — run first, no hardcoded IDs)

```bash
export GW=${APME_GATEWAY_URL:-http://localhost:8080}
curl -s $GW/api/v1/health | jq .
curl -s "$GW/api/v1/projects?limit=5" | jq '.items[]? | {id, name}'
PID=$(curl -s "$GW/api/v1/projects?limit=5" | jq -r '.items[0].id')
```

## CLI-local (default, writes allowed)

```bash
apme format /path/to/project              # show diffs (no changes written)
apme format --apply /path/to/project      # apply changes in place
apme format --check /path/to/project      # CI mode: exit 1 if changes needed
apme format . --exclude "vendor/**"       # exclude paths (targets first)
```

## Gateway preview (read-only, commit-pinned)

```bash
curl -s -X POST $GW/api/v1/projects/$PID/format | jq '{commit, diffs: (.diffs | length)}'
```

`POST /projects/{id}/format` never writes formatted content back.
It returns the clone `commit` SHA with per-file diffs — apply only on a
checkout at that commit, otherwise the diffs may not apply cleanly:

```bash
COMMIT=$(curl -s -X POST $GW/api/v1/projects/$PID/format | jq -r .commit)
git -C /path/to/checkout checkout $COMMIT   # align before applying
apme format --apply /path/to/checkout
```

Caps: clone collection reuses `APME_SCAN_MAX_FILES` /
`APME_SCAN_MAX_BYTES` (413 when exceeded).

## See also

- Noun table: `docs/api/AGENT_CLIENT_GUIDE.md` — canonical tool per noun.
- Topology: `docs/guides/CLI.md` — CLI-local XOR Gateway-managed.
- Remediate (format + fixes): `/apme-remediate`.
