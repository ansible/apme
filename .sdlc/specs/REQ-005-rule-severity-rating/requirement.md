# REQ-005: Rule Rating & Severity System

## Metadata

- **Phase**: PHASE-003 - Enterprise Dashboard
- **Status**: Draft
- **Created**: 2026-03-24

## Overview

A configurable rule rating system that assigns severity scores to scanning rules, enabling teams to prioritize remediation by impact. Severity ratings drive violation ordering, filtering, and threshold-based gating in CI/CD pipelines and the UI.

## User Stories

**As an Automation Architect**, I want to assign custom severity ratings to rules so that my team focuses on the violations that matter most to our environment.

**As a DevOps Engineer**, I want violations sorted and filtered by severity so that I can triage quickly without reading every finding.

**As a CI Pipeline Operator**, I want to set severity thresholds (e.g., "fail on Critical/High") so that only impactful violations block deployments.

**As a Platform Admin**, I want default severity ratings shipped with APME so that teams have sensible defaults out of the box.

## Acceptance Criteria

### Default Severity Assignment
- **GIVEN** a fresh APME installation
- **WHEN** a scan runs
- **THEN** every rule has a default severity rating (Critical, High, Medium, Low, Info)

### Custom Severity Override
- **GIVEN** an Automation Architect with admin privileges
- **WHEN** they override a rule's severity via the UI or configuration file
- **THEN** subsequent scans use the custom severity for that rule

### Severity-Based Ordering
- **GIVEN** a scan with mixed-severity violations
- **WHEN** results are displayed (CLI, UI, or API)
- **THEN** violations are sorted by severity (Critical → Info) by default

### Severity Threshold Gating
- **GIVEN** a CI pipeline with a threshold set to "High"
- **WHEN** a scan produces only Medium/Low/Info violations
- **THEN** the pipeline passes (exit code 0)
- **AND WHEN** a scan produces a High or Critical violation
- **THEN** the pipeline fails (exit code non-zero)

### Severity in Output Formats
- **GIVEN** any output format (JSON, JUnit, text)
- **WHEN** violations are emitted
- **THEN** each violation includes its severity rating

## Inputs / Outputs

### Inputs

| Name | Type | Description | Required |
|------|------|-------------|----------|
| rule_id | string | Rule identifier (e.g., L026, SEC:aws-access-key-id) | Yes |
| severity | enum | Critical / High / Medium / Low / Info | Yes |
| scope | string | Global, per-project, or per-profile | No |

### Outputs

| Name | Type | Description |
|------|------|-------------|
| rated_violations | list[Violation] | Violations annotated with severity |
| severity_summary | dict | Count of violations per severity level |

## Behavior

### Happy Path

1. APME ships a default severity map covering all built-in rules (L*, M*, R*, P*, SEC:*)
2. Admin overrides specific severities via UI or `.apme/severity.yml` config file
3. Scanner enriches each violation with its resolved severity (custom > project > default)
4. CLI and UI present violations grouped/sorted by severity
5. CI threshold check compares max severity against configured threshold

### Severity Resolution Order

```
Custom (per-project .apme/severity.yml)
  → Profile (named severity profile)
    → Default (built-in APME defaults)
```

### Edge Cases

| Case | Handling |
|------|----------|
| Unknown rule ID | Falls back to "Medium" severity with warning |
| Rule added by plugin | Plugin declares default severity; overridable |
| Conflicting overrides | Per-project wins over profile; profile wins over default |
| Empty severity config | All defaults applied; no error |

### Error Conditions

| Error | Cause | Response |
|-------|-------|----------|
| Invalid severity value | Typo in config (e.g., "Crtical") | Config validation error with suggestion |
| Invalid rule ID in override | Rule doesn't exist | Warning logged; override ignored |

## Dependencies

### Internal

- REQ-001: Core Scanning Engine (violations to rate)
- REQ-003: Security & Compliance (SEC rules need severity)

### External

- None (severity is metadata layered on existing violations)

## Non-Functional Requirements

- **Performance**: Severity lookup must add <1ms per violation (O(1) map lookup)
- **Compatibility**: Severity config format must be forward-compatible (versioned schema)
- **Security**: Only admin/architect roles can modify severity ratings

## Open Questions

- [ ] Should severity profiles be shareable across projects (e.g., "PCI-DSS profile")?
- [ ] Should severity ratings influence remediation priority in REQ-002?
- [ ] Do we need a "Disabled" severity that suppresses the rule entirely (overlap with REQ-007)?

## References

- ADR-008: Rule ID Conventions (L/M/R/P/SEC)
- REQ-001: Core Scanning Engine
- REQ-003: Security & Compliance

---

## Change History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-24 | APME Team | Initial draft |
