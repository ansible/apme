# REQ-001: Core Scanning Engine

## Metadata

- **Phase**: PHASE-001 - CLI Scanner
- **Status**: In Progress
- **Created**: 2026-03-12

## Overview

Static analysis engine that scans Ansible content for compatibility issues, categorizes findings by severity, and identifies FQCN transformation opportunities.

## User Stories

**As a DevOps Engineer**, I want to scan my playbook against a target Ansible version so that I know what will break before upgrading.

**As an Automation Architect**, I want issues categorized by severity so that I can prioritize remediation efforts.

**As a CI Pipeline**, I want JSON/JUnit output so that I can integrate scan results into existing tooling.

## Acceptance Criteria

### Version-Specific Analysis
- [ ] GIVEN a playbook and target version (e.g., 2.16)
- [ ] WHEN the scanner runs
- [ ] THEN it identifies issues specific to that version transition

### Multi-Level Issue Categorization
- [ ] Errors: Critical issues that will cause execution failure
- [ ] Warnings: Deprecated syntax that may still run
- [ ] Hints: Best-practice recommendations

### FQCN Transformation
- [ ] GIVEN short-form module names (e.g., `apt`)
- [ ] WHEN scanned
- [ ] THEN suggests FQCN equivalents (e.g., `ansible.builtin.apt`)

### Output Formats
- [ ] JSON for automation
- [ ] JUnit for CI/CD reports
- [ ] Text for humans

## Dependencies

- Vendored ARI engine (ADR-003)
- gRPC validators (ADR-001)

## Notes

Foundation capability. All other features depend on scanning.
