# Vulnerable Packages Registry

Last updated: 2026-08-18

This file contains known vulnerable packages to scan for. Add new entries
as security advisories are published.

---

## GitHub Actions

### Trivy Actions

- **Packages**:
  - `aquasecurity/setup-trivy`
  - `aquasecurity/trivy-action`
- **Affected**: setup-trivy < 0.2.6, trivy-action < 0.35.0
- **Severity**: HIGH
- **Source**: Internal Security Advisory (2026-03)
- **Pattern**: `aquasecurity/setup-trivy|aquasecurity/trivy-action`
- **Remediation**: Upgrade to setup-trivy >= 0.2.6, trivy-action >= 0.35.0

### Checkmarx Actions

- **Packages**:
  - `checkmarx/kics-github-action`
  - `checkmarx/ast-github-action`
- **Affected**: All versions (evaluate before use)
- **Severity**: MEDIUM
- **Source**: Internal Security Advisory (2026-03)
- **Pattern**: `checkmarx/kics-github-action|checkmarx/ast-github-action`
- **Remediation**: Review usage and pin to audited SHA

---

## Python Packages

### LiteLLM

- **Package**: `litellm`
- **Affected**: 1.82.7, 1.82.8
- **Severity**: HIGH
- **Source**: Internal Security Advisory (2026-03)
- **Pattern**: `litellm`
- **Files**: `requirements*.txt`, `pyproject.toml`, `setup.py`
- **Remediation**: Upgrade to patched version or remove

---

## Go Modules

### Trivy

- **Package**: `github.com/aquasecurity/trivy`
- **Affected**: 0.69.4
- **Severity**: HIGH
- **Source**: Internal Security Advisory (2026-03)
- **Pattern**: `github.com/aquasecurity/trivy`
- **Files**: `go.mod`, `go.sum`
- **Remediation**: Upgrade to patched version

---

## npm Packages (undici)

### undici (8.x cache / CRLF advisories)

- **Package**: `undici`
- **Affected**: `>= 8.0.0, < 8.9.0` (also avoid 8.x with jsdom — breaks `undici/lib/handler/wrap-handler.js`)
- **Severity**: HIGH (cross-user cache disclosure); additional MEDIUM (CRLF, cookie injection, desync)
- **Source**: [GitHub Advisory Database](https://github.com/advisories) / Dependabot
- **Pattern**: `"undici":\s*"(?:\^|~)?8\.[0-8]\.` (grep quick-check; misses some range forms)
- **Semver scan**: `python scripts/security_scan_npm.py frontend/package.json` (manifest ranges + lockfile resolved versions)
- **Fixtures**: `tests/fixtures/security_scan_npm/` (exact, caret, tilde, lockfile)
- **Files**: `frontend/package.json`, `frontend/package-lock.json`
- **Remediation**: Pin `undici` to `7.29.0` (jsdom-compatible) or `>=8.9.0` only if jsdom is upgraded to support undici 8.x

---

## npm Packages (Canister Worm)

Source: [JFrog Research](https://research.jfrog.com/post/canister-worm/)

These packages were compromised as part of the Canister worm supply chain
attack. Any installation should be treated as a security incident.

- **Severity**: CRITICAL
- **Remediation**: Remove immediately, audit for data exfiltration, rotate credentials

### @pypestream scope
- `@pypestream/floating-ui-dom`

### @leafnoise scope
- `@leafnoise/mirage`

### @opengov scope
- `@opengov/ppf-backend-types`
- `@opengov/form-renderer`
- `@opengov/qa-record-types-api`
- `@opengov/form-builder`
- `@opengov/ppf-eslint-config`
- `@opengov/form-utils`

### @virtahealth scope
- `@virtahealth/substrate-root`

### @airtm scope
- `@airtm/uuid-base32`

### @teale.io scope
- `@teale.io/eslint-config`

### @emilgroup scope
- `@emilgroup/setting-sdk`
- `@emilgroup/partner-portal-sdk`
- `@emilgroup/gdv-sdk-node`
- `@emilgroup/docxtemplater-util`
- `@emilgroup/accounting-sdk`
- `@emilgroup/task-sdk`
- `@emilgroup/setting-sdk-node`
- `@emilgroup/task-sdk-node`
- `@emilgroup/partner-sdk`
- `@emilgroup/numbergenerator-sdk-node`
- `@emilgroup/customer-sdk`
- `@emilgroup/commission-sdk`
- `@emilgroup/process-manager-sdk`
- `@emilgroup/changelog-sdk-node`
- `@emilgroup/document-sdk-node`
- `@emilgroup/commission-sdk-node`
- `@emilgroup/document-uploader`
- `@emilgroup/discount-sdk`
- `@emilgroup/discount-sdk-node`
- `@emilgroup/insurance-sdk`
- `@emilgroup/account-sdk`
- `@emilgroup/account-sdk-node`
- `@emilgroup/accounting-sdk-node`
- `@emilgroup/api-documentation`
- `@emilgroup/auth-sdk`
- `@emilgroup/auth-sdk-node`
- `@emilgroup/billing-sdk`
- `@emilgroup/billing-sdk-node`
- `@emilgroup/claim-sdk`
- `@emilgroup/claim-sdk-node`
- `@emilgroup/customer-sdk-node`
- `@emilgroup/document-sdk`
- `@emilgroup/gdv-sdk`
- `@emilgroup/insurance-sdk-node`
- `@emilgroup/notification-sdk-node`
- `@emilgroup/partner-portal-sdk-node`
- `@emilgroup/partner-sdk-node`
- `@emilgroup/payment-sdk`
- `@emilgroup/payment-sdk-node`
- `@emilgroup/process-manager-sdk-node`
- `@emilgroup/public-api-sdk`
- `@emilgroup/public-api-sdk-node`
- `@emilgroup/tenant-sdk`
- `@emilgroup/tenant-sdk-node`
- `@emilgroup/translation-sdk-node`

### Unscoped packages
- `eslint-config-ppf`
- `react-leaflet-marker-layer`
- `react-leaflet-cluster-layer`
- `react-autolink-text`
- `opengov-k6-core`
- `jest-preset-ppf`
- `cit-playwright-tests`
- `eslint-config-service-users`
- `babel-plugin-react-pure-component`
- `react-leaflet-heatmap-layer`

---

## Grep Patterns (for automated scanning)

```bash
# GitHub Actions
TRIVY_ACTIONS="aquasecurity/setup-trivy|aquasecurity/trivy-action"
CHECKMARX_ACTIONS="checkmarx/kics-github-action|checkmarx/ast-github-action"

# Python
PYTHON_VULNS="litellm"

# Go
GO_VULNS="github.com/aquasecurity/trivy"

# npm scopes (Canister worm)
NPM_SCOPES="@pypestream/|@leafnoise/|@opengov/|@virtahealth/|@airtm/|@teale\\.io/|@emilgroup/"

# npm unscoped (Canister worm)
NPM_UNSCOPED="eslint-config-ppf|react-leaflet-marker-layer|react-leaflet-cluster-layer|react-autolink-text|opengov-k6-core|jest-preset-ppf|cit-playwright-tests|eslint-config-service-users|babel-plugin-react-pure-component|react-leaflet-heatmap-layer"
```

---

## Adding New Vulnerabilities

When adding a new vulnerability:

1. Add under the appropriate category (or create a new one)
2. Include all required fields:
   - **Package(s)**: Name(s) of affected package(s)
   - **Affected**: Version range or specific versions
   - **Severity**: CRITICAL, HIGH, MEDIUM, LOW
   - **Source**: URL or reference to advisory
   - **Pattern**: Grep-compatible regex pattern
   - **Remediation**: Clear action to take
3. Update the grep patterns section if needed
4. Update "Last updated" date at the top
5. Test the scan locally before committing
