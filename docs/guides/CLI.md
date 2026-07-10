# CLI Guide

APME's CLI is a thin gRPC client that connects to a Primary service. It
discovers the Primary using a three-tier strategy: (1) `APME_PRIMARY_ADDRESS`
env var, (2) a running local daemon, (3) auto-start a local daemon. For scan
commands (`check`, `remediate`, `format`, `health-check`), this discovery
happens automatically. Commands like `sbom` and `suppress` talk to the Gateway
REST API or operate locally without a Primary. No full pod required — just
`pip install` and go (OPA uses a Podman container by default; set
`OPA_USE_PODMAN=0` for a local binary, or OPA is skipped if neither is
available).

## Installation

```bash
# Install a specific release (recommended)
pip install apme-engine@git+https://github.com/ansible/apme.git@v2026.4.1

# Install with AI escalation support (requires Abbenay daemon)
pip install "apme-engine[ai] @ git+https://github.com/ansible/apme.git@v2026.4.1"

# Install the latest development version (main branch)
pip install apme-engine@git+https://github.com/ansible/apme.git@main
```

Replace the tag with any [release version](https://github.com/ansible/apme/releases).

### Requirements

- Python 3.10+
- Podman **or** `opa` binary on `$PATH` (optional — OPA uses Podman by default; falls back to local `opa`; skipped if neither is available)

## How it works

The CLI uses a **daemon architecture**:

1. On first use, `apme` starts a background daemon process
2. The daemon runs Primary, Native, OPA, and Ansible as in-process gRPC servers,
   plus Galaxy Proxy as an HTTP service (uvicorn), all on localhost
3. The CLI sends file bytes to the daemon over gRPC and receives results
4. The daemon stays running between commands for fast subsequent scans

```
┌───────────┐  gRPC   ┌──────────────────────────────────────────┐
│  apme CLI │ ──────► │          Local Daemon (Primary)          │
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

## Commands

### `apme check` — scan for violations

```bash
apme check /path/to/playbook-or-project
apme check .                              # scan current directory
apme check --json .                       # JSON output
apme check --sarif .                      # SARIF 2.1.0 output (for GitHub/IDE)
apme check -v .                           # summary diagnostics + top 10 slow rules
apme check -vv .                          # full per-rule timing breakdown
apme check --diff .                       # show what remediate would change
apme check --ansible-version 2.17 .       # target a specific ansible-core version
```

**Exit codes:** 0 = clean, 1 = violations found, 2 = error.

#### Dependency scanning options

```bash
apme check --skip-dep-scan .              # skip collection health + Python CVE
apme check --skip-collection-scan .       # skip only collection health scanning
apme check --skip-python-audit .          # skip only Python CVE audit
```

### `apme remediate` — fix violations automatically

```bash
apme remediate /path/to/project           # Tier 1 deterministic transforms
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
4. **Tier 1** — generate/detect deterministic changes and `t1-*` proposals in a convergence loop
5. **Gate 1** (with `--interactive`) — review deterministic `t1-*` proposals; approved proposals are then applied after `ApprovalRequest`
6. **Tier 2** (with `--ai`) — escalate remaining violations to AI provider
7. **Gate 2** (with `--ai`, without `--json`, without `--auto-approve`) — review AI `ai-*` proposals
8. **Auto-approve** (with `--auto-approve`) — accept all proposals in each approval gate

### `apme format` — normalize YAML style

```bash
apme format /path/to/project              # show diffs (no changes written)
apme format --apply /path/to/project      # apply changes in place
apme format --check /path/to/project      # CI mode: exit 1 if changes needed
apme format --exclude "vendor/**" .       # exclude paths
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
apme suppress add --rule-id L046 --original-yaml 'name: example' . # suppress a specific occurrence (full mode)
apme suppress list .                                               # show active suppressions
apme suppress remove FINGERPRINT_PREFIX .                          # remove a suppression
```

Suppressions are stored in `.apme/suppressions.yml` within your project.

### `apme sbom` — software bill of materials

```bash
apme sbom PROJECT_ID                      # CycloneDX SBOM (requires Gateway)
apme sbom PROJECT_ID -o sbom.json         # write to file
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
  run: pip install apme-engine@git+https://github.com/ansible/apme.git@v2026.4.1

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
| `--timeout` | check, health-check | gRPC timeout in seconds |

## Limitations vs full deployment

The CLI daemon is designed for quick evaluation and CI pipelines. For
production use or full feature access, use a [deployment method](DEPLOYMENT.md).

| Capability | CLI (daemon) | Podman / bootc / Helm |
|------------|:------------:|:---------------------:|
| Check (scan for violations) | Yes | Yes |
| Remediate (Tier 1 transforms) | Yes | Yes |
| Format (YAML normalization) | Yes | Yes |
| Secret scanning (Gitleaks) | Not available (daemon does not start gitleaks validator) | Built-in |
| AI-assisted remediation | Requires Abbenay daemon | Built-in (pod) |
| Web UI dashboard | No | Yes |
| Persistent scan history | No | Yes (Gateway + SQLite) |
| Multi-user / shared service | No (single-user) | Yes |
| Collection Health scanning | Not available (daemon does not start optional validators) | Yes |
| Python CVE audit | Not available (daemon does not start optional validators) | Yes |
| SBOM generation | Requires Gateway | Yes |
| Atomic upgrades / rollback | No | Yes (bootc) |
| Horizontal scaling | No | Yes (Helm) |

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
- You want atomic upgrades and production reliability (bootc/Helm)

## Configuration

The CLI reads configuration from these sources (in priority order):

1. CLI flags (highest priority)
2. Environment variables (`APME_PRIMARY_ADDRESS`, `APME_ABBENAY_ADDR`, etc.)
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

- [Deployment Guide](DEPLOYMENT.md) — Podman pod, bootc VM, Helm chart
- [Development Guide](DEVELOPMENT.md) — Contributing, tox environments, testing
- [Rule Catalog](../rules/RULE_CATALOG.md) — Complete rule listing
- [Architecture](../architecture/) — Pipeline design and service contracts
