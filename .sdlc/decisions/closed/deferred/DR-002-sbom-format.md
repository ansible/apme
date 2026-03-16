# DR-002: SBOM Format and Scope

## Status

Open

## Raised By

Team Review — 2026-03-11

## Category

Technical

## Priority

Medium

---

## Question

What SBOM format should we generate, and what scope should it cover?

The PRD says "Generate an SBOM listing every collection, module, and version" but doesn't specify:
- Format (CycloneDX vs SPDX)
- Scope (just Ansible content? Python dependencies? Transitive deps?)
- CVE lookup (just inventory or vulnerability correlation?)

## Context

SBOM (Software Bill of Materials) requirements are increasingly mandated by enterprises and government contracts. Different formats have different ecosystem support:

- **CycloneDX**: Better for vulnerability tracking, native CVE correlation, lighter weight
- **SPDX**: ISO standard, better for license compliance, more verbose

Spotter does CVE lookup against their vulnerability database. Our PRD doesn't specify if we do the same.

## Impact of Not Deciding

- Cannot implement SBOM generation feature
- No clarity on dashboard requirements for vulnerability display
- Integration with enterprise vulnerability scanners undefined

---

## Options Considered

### Option A: CycloneDX Inventory Only

**Description**: Generate CycloneDX 1.5 JSON with:
- Collections (name, version, source)
- Roles (name, version, source)
- Python dependencies from requirements.txt (if present)

No CVE lookup — let users feed SBOM into their existing vuln scanners (Snyk, Grype, etc.).

**Pros**:
- Standard format, wide tooling support
- No vulnerability database to maintain
- Users can use their preferred CVE source

**Cons**:
- Less "batteries included"
- Dashboard can't show vulnerabilities natively

**Effort**: Low

### Option B: CycloneDX with CVE Correlation

**Description**: Same as A, but POST to OSV (Open Source Vulnerabilities) or similar free API for CVE lookup.

**Pros**:
- Shows vulnerabilities in dashboard
- More actionable output
- Competitive with Spotter

**Cons**:
- External API dependency
- OSV coverage for Ansible collections may be limited
- Rate limits on free tier

**Effort**: Medium

### Option C: SPDX for Compliance Focus

**Description**: Generate SPDX 2.3 with full license information for collections and dependencies.

**Pros**:
- ISO standard
- Better for license compliance requirements
- Works with enterprise compliance tools

**Cons**:
- More verbose output
- Worse CVE tooling ecosystem
- Most users care more about vulns than licenses

**Effort**: Medium

### Option D: Both Formats, User Choice

**Description**: Support `--sbom-format cyclonedx|spdx` flag. Generate whichever the user needs.

**Pros**:
- Maximum flexibility
- Covers all use cases

**Cons**:
- Double the maintenance
- Testing burden

**Effort**: High

---

## Recommendation

**Option A** (CycloneDX inventory only) for v1. It's the most pragmatic:
- CycloneDX has better vuln tooling ecosystem
- Users can feed output to their existing scanners
- No external API dependency
- Can add CVE lookup in v2 if users request it

---

## Related Artifacts

- PRD: SBOM requirement
- REQ-004: Integration (CI/CD output formats)

---

## Discussion Log

| Date | Participant | Input |
|------|-------------|-------|
| 2026-03-11 | Team | Initial question raised during PRD review |

---

## Decision

**Status**: Deferred
**Date**: 2026-03-16
**Decided By**: Team

**Decision**: Deferred — part of REQ-003 (Security & Compliance) scope

**Rationale**:
- Core scanning features prioritized for v1
- SBOM is part of REQ-003 (Security & Compliance) in PHASE-003
- Dashboard deferred (DR-003) — CVE display would need dashboard
- CycloneDX likely choice based on research, but defer final decision

**Research Notes** (for future reference):
- CycloneDX preferred for security use cases
- All major vuln scanners support CycloneDX
- Scope: Collections + Roles + Python deps (including transitive)
- CVE correlation via OSV API is viable for v2

**Revisit**: When security/compliance features are prioritized (REQ-003)

**Action Items**:
- [ ] Re-open when REQ-003 is in scope
- [ ] Validate CycloneDX recommendation with enterprise users
- [ ] Evaluate OSV API for CVE correlation
