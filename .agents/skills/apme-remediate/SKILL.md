---
name: apme-remediate
description: >
  Run APME remediate (Tier 1 deterministic + optional Tier 2 AI) via CLI
  or Gateway REST+SSE with server-side approval gates. Use this skill when
  fixing violations, reviewing proposals, or driving atomic operate flows.
argument-hint: "[path]"
user-invocable: true
metadata:
  author: APME Team
  version: 1.0.0
---

# apme-remediate — Fix Violations

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

## CLI-local

```bash
apme remediate --json . > remediate.json
apme remediate --ai --auto-approve --json .   # CI mode, no prompts
apme remediate --interactive .                # Gate 1 review (Tier 1)
apme remediate --interactive --ai .           # two-gate flow (Tier 1 then AI)
```

`--json` output includes `violations`, `diffs`, `files_updated`,
`remediation_summary`, and `resolution_summary`.

## Gateway stepped flow (human review)

```bash
PID=<project-uuid>
curl -s -X POST $GW/api/v1/projects/$PID/operation \
  -H 'Content-Type: application/json' \
  -d '{"action":"remediate","options":{"enable_ai":true}}'
curl -s -N $GW/api/v1/projects/$PID/operation/events   # proposals, findings
curl -s -X POST $GW/api/v1/projects/$PID/operation/approve \
  -H 'Content-Type: application/json' \
  -d '{"approved_ids":["<id>",...]}'
curl -s -X POST $GW/api/v1/projects/$PID/operation/escalate-ai \
  -H 'Content-Type: application/json' \
  -d '{"targets":[{"path":"<node-id>","rule_ids":[]}]}'
```

## Gateway atomic (agents, no interactivity)

Atomic resolves all Tier 1/Tier 2 gates server-side from tier-wide
booleans with no mid-flow veto. Use the stepped flow above (per-proposal
approve + SSE) when selective per-proposal veto is needed; reserve atomic
`auto_approve_*` for a CI operator within the trusted boundary.

```bash
curl -s -X POST $GW/api/v1/projects/$PID/operate \
  -H 'Content-Type: application/json' \
  -d '{"action":"remediate","options":{"auto_approve_tier1":true,"auto_approve_ai":true,"enable_ai":true}}' \
  | jq '.status, .result.remediated_count'
# Submit embedded in the same call: check .pr_url/.submit_error too.
curl -s -X POST $GW/api/v1/projects/$PID/operate \
  -H 'Content-Type: application/json' \
  -d '{"action":"remediate","options":{"auto_approve_tier1":true,"submit":{"create_pr":true}}}' \
  | jq '.status, .pr_url, .submit_error'
# .pr_url null + .submit_error set => embedded submit failed (terminal
# status kept). 409 detail is string-or-dict — see `/apme-submit` for the
# recipe (`code == "idempotency_conflict"` retries, else state conflict).
```

> **Authorization boundary:** the Gateway has no caller authentication
> (real caller auth is tracked in GitHub #664) — `POST /operate` with
> `auto_approve_*` applies fixes server-side and can open PRs as whoever
> can reach the Gateway. Call it only within a trusted boundary
> (localhost, or a network where every caller holds the operator role);
> never expose the Gateway unauthenticated to shared/untrusted networks.
> CLI-local `--auto-approve` (CI mode above) stays on the local checkout
> and carries no Gateway auth implications.

Atomic drives begin-remediate, AI escalation, and approvals server-side
and returns the terminal snapshot. Live-policy context (resolved
severity/enabled, suppressions, ansible-core/collection pins) is rendered
into AI prompts; missing policy fails closed.

## See also

- Noun table: `docs/api/AGENT_CLIENT_GUIDE.md` — canonical tool per noun.
- Topology: `docs/guides/CLI.md` — CLI-local XOR Gateway-managed.
- Health aliases: `docs/api/health-contract.json` — Gateway/CLI field map.
