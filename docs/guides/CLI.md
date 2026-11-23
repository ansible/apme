# CLI Guide

APME's CLI is a thin gRPC client that connects to an Engine service. It
discovers the Engine using a three-tier strategy: (1) `APME_ENGINE_ADDRESS`
env var, (2) a running local daemon, (3) auto-start a local daemon. For scan
commands (`check`, `remediate`, `format`, `health-check`), this discovery
happens automatically. Commands like `sbom` and `suppress` talk to the Gateway
REST API or operate locally without an Engine. No full pod required — just
`pip install` and go (the OPA validator gRPC server is always started;
policy evaluation uses Podman by default or a local `opa` binary when
`OPA_USE_PODMAN=0`). OPA infrastructure failures (unavailable binary, eval
errors, circuit-breaker disable) propagate as validator R902 errors — scans do
not succeed with silently missing OPA findings.

## Installation

```bash
# Install a specific release (recommended)
pip install apme-engine@git+https://github.com/ansible/apme.git@v2026.9.4

# Install with AI escalation support (requires Abbenay daemon)
pip install "apme-engine[ai] @ git+https://github.com/ansible/apme.git@v2026.9.4"

# Install the latest development version (main branch)
pip install apme-engine@git+https://github.com/ansible/apme.git@main
```

Replace the tag with any [release version](https://github.com/ansible/apme/releases).
CLI git tags and container image tags are independent channels — a newer
CLI release does not imply matching operator/image versions are published yet.

### Requirements

- Python 3.12+
- Podman **or** `opa` binary on `$PATH` (required for OPA policy evaluation;
  Podman is the default, or set `OPA_USE_PODMAN=0` for a local binary)

## How it works

The CLI uses a **daemon architecture**:

1. On first use, `apme` starts a background daemon process
2. The daemon runs Engine, Native, OPA, and Ansible as in-process gRPC servers,
   plus Galaxy Proxy as an HTTP service (uvicorn), all on localhost
3. The CLI sends file bytes to the daemon over gRPC and receives results
4. The daemon stays running between commands for fast subsequent scans

```text
┌───────────┐  gRPC   ┌──────────────────────────────────────────┐
│  apme CLI │ ──────► │          Local Daemon (Engine)          │
│  (client) │         │  ┌────────┬───────┬─────────┬────────┐  │
└───────────┘         │  │ Native │  OPA  │ Ansible │ Galaxy │  │
                      │  │        │       │         │ Proxy  │  │
                      │  └────────┴───────┴─────────┴────────┘  │
                      └──────────────────────────────────────────┘
```

### Daemon management

```bash
apme daemon start     # start explicitly (auto-starts on first command)
apme daemon status    # check if running
apme daemon stop      # stop the background daemon
```

## Topology: CLI-local XOR Gateway-managed

APME has two exclusive operating modes for the same project content —
pick one per workflow (shared-space rule):

- **CLI-local (default):** `apme check`, `apme remediate`, `apme format`
  talk to the local Engine daemon over gRPC. No Gateway, no PostgreSQL,
  no persistent history. Fast, single-user, CI-friendly.
- **Gateway-managed:** registered projects (`POST /api/v1/projects`)
  run via Gateway REST + SSE (`POST /operation`, `POST /operate`,
  `GET /operation/events`). History, health, SBOM, and PR submit live in
  Gateway + PostgreSQL. The Engine is still the scanner; the Gateway is
  the orchestrator and system of record. `POST /operate` is an
  agent-oriented one-call convenience; the UI drives the stepped
  `/operation` flow (no UI wrapper by design).

Bridge between the spaces (additive, opt-in only):

```bash
apme check --json . > check.json
apme check . --report-to-gateway --gateway-url http://localhost:8080
# POSTs check JSON to Gateway POST /api/v1/scans/import (stored as activity).
apme check . --report-to-gateway --project-id <uuid>  # link to a project
```

There is no auto-sync: CLI-local runs stay local unless `--report-to-gateway`
is passed. Gateway-managed runs never write to the CLI-local tree.

## Commands

### `apme check` — scan for violations

```bash
apme check /path/to/playbook-or-project
apme check .                              # scan current directory
apme check playbooks/ roles/              # scan multiple paths
apme check . --exclude "tests/" "vendor/*"  # skip matching paths (targets first)
apme check --json .                       # JSON output
apme check --sarif .                      # SARIF 2.1.0 output (for GitHub/IDE)
apme check -v .                           # summary diagnostics + top 10 slow rules
apme check -vv .                          # full per-rule timing breakdown
apme check --diff .                       # show what remediate would change
apme check --ansible-version 2.17 .       # target a specific ansible-core version
apme check . --report-to-gateway --gateway-url http://localhost:8080
```

**Exit codes:** 0 = clean, 1 = violations found, 2 = error.

> Pass scan targets before `--exclude`: `--exclude` takes a greedy glob
> list, so a trailing target would be swallowed as another pattern.
> When a pattern could be mistaken for a path, disambiguate with `--`:
> `apme check --exclude "tests/" -- playbooks/`.

#### Dependency scanning options

```bash
apme check --skip-dep-scan .              # skip collection health + Python CVE
apme check --skip-collection-scan .       # skip only collection health scanning
apme check --skip-python-audit .          # skip only Python CVE audit
```

### `apme remediate` — fix violations automatically

```bash
apme remediate /path/to/project           # Tier 1 deterministic transforms
apme remediate playbooks/ roles/          # remediate multiple paths
apme remediate . --exclude "tests/"       # skip matching paths (targets first)
apme remediate --interactive .            # Gate 1 review for deterministic fixes
apme remediate --ai .                     # include Tier 2 AI-assisted fixes
apme remediate --interactive --ai .       # two-gate flow (Tier 1 then AI)
apme remediate --ai --auto-approve .      # no interactive review (CI mode)
apme remediate --max-passes 3 .           # limit convergence iterations
```

The remediation pipeline:
1. **Format** — normalize YAML style
2. **Idempotency gate** — verify formatting is stable
3. **Scan** — detect violations
4. **Tier 1** — generate/detect deterministic changes in a convergence loop (`t1-*` proposals are emitted only with `--interactive`)
5. **Gate 1** (with `--interactive`) — review deterministic `t1-*` proposals; approved proposals are then applied after `ApprovalRequest`
6. **Tier 2** (with `--ai`) — escalate remaining violations to AI provider
7. **Gate 2** (with `--ai`, without `--json`, without `--auto-approve`) — review AI `ai-*` proposals
8. **Auto-approve** (with `--auto-approve`) — accept all proposals in each approval gate

### `apme format` — normalize YAML style

```bash
apme format /path/to/project              # show diffs (no changes written)
apme format playbooks/ roles/             # format multiple paths
apme format --apply /path/to/project      # apply changes in place
apme format --check /path/to/project      # CI mode: exit 1 if changes needed
apme format . --exclude "vendor/**"       # exclude paths (targets first)
```

Normalizes indentation, key ordering, Jinja spacing, and removes tabs while
preserving YAML comments. Idempotent by design.

### `apme health-check` — service status

```bash
apme health-check                         # human-readable status
apme health-check --json                  # machine-readable
apme health-check --timeout 5             # custom timeout (seconds)
```

### `apme suppress` — manage violation suppressions

```bash
apme suppress add --rule-id L046 --mode rule_only .                # suppress all instances of a rule
apme suppress add --rule-id L046 --original-yaml $'- name: Example\n  ansible.builtin.command: whoami' .
apme suppress list .                                               # show active suppressions
apme suppress remove FINGERPRINT_PREFIX .                          # remove a suppression
apme suppress fingerprint --rule-id L046 --file snippet.yml        # dry-run: print hash only
```

Suppressions are stored in `.apme/suppressions.yml` within your project.
For full mode, pass the finding's complete original task YAML.
In full mode, identical normalized YAML for the same rule shares a
fingerprint across files. Moving a task therefore preserves its suppression.
Dependency audits of installed Python packages do not have a source YAML
file. In Backstage, acknowledging a dependency finding without source YAML
applies to all findings for that rule in the project. Package and advisory
specific suppression is tracked separately in
[REQ-020 TASK-004](../../.sdlc/specs/REQ-020-unified-supply-chain-security/tasks/TASK-004-vex-advisory-suppressions.md).

> `--mode rule_module` is deprecated: it warns on stderr and behaves as
> `rule_only`. Violation rows do not store resolved module FQCNs, so a
> rule_module suppression could never match server-side.

Violations → suppress recipe (fingerprint uses the same canonical hash
as the Gateway `_violation_fingerprint` server computation, ADR-055):

```bash
apme check --json . | jq -r '.violations[0].original_yaml' > snippet.yml
apme suppress fingerprint --rule-id L046 --file snippet.yml
apme suppress add --rule-id L046 --fingerprint <hash> --reason "justified" .
```

### `apme sbom` — software bill of materials

```bash
apme sbom PROJECT_ID                      # CycloneDX SBOM (requires Gateway)
apme sbom PROJECT_ID -o sbom.json         # write to file
apme sbom --path ./project -o sbom.json   # local SBOM, no registered project
```

`--path` builds a CycloneDX SBOM from local manifests
(`requirements.yml`, `requirements.txt`, ansible-core pin) without a
registered project. Register → scan → sbom recipe (Gateway-managed):

```bash
curl -X POST $GW/api/v1/projects -H 'Content-Type: application/json' -d '{"name":"demo","repo_url":"https://..."}'
curl -X POST $GW/api/v1/projects/<id>/operate -H 'Content-Type: application/json' -d '{"action":"check"}'
apme sbom <id> -o sbom.json
```

### `apme submit` — branch/PR from remediation (thin Gateway shim)

```bash
apme submit <project-id> --gateway-url http://localhost:8080
apme submit <project-id> --branch apme/fix-001 --no-pr
apme submit <project-id> --activity-id <scan-id> --json
apme submit <project-id> --idempotency-key <key>   # replay an earlier submit
```

Calls Gateway `POST /api/v1/projects/{id}/operation/submit` (Gateway owns
SCM push, ADR-056). Every submit carries an `Idempotency-Key` header: the
Gateway stores the token with the resulting operation, so repeating the call
with the same token returns that operation instead of pushing a second
branch. `apme submit` mints a fresh token per invocation; when a submit times
out (or returns 409/500/502/503/504) it prints `--idempotency-key <token>` —
reuse that token to replay rather than push again.

REST equivalent — create one key, keep it, and send it on the first request
too. A replay is only possible if the retry carries the same key as the
original call, so a key minted after a timeout cannot recover the earlier
operation:

```bash
# Once, before the first attempt: persist a replayable key at a stable path,
# one file per operation. Write it before the request so a crashed shell, a
# lost variable, or a dropped session cannot lose the key for an operation
# that may have landed.
KEY_FILE=".apme-submit-keys/$(date -u +%Y%m%dT%H%M%SZ)-apme-remediate-abc123.key"
mkdir -p "$(dirname "$KEY_FILE")"
set -o noclobber  # a later submit must never overwrite an unresolved key
python -c 'import uuid; print(uuid.uuid4().hex)' > "$KEY_FILE" || exit 1
# A failed write (the file already exists, or the directory is unwritable)
# must stop the recipe: running the request anyway would send an empty or
# stale key from an earlier operation, replaying that operation or 409-ing.

curl -X POST $GW/api/v1/projects/<id>/operation/submit \
  -H 'Content-Type: application/json' \
  -H "Idempotency-Key: $(cat "$KEY_FILE")" \
  -d '{"branch_name":"apme/remediate-abc123","create_pr":true}'
```

Each submit gets its own file, so starting a second submit from the same
directory cannot overwrite the key of an earlier one that is still unresolved.
Keep the file until the outcome is settled, then delete it: remove it only
after the call succeeded or a replay/409 response told you the operation's
final state, and keep the file name (or the key inside it) in your notes when
you escalate a 409. Deleting it while the outcome is unknown (timeout, 5xx)
throws away the only key that can recover the operation.

Re-send the *unchanged* body with the same key (read it back from
`$KEY_FILE`) when the call times out or fails with 500/502/503/504: the
outcome is unknown or retryable, so the key recovers the stored result instead
of pushing twice. A 409 is different — the Gateway already has a definitive
answer, so follow its message. Changing the branch, activity, or patch set
under the same key is rejected (409), and resending that request unchanged
repeats the conflict.

A timeout or 5xx is *not* settled by a branch/PR check. The Gateway stops
waiting on the provider call, but the request it already sent can still
complete afterwards, so an empty SCM check is only a snapshot and a new intent
started from it can publish a second branch or PR when the original lands.
A 409 does not settle it either: the Gateway also returns 409 when an earlier
tokened submit has claimed the scan or recorded no branch yet, and in both
cases the original push or PR may still be running, or may have finished
without being recorded. Settle the original first by re-sending the unchanged
body with the same key.

The outcome counts as known only when that key returns a result, or when SCM
shows evidence attributable to *this* submit: the commit it published on the
branch, plus the PR when `create_pr` was requested. Branch existence alone
proves nothing — the branch may be one the request named explicitly and that
already existed before the submit ran — and a branch that still points at its
previous head means the push never landed. When `create_pr` is true, a visible
branch is not a finished submit either: PR creation may still be running, so
keep the key until the PR itself appears. A 409 on its own leaves the outcome
unknown, and so does an empty SCM check.

Until one of those conditions holds, keep the key and do not start a new
intent. Use a fresh key file only after the original outcome is known to be
unrecorded *and* SCM shows neither the submitted commit nor the PR — that is
the case where nothing landed and a new intent is genuinely needed
(`set -o noclobber` also stops an accidental reuse).

#### Stranded claims

The Gateway releases a submit claim on cancellation, on a timeout, and on an
SCM error raised before any mutation. A claim recorded before an interruption
that bypasses those handlers — a Gateway restart, or a release update that
failed — stays in place with no branch on the scan. Every later attempt then
fails the same way, and because no published branch exists it is also the one
state where the SCM check above can never be satisfied:

```text
409 idempotency_conflict — A prior tokened submit claimed this scan but did not
complete. Verify SCM state; if no publish occurred, start a new operation with a
new token.
```

The Gateway has no endpoint that reports claim state, so decide from SCM:

1. Look for the commit this submit would have published, and for the PR when
   `create_pr` was requested. A branch at its previous head, or the absence of
   any commit attributable to the submit, means no publish occurred.
2. With that evidence, the 409 above *is* the Gateway's own instruction to start
   a new operation with a new token — take a fresh key file and submit again.
   The old key cannot be reused: its claim is the one blocking you.
3. A commit attributable to this submit on the branch, with no PR when
   `create_pr` was requested, is a partial state: the publish happened, so a
   fresh key must not be used — a new operation would push a second commit
   onto the same branch. Reconcile the branch and PR in SCM (PR creation may
   have completed after the Gateway stopped waiting), and escalate to a
   Gateway operator when the PR is genuinely absent: PR creation failed
   while the claim records the publish, and only an operator can settle that
   claim. Keep the key throughout — the commit on the branch is the
   submit-attributable evidence that the operation ran.
4. If you cannot rule out a publish still in flight — for example the SCM API is
   unavailable, or a push is still running against the same branch — keep the
   key and escalate to a Gateway operator rather than starting a second intent.
   Gateway-side claim-state reporting, which would remove that inference, is
   tracked in [#913](https://github.com/ansible/apme/issues/913).

Omitting the header is the only way to start an unclaimed operation; `apme
submit --no-idempotency-key` does that deliberately, and the call then cannot be
replayed safely.

### `apme format` via REST (Gateway-managed alternative)

CLI-local `apme format` uses Engine `FormatStream` gRPC. The
Gateway-managed equivalent is additive:

```bash
curl -X POST $GW/api/v1/projects/<id>/format | jq '.diffs | length'
```

`POST /projects/{id}/format` is an intentional read-only preview: the
Gateway never writes formatted content back to the repo (apply via
`apme format --apply` on a local checkout, or run `apme remediate`).
Recipe — preview on the registered project, apply locally:

```bash
preview_file=$(mktemp)
trap 'rm -f "$preview_file"' EXIT
curl -fsS -X POST "$GW/api/v1/projects/<id>/format" -o "$preview_file"
jq -r '.diffs[] | .path' "$preview_file"
commit=$(jq -r '.commit' "$preview_file")
git clone --branch <project-branch> --single-branch <repo-url> ./project \
  && git -C ./project checkout --detach "$commit" \
  && apme format --apply ./project
# The steps are chained so a failed clone or checkout stops the recipe: a
# detached-checkout failure would otherwise format the branch tip instead
# of the previewed commit.
```

## CI usage

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | No violations (check), no changes needed (format --check) |
| 1 | Violations found (check), changes needed (format --check) |
| 2 | Runtime error |

### JSON output for automation

```bash
apme check --json . | jq '.violations | length'
```

### SARIF for GitHub Code Scanning

```bash
apme check --sarif . > results.sarif
```

Upload the SARIF file to GitHub Code Scanning via the
[upload-sarif action](https://docs.github.com/en/code-security/code-scanning/integrating-with-code-scanning/uploading-a-sarif-file-to-github).

### Pre-commit hook

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: apme-check
        name: APME check
        entry: apme check
        language: system
        types: [yaml]
        pass_filenames: false
```

### GitHub Actions example

```yaml
- name: Install APME
  run: pip install apme-engine@git+https://github.com/ansible/apme.git@v2026.9.4

- name: Run APME check
  run: apme check --sarif . > results.sarif

- name: Upload SARIF
  uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: results.sarif
```

See [examples/ci/](../../examples/ci/) for complete workflow examples.

## Common options

| Flag | Commands | Description |
|------|----------|-------------|
| `--json` | check, remediate, health-check | Machine-readable JSON output |
| `--sarif` | check | SARIF 2.1.0 output |
| `--no-ansi` | all | Disable colored output |
| `--session ID` | check, format, remediate | Explicit session ID for venv reuse |
| `--ansible-version` | check, remediate | Target ansible-core version |
| `--collections` | check, remediate | Additional collection specs to install |
| `--timeout` | check, remediate, health-check | gRPC timeout in seconds |
| `--exclude GLOB...` | check, format, remediate | Skip matching paths (pass targets first) |
| `target...` | check, format, remediate | One or more files/directories (default: `.`) |

## Limitations vs full deployment

The CLI daemon is designed for quick evaluation and CI pipelines. For
production use or full feature access, use a [deployment method](DEPLOYMENT.md).

| Capability | CLI (daemon) | Podman / bootc / operator |
|------------|:------------:|:---------------------:|
| Check (scan for violations) | Yes | Yes |
| Remediate (Tier 1 transforms) | Yes | Yes |
| Format (YAML normalization) | Yes | Yes |
| Secret scanning (Gitleaks) | Not available (daemon does not start gitleaks validator) | Built-in |
| AI-assisted remediation | Requires Abbenay daemon | Built-in (pod) |
| Web UI dashboard | No | Yes |
| Persistent scan history | No | Yes (Gateway + PostgreSQL) |
| Multi-user / shared service | No (single-user) | Yes |
| Collection Health scanning | Not available (daemon does not start optional validators) | Yes |
| Python CVE audit | Not available (daemon does not start optional validators) | Yes |
| SBOM generation | Requires Gateway | Yes |
| Atomic upgrades / rollback | No | Yes (bootc) |
| Horizontal scaling | No | No (current operator; future topology — ADR-012) |

### When to use the CLI

- Evaluating APME on a project for the first time
- CI/CD pipelines (GitHub Actions, GitLab CI, Jenkins)
- Developer workstation pre-commit checks
- Scripted automation with JSON/SARIF output

### When to deploy

- You need the web UI for team visibility
- You want persistent scan history and analytics
- You need AI-assisted remediation without managing Abbenay separately
- You're running APME as a shared service for multiple teams
- You want atomic upgrades and production reliability (bootc/operator)

## Configuration

The CLI reads configuration from these sources (in priority order):

1. CLI flags (highest priority)
2. Environment variables (`APME_ENGINE_ADDRESS`, `APME_ABBENAY_ADDR`, etc.)
3. Project-level `.apme/rules.yml` for rule configuration

### Rule configuration

Create `.apme/rules.yml` in your project root to customize rule behavior:

```yaml
rules:
  L003:
    enabled: false
  R101:
    severity: warning
```

See [Rule Configuration](RULE_CONFIGURATION.md) for the full reference.

## Related

- [Deployment Guide](DEPLOYMENT.md) — Podman pod, bootc VM, APME operator
- [Development Guide](DEVELOPMENT.md) — Contributing, tox environments, testing
- [Rule Catalog](../rules/RULE_CATALOG.md) — Complete rule listing
- [Architecture](../architecture/) — Pipeline design and service contracts
