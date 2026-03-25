# DR-013: Automation Analytics Integration Approach

## Status

Open

## Raised By

Claude (from AAPRFE-1607 analysis) — 2026-03-25

## Category

Architecture

## Priority

High

---

## Question

How should APME feed deprecated module detection data into Automation Analytics for reporting?

## Context

Customer RFE [AAPRFE-1607](https://redhat.atlassian.net/browse/AAPRFE-1607) requests deprecated module reports in Automation Analytics. The data already exists in job logs ("this module is deprecated, please upgrade it"), but requires structured collection and reporting.

APME already detects deprecated modules via:
- **L004**: Deprecated module usage
- **M001-M004**: Modernization rules for outdated patterns

The question is how to get this data from APME into Automation Analytics.

## Impact of Not Deciding

- Cannot proceed with REQ-011 implementation
- Customer use case (AAPRFE-1607) remains unaddressed
- Telco customers cannot plan ansible-core upgrades effectively

---

## Options Considered

### Option A: AAP Event-Driven Integration

**Description**: APME runs as part of AAP job execution (pre-flight or execution environment). Results flow through AAP's existing telemetry to Automation Analytics.

**Pros**:
- Uses existing AAP → AA data pipeline
- Job context (job ID, template, inventory) automatically available
- Minimal new infrastructure

**Cons**:
- Requires AAP integration work (EE, callback plugin, or pre-flight hook)
- Dependent on AAP release cycle
- May require changes to AA schema

**Effort**: High

### Option B: Direct AA API Integration

**Description**: APME sends scan results directly to Automation Analytics via API.

**Pros**:
- Decoupled from AAP release cycle
- Can work for standalone scanning (CI/CD, local)
- Full control over data format

**Cons**:
- Requires AA API access and schema changes
- Need to correlate with AAP job metadata separately
- Additional authentication/authorization flow

**Effort**: Medium

### Option C: Insights Client Extension

**Description**: Extend the Insights client (already on AAP) to collect APME scan results and ship to AA.

**Pros**:
- Leverages existing Insights infrastructure
- Already handles authentication and transport
- Familiar pattern for RHEL/AAP customers

**Cons**:
- Requires Insights client changes
- May have data freshness limitations (batch upload)
- Additional dependency

**Effort**: Medium

### Option D: Export-Only (No Direct Integration)

**Description**: APME generates structured reports (JSON/SARIF) that customers manually import or feed into their own pipelines to AA.

**Pros**:
- No external dependencies
- Works today with current APME capabilities
- Customer controls the integration

**Cons**:
- No automatic flow to AA
- Requires customer implementation effort
- Doesn't fully address RFE request

**Effort**: Low

### Option E: Do Nothing / Defer

**Description**: Leave undefined for now, revisit after v1 CLI stabilizes.

**Pros**:
- Focus on core scanning functionality
- Avoid premature integration decisions
- AA roadmap may clarify integration patterns (2H 2026 per Jira comments)

**Cons**:
- Customer RFE remains unaddressed
- May miss alignment with AA roadmap work

---

## Recommendation

**Option D (Export-Only) for v1, with Option A planning for v2.**

Rationale:
1. v1 focus is CLI scanning - export capability exists (JSON, SARIF)
2. Jira comments indicate AA reporting capabilities coming in 2H 2026
3. Option A (event-driven) aligns best long-term but requires AAP integration
4. We should coordinate with AA team on schema before committing

---

## Related Artifacts

- [REQ-011](../../specs/REQ-011-aa-deprecated-reporting/requirement.md): Automation Analytics Deprecated Module Reporting
- [DR-004](../closed/deferred/DR-004-aap-integration.md): AAP Pre-Flight Integration (deferred)
- [REQ-004](../../specs/REQ-004-enterprise-integration/requirement.md): Enterprise Integration
- [AAPRFE-1607](https://redhat.atlassian.net/browse/AAPRFE-1607): Original customer RFE

---

## Discussion Log

| Date | Participant | Input |
|------|-------------|-------|
| 2026-03-25 | Claude | Initial DR created from AAPRFE-1607 analysis |
| | | AA team comment on Jira suggests 2H 2026 reporting roadmap |

---

## Decision

**Status**: Open
**Date**:
**Decided By**:

**Decision**:

**Rationale**:

**Action Items**:
- [ ] Coordinate with AA team on reporting roadmap
- [ ] Review AA API documentation for integration patterns
- [ ] Determine if pre-flight or post-run scanning is preferred

---

## Post-Decision Updates

| Date | Update |
|------|--------|
