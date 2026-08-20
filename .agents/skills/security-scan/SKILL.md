---
name: security-scan
description: >
  Scan project dependencies and CI workflows for known vulnerable packages.
  Use when checking for security issues, "scan for vulnerabilities", "check
  for compromised packages", or after a security advisory is published.
  Extensible via references/vulnerable-packages.md.
argument-hint: "[--update]"
user-invocable: true
metadata:
  author: APME Team
  version: 1.0.0
---

# Security Scan

Scan this project for known vulnerable or compromised packages. The scan
checks dependencies, CI workflows, and installed packages against a
maintained list of security advisories.

## Usage

```
/security-scan           # Run full scan
/security-scan --update  # Fetch latest advisories, then scan
```

## What It Scans

### GitHub Actions (CI/CD Supply Chain)

Searches `.github/workflows/*.yml` and `*.yaml` for:
- Vulnerable action versions (e.g., `aquasecurity/trivy-action < 0.35.0`)
- Actions with known security issues (e.g., `checkmarx/kics-github-action`)

### Python Dependencies

Searches `requirements*.txt`, `pyproject.toml`, `setup.py` for:
- Packages with known vulnerabilities (e.g., `litellm` specific versions)

### npm Dependencies

Searches `package.json`, `package-lock.json`, `yarn.lock`, and `node_modules/`
for:
- Compromised packages (e.g., Canister worm malware)
- Packages with known supply chain attacks

### Go Modules

Searches `go.mod`, `go.sum` for:
- Vulnerable module versions

### Other Locations

Also checks:
- `Dockerfile*` for vulnerable base images or tool installations
- `Makefile` for vulnerable tool invocations
- Shell scripts (`*.sh`) for vulnerable tool usage

## Scan Procedure

1. Load vulnerable packages from `references/vulnerable-packages.md`
2. For each category, search relevant files using grep patterns
3. Report findings with file path, line number, and remediation guidance
4. Exit with non-zero status if vulnerabilities found

### Search Commands

Use these grep patterns (adapt paths as needed):

```bash
# GitHub Actions - Trivy
grep -rn "aquasecurity/setup-trivy\|aquasecurity/trivy-action" .github/workflows/

# GitHub Actions - Checkmarx
grep -rn "checkmarx/kics-github-action\|checkmarx/ast-github-action" .github/workflows/

# Python - LiteLLM
grep -rn "litellm" requirements*.txt pyproject.toml setup.py 2>/dev/null

# Go - Trivy module
grep -rn "github.com/aquasecurity/trivy" go.mod go.sum 2>/dev/null

# npm - compromised packages (use patterns from vulnerable-packages.md)
grep -rn "@emilgroup/\|@opengov/\|@pypestream/" package*.json yarn.lock 2>/dev/null

# npm - undici 8.x advisories (semver-aware manifest + lockfile scan)
python scripts/security_scan_npm.py frontend/package.json
```

## Output Format

Report findings as a markdown table:

| Severity | Package | Location | Remediation |
|----------|---------|----------|-------------|
| CRITICAL | @emilgroup/sdk | package.json:15 | Remove immediately, audit for exfiltration |
| HIGH | trivy-action@0.30.0 | .github/workflows/ci.yml:42 | Upgrade to >= 0.35.0 |

## Adding New Vulnerabilities

When a new security advisory is published:

1. Edit `references/vulnerable-packages.md`
2. Add the package under the appropriate category
3. Include: package name, affected versions, severity, source URL
4. Test the scan locally

Example entry:
```markdown
### new-vuln-package
- **Affected**: < 2.0.0
- **Severity**: HIGH
- **Source**: https://example.com/advisory
- **Pattern**: `new-vuln-package`
```

## Current Advisories

See `references/vulnerable-packages.md` for the full list of tracked
vulnerabilities. Last updated: 2026-03-25.

Categories tracked:
- Trivy GitHub Actions and Go module
- LiteLLM Python package
- Checkmarx GitHub Actions
- Canister worm npm packages (66 packages)

## References

- [JFrog Canister Worm Research](https://research.jfrog.com/post/canister-worm/)
- [GitHub Security Advisories](https://github.com/advisories)
- [OSV Database](https://osv.dev/)
