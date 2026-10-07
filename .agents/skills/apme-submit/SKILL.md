---
name: apme-submit
description: >
  Submit remediated patches as a branch/PR via apme submit (Gateway REST)
  or direct REST. Use this skill when pushing fixes, opening PRs, or
  submitting historical activity after remediate.
argument-hint: "[project-id]"
user-invocable: true
metadata:
  author: APME Team
  version: 1.0.0
---

# apme-submit — Branch + PR From Remediation

Thin CLI shim over `POST /api/v1/projects/{id}/operation/submit`
(Gateway owns SCM push, ADR-056). No local git writes beyond what
remediate already applied.

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

## CLI shim (preferred)

```bash
apme submit <project-id> --gateway-url http://localhost:8080
apme submit <project-id> --branch apme/fix-001 --no-pr
apme submit <project-id> --activity-id <scan-id>   # historical activity
```

## REST-only path (no CLI)

```bash
PID=<project-uuid>
curl -s -X POST $GW/api/v1/projects/$PID/operation/submit \
  -H 'Content-Type: application/json' \
  -d '{"branch_name":"apme/remediate-abc123","create_pr":true}' \
  | jq '.branch_name, .pr_url'
# Historical: {"activity_id":"<scan-id>","create_pr":true}
# .pr_url is null until a PR is recorded — with create_pr:false that is
# expected, not a failure. On 4xx/5xx, read .detail (a string, or a
# {code, message} dict on 409 — see the recipe below) instead of
# branching on null.
```

## Atomic operate + submit (one call)

```bash
curl -s -X POST $GW/api/v1/projects/$PID/operate \
  -H 'Content-Type: application/json' \
  -d '{"action":"remediate","options":{"auto_approve_tier1":true,"submit":{"create_pr":true}}}' \
  | jq '.status, .pr_url, .submit_error'
# .pr_url null + .submit_error set => the embedded submit failed (terminal
# status is kept; the snapshot already reflects completion). .pr_url null
# with no .submit_error => no PR requested/recorded. Always check
# .submit_error before treating a null .pr_url as failure.
```

409 means: no completed remediate, no patches, PR already exists, or no
SCM token. 409 `detail` is intentionally NOT normalized (ADR-060
additive-only) — accept both shapes: a string (`{"detail": "PR already
created for this activity: <url>"}`) or a dict (`{"detail": {"code":
"<code>", "message": "<human>"}}`). Closed code list: `idempotency_conflict`,
`working_set_in_progress`, `invalid_status`, `session_expired`. Recipe:
`code = d.get("code") if isinstance(d, dict) else None`; retry idempotency
only on `code == "idempotency_conflict"`; treat a string detail + 409 as a
non-retryable state conflict. 422 means provider undetectable.
Prerequisites: a completed remediate operation (or stored activity) with patches.

## See also

- Noun table: `docs/api/AGENT_CLIENT_GUIDE.md` — canonical tool per noun.
- Topology: `docs/guides/CLI.md` — CLI-local XOR Gateway-managed.
- Health aliases: `docs/api/health-contract.json` — Gateway/CLI field map.
