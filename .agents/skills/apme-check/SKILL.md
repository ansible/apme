---
name: apme-check
description: >
  Run APME check (scan for violations) via CLI --json or Gateway REST+SSE.
  Use this skill when assessing Ansible content, triaging violations, or
  feeding check JSON into remediate/submit flows.
argument-hint: "[path]"
user-invocable: true
metadata:
  author: APME Team
  version: 1.0.0
---

# apme-check — Assess Ansible Content

Closed-bundle invariant: built-in validators ship with the image. Custom
rules go through the Plugin service, never volume-mounted into built-ins.

## Discovery (Gateway-managed flows — run first, no hardcoded IDs)

```bash
export GW=${APME_GATEWAY_URL:-http://localhost:8080}
curl -s $GW/api/v1/health | jq .
curl -s "$GW/api/v1/projects?limit=5" | jq '.items[]? | {id, name}'
curl -s $GW/api/v1/rules/stats | jq '{total, override_count}'
# create once and reuse the response (project names are unique: a second
# identical POST would 409, so never re-POST to derive PID)
CREATED=$(curl -s -X POST $GW/api/v1/projects \
  -H 'Content-Type: application/json' \
  -d '{"name":"demo","repo_url":"https://example.com/org/demo.git"}')
echo "$CREATED" | jq '{id, name}'
PID=$(echo "$CREATED" | jq -r .id)
# history (recent scan activity for a project, or gateway-wide)
curl -s "$GW/api/v1/projects/$PID/activity?limit=5" | jq '.items[]? | {id, status}'
curl -s "$GW/api/v1/activity?limit=5" | jq '.items[]? | {id, status}'
```

## CLI-local (default, no Gateway)

```bash
apme check --json . > check.json
apme check --json . | jq '.count, .remediation_summary'
apme check --sarif . > results.sarif
apme check --diff .                       # what remediate would change
apme check . --report-to-gateway --gateway-url http://localhost:8080
# Mirrors check JSON into Gateway activity via POST /api/v1/scans/import.
# Link the import to a registered project (see Discovery create recipe):
apme check --json . --report-to-gateway --gateway-url $GW --project-id $PID | jq '.count'
# Topology: CLI-local XOR Gateway-managed (see docs/guides/CLI.md).
```

Exit codes: `0` clean, `1` violations, `2` error.

## Gateway-managed (registered project + SSE)

```bash
PID=<project-uuid>
curl -s -X POST $GW/api/v1/projects/$PID/operation \
  -H 'Content-Type: application/json' \
  -d '{"action":"check"}'
curl -s -N $GW/api/v1/projects/$PID/operation/events  # SSE: snapshot + deltas
curl -s $GW/api/v1/projects/$PID/operation | jq '.status, .result'
```

## Atomic (server-side gates, terminal snapshot)

```bash
curl -s -X POST $GW/api/v1/projects/$PID/operate \
  -H 'Content-Type: application/json' \
  -d '{"action":"check","options":{"auto_approve_tier1":true}}' | jq '.status'
# Submit embedded in the same call: check .pr_url/.submit_error too.
curl -s -X POST $GW/api/v1/projects/$PID/operate \
  -H 'Content-Type: application/json' \
  -d '{"action":"check","options":{"auto_approve_tier1":true,"submit":{"create_pr":true}}}' \
  | jq '.status, .pr_url, .submit_error'
# .pr_url null + .submit_error set => embedded submit failed. 409 detail
# is string-or-dict — see `/apme-submit` for the recipe.
```

> Auth boundary: the Gateway has no caller auth — same trusted-boundary
> rule as `/apme-remediate` atomic (operator role only, never exposed
> unauthenticated).

Stepped flow (`/operation` + `/approve` + SSE) is unchanged; `/operate`
is additive for agents that want one call + terminal snapshot.

## See also

- Noun table: `docs/api/AGENT_CLIENT_GUIDE.md` — canonical tool per noun.
- Topology: `docs/guides/CLI.md` — CLI-local XOR Gateway-managed.
- Health aliases: `docs/api/health-contract.json` — Gateway/CLI field map.
