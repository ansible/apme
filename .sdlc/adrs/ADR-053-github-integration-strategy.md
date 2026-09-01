# ADR-053: GitHub Integration Strategy

## Status

Accepted

## Date

2026-04-10

## Context

APME is a multi-service system: Engine orchestrator, Native/OPA/Ansible/Gitleaks
validators, Galaxy Proxy (PEP 503 HTTP on :8765), Collection Health, and
Dependency Audit — Engine and validators communicate via gRPC; Galaxy Proxy is
the HTTP exception. The CLI delegates to this full stack. Running only the
pip-installed `apme-engine` in CI misses Gitleaks secret detection, collection
health analysis, dependency auditing, and Gateway persistence.

Example workflows in `examples/ci/github-actions/` run `apme check --json` and
fail on violations. There is no inline annotation, SARIF upload, or PR comment
support. ADR-050 (proposed) covers Gateway-side PR creation. ADR-038 defines the
public API and webhook model. Neither addresses CI-triggered annotation/feedback.

GitHub Code Scanning natively renders SARIF (Static Analysis Results Interchange
Format) as inline annotations on the Files Changed tab and entries in the
Security tab. This is the standard mechanism used by CodeQL, Semgrep, Trivy, and
other analysis tools.

### Decision Drivers

- The full APME stack (all validators + Galaxy Proxy) is required for complete
  scanning. A CI integration that only runs the engine is incomplete.
- Developers expect inline annotations on PR diffs.
- Organizations deploying APME centrally need CI to connect to their deployment,
  not spin up throwaway instances.
- GitHub natively supports SARIF upload via `github/codeql-action/upload-sarif`.

## Decision

**APME will integrate with GitHub through SARIF output from the CLI and a
composite GitHub Action that connects to a hosted APME deployment.**

### 1. SARIF Output (`apme check --sarif`)

The CLI gains a `--sarif` flag that writes SARIF 2.1.0 JSON to stdout. The
SARIF output maps:

| APME field | SARIF field |
|-----------|-------------|
| `rule_id` | `reportingDescriptor.id` |
| `message` | `result.message.text` |
| `file` | `result.locations[].physicalLocation.artifactLocation.uri` |
| `line` | `result.locations[].physicalLocation.region.startLine` |
| `severity` (critical/high) | `error` |
| `severity` (medium) | `warning` |
| `severity` (low/info) | `note` |

Uploaded via `github/codeql-action/upload-sarif`, this populates the Security
tab and adds inline annotations on PR diffs with zero custom code.

### 2. Action Mode: Hosted

The Action connects to an existing APME deployment (Kubernetes via Helm chart,
VM via bootc, or any running pod). This gives the exact same validation as a
production deployment: all validators, Gitleaks secret scanning, collection
health, and dependency auditing.

```yaml
- uses: ansible/apme@v1
  with:
    engine-address: ${{ secrets.APME_ENGINE_ADDRESS }}
    target: .
```

The Action:
1. Installs the CLI via `uv pip install`
2. Points `APME_ENGINE_ADDRESS` at the hosted Engine
3. Runs `apme check` against the remote deployment
4. Generates SARIF and uploads to Code Scanning
5. Optionally posts a PR summary comment

This avoids image pulls and pod startup, making scans fast (~10s). It requires
network connectivity from the runner to the APME deployment.

### 3. PR Summary Comment

After the scan, the Action optionally posts a markdown summary comment on the
PR with violation counts by severity, top rules triggered, and remediation
summary. The comment uses a hidden HTML marker for idempotent update-in-place.

### What This ADR Does NOT Cover

- **Gateway-side PR creation** — covered by ADR-050.
- **Webhook notifications** — covered by ADR-038.
- **Per-line review comments** — SARIF annotations serve this purpose.

## Alternatives Considered

### Alternative 1: Full-Stack Ephemeral Pod in Runner

**Description**: Spin up the complete APME pod inside the GitHub Actions runner
using pre-built GHCR images, scan, then tear down.

**Pros**: Self-contained, no external deployment needed.

**Cons**: ~60-90s startup for image pulls + pod initialization. Requires Podman
on the runner. Custom runners may not have Podman. Couples the Action to
container orchestration complexity (pod YAML, health waits, cleanup). GitHub
Actions Docker layer caching mitigates pulls but not pod startup.

**Why not chosen**: Organizations using APME seriously enough for CI integration
will have a running deployment (Helm, bootc, Podman pod). The hosted approach
is simpler, faster, and reuses that investment. The maintenance burden of
embedding a pod spec in the Action is not justified.

### Alternative 2: CLI-only Action (pip install, daemon mode)

**Description**: Install `apme-engine` via pip and run the daemon-mode CLI.

**Pros**: Fast startup, simple implementation.

**Cons**: Misses Gitleaks (requires external binary), collection health,
dependency audit, and Gateway persistence. The daemon starts only
Engine + Native + OPA + Ansible + Galaxy Proxy — not the full stack. Users get
an incomplete scan compared to their production deployment.

**Why not chosen**: An incomplete scan creates false confidence. The whole
point of CI integration is running the same validation as production.

### Alternative 3: GitHub Checks API Instead of SARIF

**Description**: Use the Checks API for per-line annotations.

**Pros**: Rich annotation model.

**Cons**: Requires GitHub App or PAT with `checks:write`. Does not populate the
Security tab. More complex to implement. Most tools use SARIF now.

**Why not chosen**: SARIF is the industry standard. It requires only
`security-events:write` and provides both inline annotations and Security tab.

## Consequences

### Positive

- **Full-stack validation in CI** — the same validators, the same rules, the
  same results as production. No gaps.
- **Fast scans** — no image pulls or pod startup, just a CLI call (~10s).
- **Inline annotations** — SARIF upload gives per-line annotations on PR diffs.
- **Security tab integration** — violations appear in the repository's Security
  tab for org-wide visibility.

### Negative

- **Requires a running APME deployment** — organizations must deploy APME
  (via Helm, bootc, or Podman) before CI integration works. This is a
  prerequisite, not a limitation for production users.
- **Network connectivity** — runners must reach the APME Engine gRPC endpoint.
  Self-hosted runners or VPN-connected runners handle this for private
  deployments.
- **GitHub Advanced Security for private repos** — SARIF upload on private repos
  requires GHAS. Public repos get it for free.

### Neutral

- The `--sarif` flag works outside GitHub (GitLab, Azure DevOps, etc.).
- The existing `--json` output is unchanged.
- Example workflows show hosted mode and manual SARIF upload.

## Implementation Notes

### SARIF Module

`src/apme_engine/cli/sarif.py` implements `violations_to_sarif()` — a pure
function converting violation dicts to SARIF 2.1.0 JSON. Tested independently.

### Hosted Action Steps

1. `actions/checkout` — check out the repo
2. `astral-sh/setup-uv` + `uv pip install --system apme-engine`
3. Run `apme check` with `APME_ENGINE_ADDRESS` set to the hosted instance:
   - default to `apme check --json` when PR comments or artifacts are enabled
   - derive SARIF from the JSON output when SARIF upload/annotations are needed
   - use `apme check --sarif` directly only when PR comments and artifacts are disabled
4. Upload SARIF via `github/codeql-action/upload-sarif` when SARIF output is available
5. Post PR summary comment via `actions/github-script` when PR comments are enabled

## Related Decisions

- ADR-004: Podman pod deployment
- ADR-012: Scale pods, not services (full stack is the unit)
- ADR-015: GitHub Actions CI with prek
- ADR-021: Proactive PR feedback
- ADR-038: Public Data API (hosted mode queries the same API)
- ADR-047: tox as sole orchestration
- ADR-050: Post-remediation PR creation
- ADR-054: Production deployment (Helm + bootc for hosted APME)
