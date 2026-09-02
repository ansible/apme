# GitHub Actions Examples

Ready-to-use workflows for scanning Ansible content with the APME Action.

## Available Workflows

| File | Trigger | Purpose |
|------|---------|---------|
| `apme-hosted.yml` | Pull request | Scan via hosted APME deployment + SARIF annotations + PR comment |
| `apme-format.yml` | Pull request | YAML formatting check (fast, no server needed) |

## How It Works

The APME Action connects to an existing APME deployment (Kubernetes, VM, or any
running pod). No image pulls, no pod startup — just the CLI calling your
centralized instance.

```yaml
- uses: ansible/apme@8629999da689ef4cd7e0f65a86129dd06627b77e # unreleased dev (PR #500)
  with:
    engine-address: ${{ secrets.APME_ENGINE_ADDRESS }}
    target: .
```

Pin to an immutable commit SHA. The example above is an unreleased development
revision from PR #500; replace it with a published release SHA once available
(see issue #501 for the `v1` action tag).

**Trade-off:** ~10s total. Requires network connectivity from the runner to your
APME deployment and a running instance.

## Quick Start

The simplest way to add full APME scanning to your repo:

```yaml
# .github/workflows/apme.yml
name: APME Check
on:
  pull_request:
    paths: ['**.yml', '**.yaml']

jobs:
  apme:
    runs-on: ubuntu-24.04
    permissions:
      security-events: write
      pull-requests: write
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4
      - uses: ansible/apme@8629999da689ef4cd7e0f65a86129dd06627b77e # unreleased dev (PR #500)
        with:
          engine-address: ${{ secrets.APME_ENGINE_ADDRESS }}
```

This will:

1. Install the APME CLI
2. Connect to your hosted APME deployment
3. Scan your Ansible content
4. Upload SARIF to GitHub Code Scanning (inline annotations on PR diffs)
5. Post a summary comment on the PR

### Prerequisites

- A running APME deployment accessible from your GitHub Actions runners
  (deployed via APME operator, bootc VM, or Podman pod)
- `APME_ENGINE_ADDRESS` secret set to the Engine gRPC endpoint (`host:port`)
  reachable from the runner — for operator installs, typically
  `<name>-engine.<namespace>.svc:50051` (ClusterIP; plaintext gRPC). The
  operator exposes Gateway REST on Route/Ingress only; Engine `:50051` is not
  on the public edge (use in-cluster runners, VPN, or bootc/Podman host access).
  See [ADR-054](../../.sdlc/adrs/ADR-054-production-deployment.md) §Hosted CI
  Engine access.

### Action Inputs

| Input | Default | Description |
|-------|---------|-------------|
| `target` | `.` | Path to scan |
| `ansible-version` | | ansible-core version to validate against |
| `collections` | | Space-separated collection specs |
| `args` | | Additional arguments passed to `apme check` |
| `engine-address` | | **Required.** Engine gRPC address (host:port) |
| `sarif` | `true` | Upload SARIF for Code Scanning annotations |
| `upload-artifact` | `false` | Upload JSON results as a workflow artifact |
| `comment` | `true` | Post a PR summary comment |
| `cli-version` | | Pin `apme-engine` version (e.g. `0.1.0`); defaults to latest |

### Action Outputs

| Output | Description |
|--------|-------------|
| `exit-code` | `0` = clean, `1` = violations, `2` = error |
| `violation-count` | Number of violations found (empty in plain mode) |
| `sarif-path` | Path to the generated SARIF file |

## SARIF and Inline Annotations

When `sarif: true` (the default), APME generates a
[SARIF 2.1.0](https://docs.oasis-open.org/sarif/sarif/v2.1.0/) file and
uploads it to GitHub Code Scanning. This gives you:

- **Inline annotations** on the PR Files Changed tab (like CodeQL)
- **Entries in the Security tab** for org-wide visibility
- **Alert management** (dismiss, reopen, track resolution)

> **Note:** SARIF upload on private repos requires
> [GitHub Advanced Security (GHAS)](https://docs.github.com/en/get-started/learning-about-github/about-github-advanced-security).
> Public repos get Code Scanning for free.

## PR Summary Comment

When `comment: true` (the default), the Action posts an updatable markdown
comment on the PR showing:

- Total violation count with pass/fail indicator
- Breakdown by severity
- Top 5 rules triggered (collapsible)
- Remediation summary (auto-fixable, AI candidate, manual review)

The comment updates in place on re-runs instead of creating duplicates.

## Required Status Checks

To make APME a required check before merging:

1. Add the workflow to your repo (e.g. `apme-hosted.yml`)
2. Open a PR so the check runs at least once
3. Go to **Settings > Branches > Branch protection rules**
4. Edit the rule for `main`
5. Enable **Require status checks to pass before merging**
6. Search for and select **APME Scan** (or whatever `name:` you used)

## Customization

### Scan a specific directory

```yaml
- uses: ansible/apme@8629999da689ef4cd7e0f65a86129dd06627b77e # unreleased dev (PR #500)
  with:
    engine-address: ${{ secrets.APME_ENGINE_ADDRESS }}
    target: playbooks/
```

### Target specific ansible-core version

```yaml
- uses: ansible/apme@8629999da689ef4cd7e0f65a86129dd06627b77e # unreleased dev (PR #500)
  with:
    engine-address: ${{ secrets.APME_ENGINE_ADDRESS }}
    ansible-version: "2.18"
```

### Non-blocking scan (continue on violations)

```yaml
- uses: ansible/apme@8629999da689ef4cd7e0f65a86129dd06627b77e # unreleased dev (PR #500)
  id: scan
  continue-on-error: true
  with:
    engine-address: ${{ secrets.APME_ENGINE_ADDRESS }}
- run: echo "Found ${{ steps.scan.outputs.violation-count }} violations"
```

### Self-hosted runners with internal APME

```yaml
- uses: ansible/apme@8629999da689ef4cd7e0f65a86129dd06627b77e # unreleased dev (PR #500)
  with:
    engine-address: apme-engine.internal.svc:50051
```

### Manual SARIF upload (without the Action)

```yaml
- run: |
    set +e
    apme check . --sarif > apme.sarif
    ec=$?
    set -e
    if [ "$ec" -eq 2 ]; then exit "$ec"; fi
  env:
    APME_ENGINE_ADDRESS: ${{ secrets.APME_ENGINE_ADDRESS }}
- uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: apme.sarif
    category: apme
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | No violations found |
| 1 | Violations found (or format changes needed) |
| 2 | Error (file not found, engine failure, invalid arguments) |

## Requirements

- **Runner:** Any runner with Python 3.10+ (e.g. `ubuntu-24.04`).
- **Permissions:** `security-events: write` for SARIF, `pull-requests: write`
  for PR comments.
- **Network:** Connectivity from the runner to your APME Engine gRPC endpoint.
- **Secret:** `APME_ENGINE_ADDRESS` set to `host:port` of your APME deployment.
