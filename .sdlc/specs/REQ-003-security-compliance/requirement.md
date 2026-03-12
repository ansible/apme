# REQ-003: Security & Compliance

## Metadata

- **Phase**: PHASE-003 - Enterprise Dashboard
- **Status**: Draft
- **Created**: 2026-03-12

## Overview

Security scanning capabilities including secret detection, SBOM generation, and custom policy enforcement.

## User Stories

**As an Automation Architect**, I want to create custom rules so that I can enforce organizational standards.

**As a Security Engineer**, I want secrets detected so that hardcoded credentials are flagged before deployment.

**As a Compliance Officer**, I want SBOM reports so that I have visibility into all collections and modules in use.

## Acceptance Criteria

### Secret Detection
- [ ] GIVEN a playbook with hardcoded passwords or keys
- [ ] WHEN scanned
- [ ] THEN secrets are flagged with remediation guidance (Vault, env vars)

### SBOM Generation
- [ ] GIVEN an enterprise codebase
- [ ] WHEN SBOM requested
- [ ] THEN a complete list of collections, modules, and versions is generated

### Custom Policy Enforcement
- [ ] GIVEN a custom rule (e.g., "prohibit shell where command suffices")
- [ ] WHEN playbooks are scanned
- [ ] THEN violations of custom rules are reported

## Dependencies

- REQ-001: Core Scanning Engine
- Gitleaks integration (ADR-010)
- OPA/Rego policies (ADR-002)

## Notes

Policy engine allows Architects to define organization-specific rules.
