# REQ-002: Automated Remediation

## Metadata

- **Phase**: PHASE-002 - Rewrite Engine
- **Status**: Draft
- **Created**: 2026-03-12

## Overview

Automated "Rewrite" feature that applies safe fixes for renamed parameters, module redirects, and basic syntax changes.

## User Stories

**As a DevOps Engineer**, I want auto-fixes applied to simple issues so that I can focus on complex problems.

**As a Code Owner**, I want to see diffs before changes are committed so that I can review what will change.

**As a CI Pipeline**, I want iterative processing so that nested issues are uncovered progressively.

## Acceptance Criteria

### Safe Auto-Fixes
- [ ] GIVEN a playbook with renamed parameters
- [ ] WHEN rewrite runs
- [ ] THEN parameters are updated to current names

### Module Redirects
- [ ] GIVEN deprecated module references
- [ ] WHEN rewrite runs
- [ ] THEN modules are replaced with current equivalents

### Iterative Processing
- [ ] GIVEN a playbook with nested issues
- [ ] WHEN multiple rewrite passes run
- [ ] THEN each pass uncovers issues hidden by previous fixes

### Diff Generation
- [ ] GIVEN any rewrite operation
- [ ] WHEN changes are proposed
- [ ] THEN a before/after diff is shown for approval

## Dependencies

- REQ-001: Core Scanning Engine
- TransformRegistry (ADR-009)

## Notes

Covers 50 most common module deprecations as priority.
