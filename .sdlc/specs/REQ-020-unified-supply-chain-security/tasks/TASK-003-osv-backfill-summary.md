# TASK-003: OSV Backfill and Supply-Chain Summary

## Parent Requirement

REQ-020: Unified Supply Chain Security (SBOM, CVE, CWE)

## Status

Pending

## Description

Implement Gateway `osv_enricher.py` for eligible PyPI components, emit
`apme:advisory_status` on CycloneDX components, add
`GET /api/v1/projects/{id}/supply-chain`, and document OSV endpoint / air-gap config.

## Prerequisites

- [ ] TASK-002 must be complete

## Implementation Notes

1. Implement `osv_enricher.py`: batch OSV only for Phase 3 backfill-eligible PyPI
   components (skipped audit, missing CVE/score, incomplete `findings_present`;
   skip `completed_clean` with complete audit).
2. Respect concurrency saturation, durable enqueue → `pending`, timeout/rate-limit →
   `error`, private-index → `excluded`, Galaxy → `not_applicable`.
3. Upsert enrichment into `supply_chain_vulnerabilities` with merge rules from
   contract Advisory identity §3.
4. Emit `apme:advisory_status` (and optional `apme:enriched_at`) on every component.
5. Add `GET /api/v1/projects/{id}/supply-chain` summary endpoint with
   `sbom_url` bound to the summarized `scan_id`.
6. Document `OSV_ENDPOINT`, timeout, and rate-limit configuration for air-gapped
   deployments.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/apme_gateway/osv_enricher.py` | Create | Lazy OSV backfill |
| `src/apme_gateway/api/sbom.py` | Modify | Advisory status properties |
| `src/apme_gateway/api/` (router) | Modify | Supply-chain summary |
| Gateway migrations / models | Modify | Coverage + vuln upsert paths |
| `docs/` (deployment / architecture) | Modify | OSV endpoint + air-gap |
| OpenAPI snapshot | Modify | New endpoint + params |

## Verification

- [ ] `tox -e lint`
- [ ] `tox -e unit`
- [ ] `tox -e openapi`
- [ ] Unit tests: eligibility filters, advisory_status lifecycle, merge on upsert
- [ ] Negative: Galaxy components never receive fabricated OSV CVEs

## Acceptance Criteria Reference

From REQ-020:
- [ ] AC-2: CycloneDX vulnerability section — Phase 3 OSV enrichment path
- [ ] AC-5: Gateway OSV backfill
- [ ] AC-7: Supply chain summary API
- [ ] AC-8: REST API versioning

## Notes

Phase 3 of design.md. Closes the practical path for DR-002 Python CVE-in-SBOM.
