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

**As a Compliance Officer**, I want SBOM reports so that I have visibility into collections
and Python dependencies in use (per-role/per-module fields deferred per ADR-044).

## Acceptance Criteria

### Secret Detection
- [ ] GIVEN a playbook with hardcoded passwords or keys
- [ ] WHEN scanned
- [ ] THEN secrets are flagged with remediation guidance (Vault, env vars)

### SBOM Generation
- [x] GIVEN an enterprise codebase
- [x] WHEN SBOM requested
- [x] THEN a CycloneDX inventory of collections and Python dependencies is generated
  — **Inventory delivered** via Gateway (`apme sbom`). **CVE/CWE/VEX export** tracked in
  [REQ-020](../REQ-020-unified-supply-chain-security/requirement.md) (ADR-072). Per-role
  / per-module SBOM fields remain deferred (ADR-044).

### Custom Policy Enforcement
- [ ] GIVEN a custom rule (e.g., "prohibit shell where command suffices")
- [ ] WHEN playbooks are scanned
- [ ] THEN violations of custom rules are reported

## Dependencies

- REQ-001: Core Scanning Engine
- Gitleaks integration (ADR-010)
- OPA/Rego policies (ADR-002)
- REQ-020: Unified Supply Chain Security (SBOM export + CVE/CWE)

## Notes

Policy engine allows Architects to define organization-specific rules.

SBOM inventory already exists via Gateway (`apme sbom`). Further SBOM/CVE/CWE work
is tracked under REQ-020 rather than duplicated here.
