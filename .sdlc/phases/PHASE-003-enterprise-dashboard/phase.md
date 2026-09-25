# PHASE-003: Enterprise Dashboard

## Status

In Progress

## Overview

Enterprise Dashboard with ROI/Time-Saved reporting and Custom Policy engine. Security & compliance features.

## Goals

- Web dashboard with aggregated reporting
- ROI metrics: "Total Errors Resolved", "Hours Saved"
- Custom policy creation for Automation Architects
- Secret detection and SBOM generation
- AAP Pre-Flight integration

## Success Criteria

- [ ] Dashboard displays enterprise-wide metrics
- [ ] Custom policy engine operational
- [ ] Secret detection identifies hardcoded credentials
- [x] SBOM inventory covers collections and Python dependencies (REQ-003 via Gateway `apme sbom`; CVE/CWE/VEX enrichment tracked in REQ-020 / ADR-072; roles/modules deferred per ADR-044)
- [ ] AAP Pre-Flight check integrated

## Requirements

| REQ | Name | Status |
|-----|------|--------|
| REQ-003 | Security & Compliance | Draft |
| REQ-004 | Enterprise Integration | In Progress |
| REQ-008 | ROI Dashboard | Draft |
| REQ-010 | Dependency Health Assessment | Draft |
| REQ-011 | AA Deprecated Module Reporting | Draft |
| REQ-012 | EDA Rulebook Validation | Draft |
| REQ-013 | Extended OPA Policy Inputs | Draft |
| REQ-014 | Policy Permissive Mode | Draft |
| REQ-016 | Phase 2 SCM Providers (GitLab + Bitbucket) | In Progress |
| REQ-020 | Unified Supply Chain Security (SBOM, CVE, CWE) | Draft |

## Dependencies

- PHASE-001: CLI Scanner
- PHASE-002: Rewrite Engine (recommended)

## Timeline

- **Target Start**: TBD
- **Target Complete**: TBD
