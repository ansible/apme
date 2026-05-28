# APME Test Plan

A comprehensive test plan for validating APME (Ansible Policy & Modernization Engine) functionality. Use this guide to exercise all features before deployment or to provide structured feedback.

## Prerequisites

| Requirement | Purpose |
|-------------|---------|
| Podman (rootless) | Container deployment |
| Git | Clone repo and manage content |
| tox | Run build/up/cli commands |
| Sample Ansible content | Files to scan |
| 5-30 minutes | Depending on test depth |

## Quick Validation (5 minutes)

Verify basic installation and operation:

```bash
# 1. Clone the repo and build containers
git clone https://github.com/ansible/apme.git
cd apme
tox -e build

# 2. Start the pod
tox -e up

# 3. Check version
tox -e cli -- --version

# 4. Scan sample content
tox -e cli -- check /path/to/playbook.yml

# 5. Verify exit codes
echo "Exit code: $?"
# 0 = clean, 1 = violations found, 2 = error

# 6. Stop the pod when done
tox -e down
```

If step 4 works, APME is functional. Continue with detailed testing below.

---

## CLI Command Reference

> **Note**: When running from the repo with the Podman pod, prefix commands with `tox -e cli --`. For example: `tox -e cli -- check .` instead of `apme check .`

### Core Commands

| Command | Purpose | Exit Codes |
|---------|---------|------------|
| `apme check .` | Scan for violations (read-only) | 0=clean, 1=violations, 2=error |
| `apme check --json .` | JSON output for automation | 0/1/2 |
| `apme check --diff .` | Show what remediate would change | 0/1/2 |
| `apme format .` | Show formatting changes (dry-run) | 0=clean, 2=error |
| `apme format --apply .` | Apply formatting in-place | 0/2 |
| `apme format --check .` | CI mode: fail if changes needed | 0=clean, 1=changes, 2=error |
| `apme remediate .` | Auto-fix Tier 1 violations | 0=all fixed, 1=remaining, 2=error |
| `apme health-check` | Check service health | 0=healthy, 1=unhealthy |
| `apme suppress add` | Add violation suppression | 0=added, 2=error |
| `apme suppress list` | List all suppressions | 0 |
| `apme suppress remove` | Remove a suppression | 0=removed, 2=error |

### Common Options

| Option | Commands | Description |
|--------|----------|-------------|
| `--json` | check, remediate, health-check | Output structured JSON |
| `--ansible-version VERSION` | check, remediate | Target ansible-core version (e.g., 2.18, 2.20) |
| `--skip-dep-scan` | check, remediate | Skip collection health + Python CVE audit |
| `--skip-collection-scan` | check, remediate | Skip collection health only |
| `--skip-python-audit` | check, remediate | Skip Python CVE audit only |
| `-v` | all | Verbose output (summary + top 10 slowest rules) |
| `-vv` | all | Very verbose (full per-rule breakdown) |
| `--no-ansi` | all | Disable colors (CI mode) |
| `--show-suppressed` | check, remediate | Include suppressed violations in output |

---

## Test Scenarios

### 1. Basic Scanning

```bash
# Scan current directory
apme check .

# Scan specific file
apme check playbook.yml

# Scan with JSON output
apme check --json . > report.json
cat report.json | jq '.count'

# Show diffs of what would be fixed
apme check --diff .
```

**Expected**: Violations listed with rule ID, file, line, and message.

### 2. YAML Formatting

```bash
# Preview formatting changes
apme format .

# Apply formatting in-place
apme format --apply .

# CI check (exit 1 if changes needed)
apme format --check .
echo "Exit code: $?"
```

**Expected**: Consistent 2-space indentation, Jinja spacing (`{{ var }}`), key ordering.

### 3. Auto-Remediation

```bash
# Remediate Tier 1 violations (writes files)
apme remediate .

# Check remaining violations
apme check .
```

**Expected**: Auto-fixable violations resolved; remaining violations require manual review.

### 4. Health Check

```bash
# Check all services
apme health-check

# JSON output
apme health-check --json
```

**Expected**: All services show "ok" status.

### 5. Violation Suppression (ADR-055)

Suppress known violations using content-based fingerprints:

```bash
# Run check to get fingerprints
apme check --json . | jq '.violations[] | {rule_id, fingerprint}'

# Add a suppression (baseline a known issue)
apme suppress add L026 --fingerprint abc123def456...

# List all suppressions
apme suppress list

# Remove a suppression
apme suppress remove L026 --fingerprint abc123def456...

# Show suppressed violations in output
apme check --show-suppressed .
```

**Expected**: Suppressed violations stored in `.apme/suppressions.yml`, excluded from default output.

### 6. Verbose Diagnostics

```bash
# Summary diagnostics
apme check -v .

# Full per-rule timing
apme check -vv .
```

**Expected**: Rule execution times, slowest rules identified.

---

## Container Deployment Tests

### Pod Startup and Basic Scan

```bash
# Build and start the pod (9 containers)
tox -e up

# Wait for services to be ready
sleep 10

# Run health check
tox -e cli -- health-check

# Scan current directory via CLI container
tox -e cli -- check .

# Scan with JSON output
tox -e cli -- check --json .
```

### UI Verification

1. Open http://localhost:8081 in browser
2. Verify dashboard loads
3. Create a new project (click "New Project")
4. Run a scan on the project
5. Verify violations display correctly

### Cleanup

```bash
# Stop the pod
tox -e down

# Full cleanup (delete database + cache)
tox -e wipe
```

---

## CI/CD Integration Tests

### GitHub Actions

Create `.github/workflows/apme-test.yml`:

```yaml
name: APME Test
on: [push, pull_request]

jobs:
  apme:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: ansible/apme@v1
        with:
          path: "."
          fail-on-violations: "true"
```

**Verify**:
- Action installs and runs
- Violations cause workflow failure
- Report artifact is uploaded

### GitLab CI

Create `.gitlab-ci.yml`:

```yaml
include:
  - local: '.gitlab/apme-check.yml'

# Or inline using container image:
apme-check:
  image: ghcr.io/ansible/apme-cli:latest
  services:
    - name: ghcr.io/ansible/apme-primary:latest
      alias: primary
  script:
    - apme check . --json > apme-report.json
  variables:
    APME_PRIMARY_HOST: primary
    APME_PRIMARY_PORT: "50051"
  artifacts:
    paths:
      - apme-report.json
```

**Verify**:
- Job runs successfully
- JSON report artifact is created

### Pre-commit Hook

Add to `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: local
    hooks:
      - id: apme-format
        name: APME Format Check
        entry: apme format --check
        language: system
        types: [yaml]
        pass_filenames: false
```

**Verify**:
- Hook runs on `git commit`
- Blocks commit if formatting needed

---

## Rule Coverage Spot Checks

Use the test fixtures in `tests/fixtures/customer-test/`:

### Lint Rules (L)

```bash
# L003: Play without name
apme check tests/fixtures/customer-test/violations-playbook.yml
# Expected: L003 violation on first play

# L026: Non-FQCN module
# Expected: L026 violations for command, shell, file, copy

# L040: Tab character (in messy-formatting.yml)
apme format --check tests/fixtures/customer-test/messy-formatting.yml
```

### Modernize Rules (M)

```bash
# M001: Short module name
apme check tests/fixtures/customer-test/deprecated-modules.yml
# Expected: M001 for debug → ansible.builtin.debug

# M009: with_items → loop
# Expected: M009 violations
```

### Risk Rules (R)

```bash
# R108: become without become_user
apme check tests/fixtures/customer-test/violations-playbook.yml
# Expected: R108 violation
```

### AAP-Specific Rules (A)

```bash
# A001: Hardcoded template IDs (should use named_url)
# Look for tasks using controller_* modules with numeric IDs
apme check playbooks/controller-config.yml
# Expected: A001 if using id: 123 instead of name: "My Template"

# A002: Deprecated AAP API endpoints or ansible.hub modules
# Look for tasks using removed/deprecated Controller API paths
apme check playbooks/aap-automation.yml
# Expected: A002 for deprecated endpoints or hub module usage
```

### Secret Detection (SEC)

```bash
# SEC:* Hardcoded credentials
apme check tests/fixtures/customer-test/secrets-test.yml
# Expected: Multiple SEC:* violations for API keys, passwords

# Vault reference (should NOT trigger)
apme check tests/fixtures/customer-test/vault-reference.yml
# Expected: 0 SEC:* violations (Jinja2 filtering works)
```

### Clean Reference

```bash
# Should pass all checks
apme check tests/fixtures/customer-test/clean-playbook.yml
# Expected: 0 violations
```

---

## AI Remediation Test (Optional)

Requires Abbenay AI daemon.

### Prerequisites

```bash
# Abbenay daemon running
# Set consumer auth token
export APME_ABBENAY_TOKEN="your-token"
```

### Test Flow

```bash
# Remediate with AI assistance
apme remediate --ai tests/fixtures/customer-test/violations-playbook.yml

# Interactive prompts:
#   y = accept this fix
#   n = reject this fix
#   a = accept all remaining
#   s = skip this file
#   q = quit

# Auto-approve mode (CI)
apme remediate --ai --auto-approve .
```

**Expected**: AI proposes fixes for Tier 2 violations; accepted fixes are validated before applying.

---

## Environment Variables

| Variable | Purpose | Example |
|----------|---------|---------|
| `APME_PRIMARY_ADDRESS` | Override Primary gRPC address | `localhost:50051` |
| `APME_ABBENAY_ADDR` | Abbenay AI daemon address | `localhost:50057` |
| `APME_ABBENAY_TOKEN` | AI consumer auth token | `your-token` |
| `NO_COLOR` | Disable ANSI colors | `1` |
| `OPA_USE_PODMAN` | Disable OPA container (use binary) | `0` |

---

## Troubleshooting

### "Connection refused" errors

```bash
# Check if daemon is running
apme daemon status

# Start daemon
apme daemon start

# Or use explicit address
APME_PRIMARY_ADDRESS=localhost:50051 apme check .
```

### OPA binary not found

```bash
# Install OPA binary
curl -fsSL -o /usr/local/bin/opa \
  https://openpolicyagent.org/downloads/latest/opa_linux_amd64_static
chmod +x /usr/local/bin/opa

# Disable Podman mode
export OPA_USE_PODMAN=0
```

### Slow scans

```bash
# Skip dependency scanning
apme check --skip-dep-scan .

# Check what's slow
apme check -vv . 2>&1 | grep "slowest"
```

---

## Feedback

After testing, please provide feedback:

1. **Bugs**: File issues at https://github.com/ansible/apme/issues
2. **False positives**: Note the rule ID and example content
3. **Missing rules**: What should APME detect that it doesn't?
4. **UX issues**: Confusing output, unclear messages
5. **Performance**: Scan times, memory usage

Include the APME version (`apme --version`) and ansible-core version in reports.

---

## Test Fixtures Reference

| File | Expected Violations |
|------|---------------------|
| `clean-playbook.yml` | 0 (reference) |
| `violations-playbook.yml` | L003, L026, M009, R108 |
| `secrets-test.yml` | SEC:* (API keys, passwords) |
| `deprecated-modules.yml` | M001, M009 |
| `messy-formatting.yml` | Formatting issues (L040) |
| `vault-reference.yml` | 0 (Jinja2 filtering test) |

Location: `tests/fixtures/customer-test/`

---

## Rule Categories

APME includes **156 rules** across 4 validators:

| Prefix | Category | Examples |
|--------|----------|----------|
| **L** | Lint | L003 (play name), L026 (FQCN), L040 (tabs) |
| **M** | Modernize | M001 (FQCN resolution), M009 (with_items→loop) |
| **R** | Risk | R108 (privilege escalation), R118 (external download) |
| **A** | AAP-specific | A001 (named_url), A002 (deprecated API) |
| **SEC** | Secrets | Hardcoded credentials, API keys |

Use `--ansible-version` to target specific ansible-core versions for modernization rules.
