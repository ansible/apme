# TASK-004: Supply-Chain VEX and Roles/Modules Inventory

## Parent Requirement

REQ-020: Unified Supply Chain Security (SBOM, CVE, CWE)

## Status

Pending

## Description

Add Gateway `supply_chain_advisory_suppressions` persistence and map matching
suppressions to CycloneDX `vulnerabilities[].analysis` when `include=vex`. Optionally
extend SBOM inventory for roles/modules when ADR-044 manifest fields are available.
Do **not** reuse ADR-055 content-violation fingerprint suppressions.

## Prerequisites

- [ ] TASK-003 must be complete

## Implementation Notes

1. Add DB migration for `supply_chain_advisory_suppressions` keyed by
   `(affected_purl, advisory_id, scope)` with `vex_state`, optional
   `vex_justification`, `reason`, and `evidence` per contract mapping table.
2. Normalize `include=vex` to imply `include=vulnerabilities` before serialization.
3. In `sbom.py`, attach `analysis` only for suppressions matching
   `(affected_purl, advisory_id)` with scope `global` or
   `project:<current_project_uuid>`; ignore other-project scopes.
4. Reject free-form `reason`-only rows for `not_affected` / non-exploitability
   justifications; ignore incomplete structured rows (emit vuln without `analysis`).
5. Ensure ADR-055 fingerprint suppressions are never read for VEX export.
6. When ADR-044 manifest fields for roles/modules are present, include them in
   CycloneDX components; otherwise leave a documented no-op.
7. Add CLI `apme sbom --vex` and OpenAPI updates.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| Gateway migrations / models | Create | Advisory suppression table |
| `src/apme_gateway/api/sbom.py` | Modify | VEX `analysis` serialization |
| Gateway API (CRUD or existing suppress UX) | Create/Modify | Manage advisory suppressions |
| `src/apme_engine/cli/sbom_cmd.py` | Modify | `--vex` |
| OpenAPI snapshot | Modify | `include=vex` docs |
| Unit tests | Create | Scope filtering + mapping table |

## Verification

- [ ] `tox -e lint`
- [ ] `tox -e unit`
- [ ] `tox -e openapi`
- [ ] Tests prove ADR-055 fingerprint suppressions do not affect VEX `analysis`
- [ ] Tests prove cross-project scopes are ignored

## Acceptance Criteria Reference

From REQ-020:
- [ ] AC-6: VEX from supply-chain advisory suppressions
- [ ] AC-8: REST API versioning (`include=vex` additive)

## Notes

Phase 4 of design.md. VEX model is defined entirely in REQ-020 / ADR-072 — independent
from ADR-055 content-violation fingerprint suppressions.
