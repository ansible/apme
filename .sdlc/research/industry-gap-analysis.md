# Industry Alignment and Gap Analysis

**Status**: Current
**Date**: 2026-03-31
**Author**: Vinny Valdez (@vvaldez)

## Overview

This analysis compares APME's current practices against four industry frameworks:
- **NIST SSDF v1.2** (SP 800-218) — Secure Software Development Framework
- **OpenSSF OSPS Baseline** (2026-02-19) — Open Source Project Security controls
- **SLSA** — Supply Chain Levels for Software Artifacts
- **GitHub Hardening Guides** — Platform-specific security configuration

## Current Alignment (what APME already does well)

| Practice | APME Implementation | Framework |
|----------|-------------------|-----------|
| Secret scanning pre-commit | gitleaks, detect-secrets, bandit, detect-private-key | NIST SSDF PW, OpenSSF L1 |
| Static analysis in CI | Ruff (lint + format), mypy strict, pydoclint via tox/prek | NIST SSDF PW |
| Vulnerability reporting process | SECURITY.md with private disclosure, response SLAs | OpenSSF L1 |
| Dependency governance | ADR-019 two-tier model with 7-question checklist | NIST SSDF PS |
| Container hardening | Non-root, pinned tags, no secrets in ENV, image scanning | OpenSSF L2 |
| CI as thin wrapper | Actions pin to SHAs, local-reproducible tox environments (ADR-015, ADR-047) | SLSA Build L1, OpenSSF SCM |
| Spec-driven development | REQ → TASK → code with traceability | NIST SSDF PO |
| Incident response documented | SECURITY.md "Incident Response" section | NIST SSDF RV |
| License declared | Apache 2.0, documented in CONTRIBUTING.md | OpenSSF L1 |
| Structured logging with redaction | structlog, `[REDACTED]` for secrets | NIST SSDF PW |

## High-Priority Gaps (Security)

These gaps are present in industry standards and are especially important for a public repository.

### CODEOWNERS File

**Gap:** No `CODEOWNERS` file to enforce review by domain experts on critical paths.

**Industry reference:** OpenSSF Baseline L2, GitHub Hardening Guide.

**Recommendation:** Create `.github/CODEOWNERS` mapping critical paths to required reviewers:
```
# Security-sensitive paths
/SECURITY.md                    @security-team
/.github/                       @maintainers
/containers/                    @maintainers
/proto/                         @maintainers

# Core engine
/src/apme_engine/engine/        @engine-team
/src/apme_engine/daemon/        @engine-team

# Validators
/src/apme_engine/validators/    @validator-team
```

### Signed Commits

**Gap:** No requirement or documentation for GPG/SSH commit signing.

**Industry reference:** OpenSSF Baseline L2, SLSA Source L2.

**Recommendation:** Document commit signing setup in `CONTRIBUTING.md`. Consider requiring verified signatures via branch protection rules. At minimum, maintainer commits should be signed.

### Automated Dependency Updates

**Gap:** No Dependabot or Renovate configuration for automated dependency update PRs.

**Industry reference:** OpenSSF Baseline L2, NIST SSDF RV.

**Recommendation:** Add `.github/dependabot.yml`:
```yaml
version: 2
updates:
  - package-ecosystem: pip
    directory: "/"
    schedule:
      interval: weekly
  - package-ecosystem: github-actions
    directory: "/"
    schedule:
      interval: weekly
```

### SBOM Generation

**Gap:** Partially addressed. The Gateway now exposes a `GET /projects/{id}/sbom` endpoint that returns CycloneDX 1.5 JSON for scanned projects (collections and Python packages with license/supplier data). However, release-time SBOM generation for the APME tool itself (Python package and container images) is not yet automated. DR-002 (SBOM format) is deferred.

**Industry reference:** NIST SSDF PS, OpenSSF Baseline L3, Executive Order 14028.

**Recommendation:** Add `syft` or `cyclonedx-bom` to the release pipeline to generate SBOMs for APME's own artifacts (Python wheel and container images), complementing the existing per-project SBOM endpoint.

### Artifact Signing

**Gap:** No container image or release artifact signing.

**Industry reference:** SLSA Build L2, Sigstore ecosystem.

**Recommendation:** Sign published container images with `cosign` (Sigstore). Include provenance attestations for builds produced in CI.

### CodeQL / SAST in CI

**Gap:** No CodeQL or equivalent deep static analysis beyond ruff and bandit pre-commit.

**Industry reference:** NIST SSDF PW, OpenSSF Scorecard.

**Recommendation:** Add `.github/workflows/codeql-analysis.yml` with Python language support. CodeQL catches vulnerability patterns (injection, path traversal, deserialization) that ruff and bandit may miss.

### Branch Protection Documentation

**Gap:** Branch protection rules are not documented as project policy.

**Industry reference:** GitHub Hardening Guide, OpenSSF SCM Best Practices.

**Recommendation:** Document the following as required branch protection settings for `main`:
- Require pull request reviews (at least 1 approver)
- Require status checks to pass (prek, tests)
- Require conversation resolution before merge
- Prevent force pushes and deletions
- Require linear commit history

### GitHub Security Tab Configuration

**Gap:** It is unclear whether `SECURITY.md` is linked via GitHub's Security tab.

**Industry reference:** OpenSSF Baseline L1.

**Recommendation:** Verify via Settings → Code security and analysis → Security policy that `SECURITY.md` is detected and shown in the Security tab.

## Medium-Priority Gaps (Process)

### Threat Modeling in Requirements

**Gap:** No explicit threat modeling step for security-sensitive features.

**Industry reference:** NIST SSDF PW.1 ("Design software to meet security requirements and mitigate security risks").

**Recommendation:** Add a lightweight "Security Considerations" section to the REQ template (`.sdlc/templates/requirement.md`):
```markdown
## Security Considerations
- [ ] Threat model: what can go wrong?
- [ ] Trust boundaries: what input is untrusted?
- [ ] Data sensitivity: what data is handled?
- [ ] Attack surface: what new endpoints/interfaces are exposed?
```

### Post-Incident Review Template

**Gap:** No post-mortem / retrospective template.

**Industry reference:** NIST SSDF RV.3 ("Analyze vulnerabilities to identify their root causes").

**Recommendation:** Add `.sdlc/templates/postmortem.md` covering: timeline, root cause, impact, remediation, prevention measures, and action items.

### Developer Certificate of Origin

**Gap:** No explicit DCO or CLA requirement beyond the license statement.

**Industry reference:** OpenSSF Baseline L2.

**Recommendation:** The Apache 2.0 license is already chosen (DR-009) and `CONTRIBUTING.md` states contributions are licensed under it. Consider adding a DCO sign-off requirement (`Signed-off-by:` trailer) for stronger contributor attribution.

### Inconsistent Tooling Documentation

**Gap:** `CONTRIBUTING.md` references `pre-commit install` while `docs/DEVELOPMENT.md` and `AGENTS.md` reference `prek install`. Both exist but the canonical tool is `prek`.

**Recommendation:** Align `CONTRIBUTING.md` to reference `prek` as the canonical pre-commit tool, with `pre-commit` mentioned only as the underlying mechanism.

### Incomplete Skills Table in AGENTS.md

**Gap:** Three skills (`branch-align`, `rfe-capture`, `security-scan`) exist under `.agents/skills/` but are not listed in the AGENTS.md Project Skills table.

**Recommendation:** Add the missing entries to the table in `AGENTS.md`.

## Summary Matrix

| Gap | Priority | Effort | Framework |
|-----|----------|--------|-----------|
| CODEOWNERS | High | Low | OpenSSF L2 |
| Signed commits | High | Low | OpenSSF L2, SLSA |
| Dependabot config | High | Low | OpenSSF L2, NIST |
| SBOM generation (release artifacts) | High | Medium | NIST, OpenSSF L3 |
| Artifact signing | High | Medium | SLSA L2 |
| CodeQL in CI | High | Low | NIST, OpenSSF |
| Branch protection docs | High | Low | GitHub Hardening |
| Security tab config | High | Low | OpenSSF L1 |
| Threat modeling in REQ | Medium | Low | NIST PW.1 |
| Post-incident template | Medium | Low | NIST RV.3 |
| DCO sign-off | Medium | Low | OpenSSF L2 |
| Tooling docs alignment | Medium | Low | Internal |
| Skills table update | Medium | Low | Internal |

## Related

- [SECURITY.md](../../SECURITY.md)
- [CONTRIBUTING.md](../../CONTRIBUTING.md)
- [ADR-019: Dependency Governance](../adrs/ADR-019-dependency-governance.md)
- [SOP.md](../../SOP.md) — links to this analysis
