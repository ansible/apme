# Agent Client Guide (additive)

Canonical tool per noun. Prefer this mapping; do not mix paths for the
same noun in one workflow.

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

| Noun | Canonical tool | Path | Notes |
|------|---------------|------|-------|
| check (assess) | CLI `apme check --json` (local) or `POST /operate` (Gateway) | Engine `FixSession` gRPC under the hood (ADR-039) | CLI-local XOR Gateway-managed (see `docs/guides/CLI.md`) |
| remediate (fix) | CLI `apme remediate --json` or `POST /operate` atomic | Engine `FixSession` gRPC; Tier 1 deterministic, Tier 2 AI via Abbenay | Stepped `/operation` + gates unchanged; `/operate` is additive |
| format | Skill `/apme-format`, CLI `apme format` or `POST /projects/{id}/format` | Engine `Format`/`FormatStream` gRPC utility RPCs | No scan; YAML normalization only; Gateway preview returns clone `commit` — apply only on a checkout at that commit |
| health | CLI `apme health-check --json` or `GET /api/v1/health` | Engine `Health` gRPC aggregated by Gateway | Field aliases in `docs/api/health-contract.json` |
| discover (projects) | `GET /api/v1/projects` | Paginated project summaries | Pick `id` from `.items`, never hardcode; `?limit=` pages |
| rules (stats) | `GET /api/v1/rules/stats` | Rule catalog totals by category/source | Read-only catalog overview before check/remediate |
| rules (detail) | `GET /api/v1/rules/{rule_id}` | Agent-readable resolved policy: `resolved_severity`, `resolved_severity_label`, `resolved_enabled` | Read-only; `PUT /rules/{rule_id}/config` is admin-only, agents must not call it |
| sbom | CLI `apme sbom` (Gateway) or `apme sbom --path` (local) | `GET /projects/{id}/sbom`; local mode needs no project | CycloneDX 1.5; local `--path` is manifest-derived only (requirements files + ansible-core pin; `galaxy.yml`/`.apme` not consulted, not the installed venv) — verify via top-level `apme:sbom-source` property |
| suppress (local) | CLI `apme suppress` | Fingerprint hash shared with Gateway `_violation_fingerprint` (ADR-055) | `fingerprint` subcommand is dry-run; use for local checkouts |
| suppress (server) | `POST /api/v1/suppressions` / `GET /api/v1/suppressions` | Server-side suppression for registered projects; send `original_yaml` and the server computes the canonical fingerprint (or send `fingerprint_hash` directly) | Rule: CLI suppress file for local checkouts, REST suppressions for registered projects |
| submit (branch/PR) | CLI `apme submit` or `POST /api/v1/projects/{id}/operation/submit` | Gateway owns SCM push (ADR-056) | Needs completed remediate + patches; `.pr_url` null until a PR exists — check `.submit_error` (atomic) / `.detail` (REST) first |
| sessions/venvs | `GET /api/v1/sessions/venvs/{id}` (read-only) | Persisted session view; Engine owns venv writes (ADR-022) | hash/requirements/age only |
| import (mirror) | CLI `--report-to-gateway` or `POST /api/v1/scans/import` | Stores external check JSON as activity | Opt-in bridge only; pass `project_id` to link to a registered project (404 when unknown), else per-scan session under `project_path` |

## Rules

- **gRPC everywhere between backend services** (ADR-001). Agents use CLI
  (gRPC client) or Gateway REST/SSE. Never call validators directly.
- **Built-in validator bundles are closed** (ADR-042). No volume-mounted
  rules, no custom rule dirs, no external Rego/Python injected into
  built-ins. Custom rules go through the Plugin service (`EXT-` prefix).
- **REST is additive-only under `/api/v1`** (ADR-060). New endpoints and
  optional fields only; no renames/removals without `/api/v2`.
- **Inference deferred** (ADR-046): no `Engine.Inference` RPC yet. Agents
  must not assume an inference endpoint exists.
- **MCP cross-file deferred**: `NEEDS_CROSS_FILE` is a classification
  only; treat as manual review.

## Submit result handling

- `POST /api/v1/projects/{id}/operation/submit` returns
  `{branch_name, commit_sha, pr_url, provider}`. `pr_url` is `null`
  until a PR is recorded — with `create_pr:false` that is expected.
  On failure `.detail` is a string, except 409s which may carry a
  `{code, message}` dict (see "409 shapes" below); branch on the status
  code / `.detail`, not on null.
- Atomic `POST /api/v1/projects/{id}/operate` with
  `options.submit` keeps the terminal snapshot and surfaces an
  embedded submit failure as additive `.submit_error` (terminal status
  unchanged). Recipe: `jq '.status, .pr_url, .submit_error'` —
  `.pr_url` null + `.submit_error` set means the submit failed;
  `.pr_url` null with no `.submit_error` means no PR was
  requested/recorded. Always send `-H 'Content-Type: application/json'`
  with `-d` payloads. See `/apme-submit` skill for copy-paste recipes.
- Atomic idempotency replay: retrying `POST /operate` with the same
  `Idempotency-Key` (or `options.submit.submit_token`) replays the
  stored submit result instead of pushing twice. Replay keys on
  `(project_id, token)` alone once a completed submit result is stored
  — a fresh `scan_id` per atomic attempt does not force a 409 when
  branch/activity bindings match.

## 409 shapes (both exist — handle both, do not string-parse codes)

409 `detail` is intentionally NOT normalized (ADR-060 additive-only).
Clients must accept **both** shapes:

- String: `{"detail": "PR already created for this activity: <url>"}`,
  `{"detail": "Project already has an active operation <id>"}`,
  `{"detail": "Cannot submit: operation is '<status>', not 'completed'"}`.
- Dict: `{"detail": {"code": "<code>", "message": "<human>"}}` with
  `code` in `idempotency_conflict`, `working_set_in_progress`,
  `invalid_status`, `session_expired`.

Recipe: `code = d.get("code") if isinstance(d, dict) else None`;
branch idempotency retries on `code == "idempotency_conflict"`,
treat a string detail + 409 as a non-retryable state conflict
(already-published / active-operation) unless the message matches a
known replayable case. Never assume `detail` is a string.

## Upload / scan caps (three dialects)

Three different layers enforce similarly-named caps with different env
vars — do not mix them:

| Layer | Env vars (defaults) | Enforced where |
|-------|--------------------|----------------|
| WS playground upload (`WS /ws/session`) | `APME_UPLOAD_MAX_FILE_BYTES` (10 MiB/file), `APME_UPLOAD_MAX_TOTAL_BYTES` (256 MiB), `APME_UPLOAD_MAX_FILES` (2000) | `src/apme_gateway/session_client.py` — per-message / aggregate ingress guards |
| Clone / scan project ops (Gateway `run_project_operation`, `POST /projects/{id}/format`) | `APME_SCAN_MAX_FILES` (2000), `APME_SCAN_MAX_BYTES` (256 MiB) | `src/apme_gateway/scan/driver.py` (+ format endpoint) — fail-fast before streaming to Engine |
| Engine PE-35 session ingress | `APME_SESSION_MAX_UPLOAD_BYTES` (256 MiB), `APME_SESSION_MAX_UPLOAD_FILES` (2000) | `src/apme_engine/daemon/engine_upload.py` — enforced before mutating session state |

All three parse via the shared `get_env_int` discipline (invalid values
fall back to defaults with a warning, never crash or invert a guard).

## Format is a read-only preview

`POST /api/v1/projects/{id}/format` is an intentional read-only preview:
the Gateway clones the repo, runs Engine `Format`, and returns per-file
diffs — it never writes formatted content back. To apply, use the CLI or
remediate on a checkout:

```bash
# preview (Gateway-managed project)
curl -X POST $GW/api/v1/projects/<id>/format | jq '{commit, count: (.diffs | length)}'
# apply (CLI-local checkout of the same repo)
apme format --apply ./project   # or: apme remediate ./project
```

Preview commit must match the checkout: the response `commit` is the HEAD
SHA of the repo the Gateway cloned. Apply the diffs only on a local
checkout at that same commit (`git checkout <commit>` first); if the
checkout has moved on, re-preview instead of applying stale diffs.

## Import project linking

`POST /api/v1/scans/import` (and CLI `--report-to-gateway`) mirrors
CLI-local check JSON into Gateway activity. Link the import to a
registered project by passing its id (`--project-id <uuid>` /
`"project_id": "<uuid>"`, picked from Discovery `create`, never
hardcoded); an unknown id fails with 404. Unlinked imports store under
`project_path` with a fresh per-scan session id, so they never share one
session row and never appear under the project's activity.

## Product skills

- `/apme-check` (`.agents/skills/apme-check/SKILL.md`): `--json` + REST+SSE recipes.
- `/apme-remediate` (`.agents/skills/apme-remediate/SKILL.md`): gates + atomic.
- `/apme-submit` (`.agents/skills/apme-submit/SKILL.md`): branch/PR shim.
- `/apme-format` (`.agents/skills/apme-format/SKILL.md`): `--apply`/`--check` + read-only preview.
