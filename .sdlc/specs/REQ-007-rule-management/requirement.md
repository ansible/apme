# REQ-007: Rule Management & Issue Acknowledgment

## Metadata

- **Phase**: PHASE-003 - Enterprise Dashboard
- **Status**: Draft
- **Created**: 2026-03-24

## Overview

UI and configuration-driven rule management allowing users to enable, disable, or add custom rules. Includes a code-level acknowledgment mechanism (inline annotations) that tells the scanner to skip specific violations, similar to `# noqa` in linting tools.

## User Stories

**As an Automation Architect**, I want to enable/disable rules from the UI so that I can tailor the scanning profile to my organization's standards without editing config files.

**As a DevOps Engineer**, I want to acknowledge a specific violation in my code so that known-acceptable patterns don't clutter scan results.

**As a Platform Admin**, I want to add custom rules via the UI so that I can enforce organization-specific policies without writing code.

**As a Team Lead**, I want to see which violations have been acknowledged and by whom so that I can audit suppression decisions.

## Acceptance Criteria

### Enable/Disable Rules via UI
- **GIVEN** an admin user in the rule management UI
- **WHEN** they toggle a rule to "disabled"
- **THEN** subsequent scans skip that rule entirely
- **AND** the change is recorded with who/when/why

### Enable/Disable Rules via Config
- **GIVEN** a `.apme/rules.yml` configuration file
- **WHEN** it contains `disabled: [L026, M005]`
- **THEN** those rules are skipped during scanning

### Inline Issue Acknowledgment
- **GIVEN** a playbook file with `# apme:ignore L026` on a line or block
- **WHEN** the scanner encounters a violation on that line/block
- **THEN** the violation is suppressed from results
- **AND** it appears in a separate "acknowledged" section (not hidden entirely)

### Block-Level Acknowledgment
- **GIVEN** a task block prefixed with `# apme:ignore-begin L026` and `# apme:ignore-end`
- **WHEN** violations of L026 occur within that block
- **THEN** all matching violations are acknowledged

### Acknowledgment with Reason
- **GIVEN** an inline annotation `# apme:ignore L026 reason="Legacy module required for RHEL7"`
- **WHEN** the scanner processes it
- **THEN** the reason is captured and displayed in the acknowledged violations list

### Acknowledgment Audit Trail
- **GIVEN** a scan with acknowledged violations
- **WHEN** results are viewed in the UI
- **THEN** a separate tab/section shows all acknowledged items with rule, file, line, reason, and author (from git blame)

### Custom Rule Addition via UI
- **GIVEN** an admin user in the rule management UI
- **WHEN** they create a new custom rule (name, pattern, severity, category)
- **THEN** the rule is available in subsequent scans
- **AND** the rule follows ADR-008 naming conventions (prefix based on category)

## Inputs / Outputs

### Inputs

| Name | Type | Description | Required |
|------|------|-------------|----------|
| rule_id | string | Rule identifier to manage | Yes |
| action | enum | enable, disable, acknowledge | Yes |
| scope | string | global, project, file, line | Yes |
| reason | string | Justification for acknowledgment/disable | Recommended |
| custom_rule | object | Definition for new custom rules | If adding |

### Outputs

| Name | Type | Description |
|------|------|-------------|
| active_rules | list[Rule] | Currently enabled rules with metadata |
| acknowledged_violations | list[AcknowledgedViolation] | Suppressed violations with reasons |
| rule_change_audit | list[AuditEntry] | History of rule state changes |

## Behavior

### Happy Path — Rule Toggle

1. Admin opens rule management UI
2. Views all rules organized by category (L/M/R/P/SEC)
3. Toggles rule state (enabled/disabled)
4. Optionally provides a reason
5. Change persists to project configuration
6. Next scan respects the new rule state

### Happy Path — Inline Acknowledgment

1. Developer encounters a violation they want to suppress
2. Adds `# apme:ignore <RULE_ID>` comment to the relevant line
3. Optionally adds `reason="..."` for audit trail
4. Next scan marks the violation as acknowledged, not active
5. UI shows acknowledged count separately from active violations

### Acknowledgment Syntax

```yaml
# Single line
- name: Use legacy module  # apme:ignore L026 reason="Required for RHEL7"
  apt:
    name: httpd

# Block scope
# apme:ignore-begin L026,M005 reason="Legacy block pending migration"
- name: Task 1
  apt: name=foo
- name: Task 2
  apt: name=bar
# apme:ignore-end

# All rules on a line
- name: Complex task  # apme:ignore-all reason="Reviewed and approved"
```

### Edge Cases

| Case | Handling |
|------|----------|
| Acknowledge non-existent rule | Warning: "Rule X not found; acknowledgment ignored" |
| Disable all rules | Prevented; at least one rule must remain enabled |
| Custom rule ID conflicts with built-in | Rejected; custom rules must use `CUSTOM:` prefix |
| Nested ignore-begin/end blocks | Not supported; inner block closes the outer |
| Ignore annotation with no matching violation | Info: "No violations to acknowledge on this line" |

### Error Conditions

| Error | Cause | Response |
|-------|-------|----------|
| Invalid rule ID in annotation | Typo in inline comment | Warning in scan output; annotation ignored |
| Malformed ignore syntax | Missing rule ID | Parse warning; line treated as normal comment |
| Permission denied | Non-admin tries to disable global rule | 403 error with message |

## Dependencies

### Internal

- REQ-001: Core Scanning Engine (rule execution, violation detection)
- REQ-005: Rule Rating & Severity (severity assignment for custom rules)

### External

- Git (for blame-based author attribution on acknowledgments)

## Non-Functional Requirements

- **Performance**: Rule state lookup must be O(1); inline annotation parsing adds <5ms per file
- **Compatibility**: Inline annotations must not break Ansible/YAML parsing (they're YAML comments)
- **Security**: Only admin/architect roles can disable rules globally or add custom rules
- **Auditability**: All rule state changes and acknowledgments are logged with timestamp, user, reason

## Open Questions

- [ ] Should acknowledgments expire after a configurable period (e.g., 90 days)?
- [ ] Should we support regex-based custom rules or only pattern matching?
- [ ] How do custom rules integrate with OPA/Rego policies (overlap with REQ-003)?
- [ ] Should acknowledged violations count toward project health score or be excluded?

## References

- ADR-008: Rule ID Conventions
- ADR-009: Remediation Engine (validators read-only constraint)
- REQ-001: Core Scanning Engine
- REQ-005: Rule Rating & Severity

---

## Change History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-24 | APME Team | Initial draft |
