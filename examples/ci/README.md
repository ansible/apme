# CI/CD Integration Examples

Copy-paste ready examples for integrating APME into your CI/CD pipelines.

## Overview

APME provides several CLI commands suitable for CI/CD:

| Command | Purpose | Exit Code |
|---------|---------|-----------|
| `apme check .` | Scan for violations | 0 = pass, 1 = violations, 2 = error |
| `apme format --check .` | Check YAML formatting | 0 = clean, 1 = changes needed, 2 = error |
| `apme remediate .` | Auto-fix Tier 1 violations | 0 = all fixed, 1 = remaining violations, 2 = error |

## Output Formats

```bash
apme check .                    # Terminal output (default)
apme check . --json             # JSON for automation
apme check . --json > results.json  # Save to file
```

## Installation

APME requires Python 3.10+ and can be installed via pip:

```bash
pip install apme-engine
```

## Examples

| Directory | Description |
|-----------|-------------|
| [github-actions/](github-actions/) | GitHub Actions workflow examples |
| [pre-commit/](pre-commit/) | Pre-commit hook configuration |

## Quick Start

### Check a registered project through the Gateway

Use the Gateway's REST operation API when CI needs to check a project
registered with a running APME deployment. The request starts a check of
the project's configured repository and branch. It does not upload the
CI checkout.

Run this Bash example with `curl` and `jq` available. Set
`APME_GATEWAY_URL` to the Gateway origin and `APME_PROJECT_ID` to the
registered project ID.

```bash
operation_url="${APME_GATEWAY_URL%/}/api/v1/projects/${APME_PROJECT_ID}/operation"
response=$(curl --fail --silent --show-error --max-time 30 \
  --header 'Content-Type: application/json' \
  --data '{"action":"check"}' "$operation_url") || exit 2
operation_id=$(jq -er '.operation_id' <<< "$response") || exit 2

deadline=$((SECONDS + 600))
while (( SECONDS < deadline )); do
  snapshot=$(curl --fail --silent --show-error --max-time 10 "$operation_url") || exit 2
  if ! jq -e --arg id "$operation_id" '.operation_id == $id' <<< "$snapshot" >/dev/null; then
    echo 'The project now has a different operation.' >&2
    exit 2
  fi
  status=$(jq -er '.status' <<< "$snapshot") || exit 2
  case "$status" in
    completed)
      count=$(jq -er '.result.total_violations | numbers' <<< "$snapshot") || exit 2
      jq -c '{operation_id, scan_id, total_violations: .result.total_violations}' <<< "$snapshot"
      if [[ "$count" == 0 ]]; then exit 0; fi
      exit 1
      ;;
    failed|expired|cancelled|pr_submitted)
      printf 'Gateway operation %s ended with status %s.\n' "$operation_id" "$status" >&2
      exit 2
      ;;
  esac
  sleep 2
done
echo 'Timed out waiting for the Gateway check.' >&2
exit 2
```

The POST returns HTTP 201 and an `operation_id`. HTTP 409 means the
project already has an active operation. This example exits with 0 for
a clean check, 1 for findings, or 2 for an operation or client error.
For progress, subscribe to `GET /api/v1/projects/{project_id}/operation/events`
and verify the operation ID from the first event. Retrieve persisted
results from `GET /api/v1/activity/{scan_id}` using the snapshot's `scan_id`.

### GitHub Actions

Copy one of these workflows to `.github/workflows/` in your Ansible repo:

```bash
# Download the basic check workflow
curl -o .github/workflows/apme.yml \
  https://raw.githubusercontent.com/ansible/apme/main/examples/ci/github-actions/apme-check.yml
```

### Pre-commit

Add to your `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: local
    hooks:
      - id: apme-check
        name: APME Ansible check
        entry: apme check
        language: system
        types: [yaml]
        pass_filenames: false
```

See [pre-commit/README.md](pre-commit/README.md) for full setup instructions.
