# TASK-001: SBOM Graph and Structured CVE Foundation

## Parent Requirement

REQ-020: Unified Supply Chain Security (SBOM, CVE, CWE)

## Status

Pending

## Description

Parse persisted dependency trees into CycloneDX `dependencies`, fix Dep Audit CVSS
severity when a score is available, and persist structured advisory columns
(or `supply_chain_vulnerabilities` + audit-coverage tables) for Gateway SBOM assembly.

## Prerequisites

- [ ] ADR-072 accepted (or implementation approved while Proposed)
- [ ] REQ-020 Draft accepted or implementation explicitly approved

## Implementation Notes

1. Extend Gateway `sbom.py` to parse `dependency_tree` into CycloneDX
   `dependencies[].dependsOn`.
2. Fix Dep Audit (`auditor.py`) to extract CVSS when present and apply ADR-051
   severity mapping; leave documented fallback when no score.
3. Add Gateway DB migration for `supply_chain_vulnerabilities` and
   `supply_chain_audit_coverage` (see design.md data model).
4. Persist structured metadata on Dep Audit findings: `advisory_id`, nullable
   `cve_id`, `osv_id`, `cvss_score`, `affected_purl`, `dep_*` fields per contract.
5. Upsert `supply_chain_vulnerabilities` on scan completion (`source=pip_audit`) and
   persist per-component audit-coverage status.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/apme_gateway/api/sbom.py` | Modify | Dependency graph serialization |
| `src/apme_engine/validators/dep_audit/auditor.py` | Modify | CVSS + structured CVE metadata |
| `src/apme_gateway/` (models/migrations) | Create/Modify | Vuln + audit-coverage tables |
| `tests/` (sbom + dep_audit) | Create/Modify | Unit coverage |

## Verification

- [ ] `tox -e lint`
- [ ] `tox -e unit`
- [ ] Unit tests cover dependency graph serialization and structured CVE persistence

## Acceptance Criteria Reference

From REQ-020:
- [ ] AC-1: CycloneDX inventory with dependency graph
- [ ] AC-4: Structured CVE persistence and accurate severity

## Notes

Phase 1 of design.md. No OSV backfill, no VEX, no `include=vulnerabilities` yet.
