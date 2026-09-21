# REQ-020: Unified Supply Chain Security (SBOM, CVE, CWE)

## Metadata

- **Phase**: PHASE-003 - Enterprise Dashboard
- **Status**: Draft
- **Created**: 2026-09-21
- **Prerequisites**: ADR-040, ADR-051, ADR-055, ADR-060; ADR-072 pending acceptance
- **Related**: REQ-003 (Security & Compliance — SBOM AC delivered here), REQ-010 (Dependency Health Assessment — export/enrichment layer; sidecar not reintroduced), DR-002 (SBOM Format)

## Purpose

Enterprises need a single, machine-readable supply chain security posture for Ansible
projects: **what dependencies exist** (SBOM), **which known advisories affect them**
(CVE), and **what classes of weakness are present** in dependencies and content (CWE).

APME already produces CycloneDX inventory (`apme sbom`), Python CVE findings during
`apme check` (Dep Audit / `R200`), and security content findings (`SEC:*`, risk rules).
These are fragmented across endpoints and formats with no CWE taxonomy and no CVE data
embedded in SBOM exports.

This requirement unifies supply chain outputs into one Gateway-owned export model while
keeping validators read-only and preserving the engine's stateless scan path.

**v1 scope (honest):** full SBOM + **Python/ansible-core CVE** + **CWE on security
findings**. Galaxy collection **CVE** is **deferred indefinitely** (ADR-072 Option A).
Collections remain in the SBOM with `apme:advisory_status=not_applicable`. Collection *content*
risk continues via Collection Health (ADR-051), optionally CWE-mapped.

## User Stories

**As a Security Engineer**, I want a CycloneDX SBOM with vulnerability and weakness
metadata for a scanned project, so that I can feed enterprise scanners and compliance
tools without manual correlation.

**As a Compliance Officer**, I want CVE and CWE identifiers on dependency and security
findings in machine-readable exports, so that I can map APME results to internal control
frameworks and downstream GRC tools.

**As a Platform Admin**, I want Python dependency CVEs correlated to manifest components,
so that I can see which projects are affected by a specific advisory.

**As a CI/CD Operator**, I want `apme sbom` and SARIF output to include optional
vulnerability and weakness enrichment, so that pipelines can gate on supply chain risk
alongside content violations.

## Acceptance Criteria

### AC-1: CycloneDX inventory with dependency graph

- **GIVEN** a project with a completed scan and persisted manifest (ADR-040)
- **WHEN** a consumer requests `GET /api/v1/projects/{id}/sbom`
- **THEN** the response is valid CycloneDX 1.5 JSON with components for ansible-core,
  Ansible collections, and Python packages, including PURLs, license, and supplier
- **AND** `dependencies` reflects the resolved package graph parsed from persisted
  `dependency_tree` where available

### AC-2: CycloneDX vulnerability section (CVE)

- **GIVEN** a project with Dep Audit findings (`R200`) and/or Gateway OSV enrichment
  results for PyPI components (ADR-072)
- **WHEN** a consumer requests `GET /api/v1/projects/{id}/sbom?include=vulnerabilities`
  or `apme sbom PROJECT_ID --vulns`
- **THEN** the CycloneDX document includes a `vulnerabilities` array
- **AND** each entry references affected component `bom-ref` / PURL, canonical
  `advisory_id` (CVE when present, otherwise OSV/GHSA/PYSEC id), optional `cve_id`
  when a CVE alias exists, severity (from CVSS when available), and fix versions
  when known
- **AND** findings are deduplicated by `(affected_purl, advisory_id)` where `advisory_id`
  is the canonical identifier (CVE when present, otherwise OSV/GHSA/PYSEC id)
- **AND** Galaxy collection components use `apme:advisory_status=not_applicable` and are
  not queried against OSV (ADR-072 Option A — collection CVE deferred indefinitely)

### AC-3: CWE on security findings

- **GIVEN** built-in security rules with CWE mappings (`SEC:*`, selected `R*` rules per
  ADR-072 catalog shipped with the engine)
- **WHEN** violations are emitted (CLI or Gateway-persisted)
- **THEN** violation metadata includes `cwe_ids`
- **AND** CycloneDX `vulnerabilities[].cwes` include CWE from OSV/CVE data only (not
  content-rule CWE from secrets/playbook findings)
- **AND** SARIF output from `apme check --sarif` includes CWE references for mapped rules
  (via `taxa`) without requiring Gateway

### AC-4: Structured CVE persistence and accurate severity

- **GIVEN** Dep Audit produces a pip-audit/OSV finding
- **WHEN** the finding is reported to the Gateway
- **THEN** structured fields are persisted in violation metadata: `advisory_id`
  (canonical — CVE when present, otherwise OSV/GHSA/PYSEC id from pip-audit), nullable
  `cve_id` (when a CVE alias exists), `osv_id` (when present), `cvss_score` (when
  present), `affected_purl`, `dep_fix_versions`, `dep_package`, `dep_installed_version`
  (aligned with `auditor.py` / Gateway convert)
- **AND** violation severity follows ADR-051 CVSS mapping when a score is available;
  otherwise severity remains a documented fallback (not a silent invent)

### AC-5: Gateway OSV backfill (Phase 3; optional)

- **GIVEN** a scan where Dep Audit was skipped, structured CVE rows are missing, or
  `cvss_score` is absent for a PyPI component — including `findings_present`
  coverage with incomplete structured data — and audit-coverage is not
  `completed_clean` with a complete audit (no missing CVE rows or scores)
- **WHEN** a consumer requests SBOM with `include=vulnerabilities` and backfill cache
  is missing or expired (ADR-072 Phase 3)
- **THEN** only those eligible components may be batch-queried against OSV; components
  with `completed_clean` coverage and no data gaps are not re-queried
- **AND** inventory-only exports (no `include=vulnerabilities`) set public PyPI
  components to `apme:advisory_status=not_evaluated`; evaluated components with
  one or more advisories use `checked`; zero advisories use `none`; Galaxy
  collections use `apme:advisory_status=not_applicable` without an OSV call
- **AND** concurrency saturation sets eligible components to `pending` immediately;
  successful deferred backfill transitions them to `checked` or `none` and persists
  enrichment rows; failures retain or transition to `error`
- **AND** enrichment failures (timeout, rate limit, unreachable OSV) set
  `apme:advisory_status=error`; export still returns inventory + persisted `R200`
  vulns when present (no 503 solely for OSV failure)
- **AND** audit-coverage status (`completed_clean`, `findings_present`, `skipped`,
  `failed`) is persisted per PyPI component so clean audits are not re-queried

### AC-6: VEX from suppressions

- **GIVEN** an active suppression (ADR-055) for a dependency advisory finding keyed by
  `(affected_purl, advisory_id)` with structured `vex_state` / `vex_justification`
  (and `evidence` when required per contract mapping table)
- **WHEN** SBOM is requested with `?include=vex` (implies `include=vulnerabilities`; Phase 4)
- **THEN** suppressed advisories appear in `vulnerabilities[]` with CycloneDX
  `analysis.state` / `justification` / `detail` derived from the structured fields
- **AND** free-form `reason` alone does not produce `not_affected` or non-exploitability
  justifications
- **AND** no separate sibling VEX document is required in v1

### AC-7: Supply chain summary API

- **GIVEN** a scanned project
- **WHEN** a consumer requests `GET /api/v1/projects/{id}/supply-chain`
- **THEN** the response summarizes component counts, CVE counts by severity, CWE
  categories present, and last enrichment timestamp (null if enrichment has not run)
- **AND** the response references the SBOM endpoint for full CycloneDX export, including
  the summarized `scan_id` in `sbom_url`

### AC-8: REST API versioning

- **GIVEN** ADR-060 versioning rules
- **WHEN** new query parameters or endpoints are added
- **THEN** existing `/api/v1/projects/{id}/sbom` response shape without `include`
  remains backward compatible (inventory-only default)
- **AND** OpenAPI spec is updated (`tox -e openapi`)
- **AND** `project_id` path parameter accepts a project UUID or unique display name
  (existing Gateway `resolve_project` behavior; unchanged for SBOM clients)

## Inputs / Outputs

### Inputs

| Name | Type | Description | Required |
|------|------|-------------|----------|
| `project_id` | UUID or string | Gateway project UUID or unique display name (same as existing SBOM route via `resolve_project`) | Yes |
| `scan_id` | UUID | Specific scan (default: latest completed) | No |
| `include` | query string | `vulnerabilities`, `vex` (comma-separated) | No |
| Scan manifest | internal | Collections, Python packages, dependency tree (ADR-040) | Yes |
| Dep Audit violations | internal | `R200` findings with CVE metadata | No |
| OSV API | external | Batch vulnerability lookup (PyPI) | No (enrichment) |
| Rule CWE catalog | internal | `rule_id` → CWE ID mapping (engine-shipped) | Yes (for content CWE) |

### Outputs

| Name | Type | Description |
|------|------|-------------|
| CycloneDX 1.5 JSON | file/REST | SBOM with optional `vulnerabilities` and analysis (VEX) |
| Supply chain summary | REST JSON | Aggregated SBOM/CVE/CWE posture |
| SARIF 2.1.0 | CLI | Existing `apme check --sarif` with CWE taxa |
| Violation metadata | gRPC/REST | `cwe_ids`, structured CVE fields on findings |

## Behavior

### Happy Path

1. User runs `apme check` on a project (Engine emits manifest + Dep Audit `R200` findings
   with `cwe_ids` stamped from the engine catalog where mapped).
2. Gateway persists manifest, violations, and structured CVE records.
3. User runs `apme sbom PROJECT_ID --vulns` or calls REST with `include=vulnerabilities`.
4. Gateway filters PyPI components by Phase 3 backfill eligibility (skipped audit,
   missing structured CVE, missing `cvss_score`, or incomplete `findings_present`
   data); `completed_clean` components with complete audit data are skipped. Eligible
   components are batch-enriched on cache miss/expired, subject to the shared
   in-flight OSV backfill limit.
5. Gateway assembles CycloneDX: components + dependency graph + vulnerabilities + CWE links.
6. Enterprise tool ingests SBOM; CI gates on critical CVE count.

### Edge Cases

| Case | Handling |
|------|----------|
| No Gateway / daemon-only mode | `apme sbom` documents Gateway requirement (existing); inline `R200` + CWE via SARIF still available via `apme check` |
| Dep Audit disabled (`--skip-python-audit`) | SBOM inventory still generated; Python CVE from lazy OSV enrichment when `--vulns` requested |
| Galaxy collection PURL | Listed in SBOM; `apme:advisory_status=not_applicable` (no collection CVE feed) |
| OSV API unreachable | Set `apme:advisory_status=error` on affected eligible components; SBOM inventory + pip-audit findings still exported; log warning (no 503) |
| Air-gapped deployment | OSV mirror / pip-audit cache documented; enrichment uses configured cache endpoint |
| Duplicate advisory from pip-audit and OSV | Dedupe by `(purl, advisory_id)`; prefer pip-audit fields when both present |
| OSV advisory without CVE alias (GHSA/PYSEC only) | Persist with `advisory_id` = OSV/GHSA/PYSEC id; `cve_id` null; include in SBOM |
| Private PyPI package (internal index) | Excluded from OSV batch unless opted in; `apme:advisory_status=excluded` |
| No CVSS in pip-audit JSON | Keep documented severity fallback; prefer OSV score when enrichment provides one |

### Error Conditions

| Error | Cause | Response |
|-------|-------|----------|
| 404 | Project or scan not found | Standard Gateway error |
| 409 | Scan in progress, manifest incomplete | Retry guidance in error body |
| 503 | SBOM assembly internal failure (invalid CycloneDX) | Log + 503; OSV backfill failure alone must not 503 when inventory + `R200` export is valid |

## Dependencies

### Internal

- REQ-001: Core Scanning Engine (manifest emission)
- REQ-003: Security & Compliance (parent theme; **SBOM AC satisfied by this REQ**)
- REQ-010: Dependency Health Assessment (CVE/collection health; REQ-020 is export +
  lazy enrichment on persisted results — does **not** reintroduce the sidecar)
- ADR-040: Scan Metadata Enrichment
- ADR-051: Dependency Health Scanning (Dep Audit, `R200`)
- ADR-055: Violation Suppression (VEX mapping)
- ADR-060: REST API Versioning Contract
- ADR-072: Unified Supply Chain SBOM/CVE/CWE Architecture
- Architectural compatibility: Verified (no invariant conflicts; see design.md)

### External

- [OSV.dev](https://osv.dev/) — vulnerability batch API for Gateway enrichment (PyPI)
- [pip-audit](https://github.com/pypa/pip-audit) — Python CVE detection (existing)
- [CycloneDX 1.5](https://cyclonedx.org/) — SBOM format
- [CWE](https://cwe.mitre.org/) — weakness taxonomy for rule mapping

## Non-Functional Requirements

- **Performance**: OSV batch enrichment must not block scan completion; lazy on first
  SBOM vulns request. SBOM assembly &lt; 2s for manifests with ≤ 200 components when
  enrichment cache is warm.
- **Security**: New OSV backfill calls are outbound from Gateway only. Existing
  engine-side `pip-audit` OSV access (ADR-051) is unchanged. Vulnerability data is
  not secret but must not log project paths in enrichment errors.
- **Compatibility**: Default SBOM export unchanged (inventory-only). Grype/Trivy documented
  as external consumers of exported CycloneDX (not required runtime deps).

## Security Considerations

- **Threat model**: Supply chain export reveals dependency inventory and known CVEs —
  intended for authorized Gateway consumers. No new unauthenticated exposure beyond
  existing project API auth.
- **Data sensitivity**: SBOM may aid reconnaissance; same access control as project scan
  results applies.
- **Attack surface**: Gateway OSV client adds outbound HTTPS; configurable timeout and
  rate limits required.
- **Authz/Authn**: Same as existing `/api/v1/projects/{id}/*` endpoints.

## Open Questions

- [x] **Collection CVE advisory source** (ADR-072): **Option A — defer indefinitely.**
- [x] **DR-002 format**: CycloneDX-only for v1; SPDX deferred (ADR-072).
- [x] **Enrichment trigger**: Lazy on first `include=vulnerabilities` (ADR-072).
- [x] **REQ-010 relationship**: Export/enrichment layer on ADR-051 results; no sidecar
  reintroduction (ADR-072).

## References

- [ADR-072: Unified Supply Chain SBOM/CVE/CWE](../../adrs/ADR-072-unified-supply-chain-sbom-cve-cwe.md)
- [ADR-051: Dependency Health Scanning](../../adrs/ADR-051-dependency-health-scanning.md)
- [DR-002: SBOM Format and Scope](../../decisions/closed/deferred/DR-002-sbom-format.md)
- [docs/guides/CLI.md](../../../docs/guides/CLI.md) — `apme sbom`, `apme check`
- [Industry gap analysis](../../research/industry-gap-analysis.md) — SBOM/CVE compliance gaps

---

## Change History

| Date | Author | Change |
|------|--------|--------|
| 2026-09-21 | Agent | Initial draft from supply chain architecture discussion |
| 2026-09-21 | Agent | Align with ADR-071: lazy enrichment, engine CWE catalog, VEX analysis, honest collection CVE scope |
| 2026-09-21 | Agent | Collection CVE: Option A — defer indefinitely |
| 2026-09-21 | Agent | PR #689 round 3: advisory status lifecycle, project_id display name, OSV unreachable error status |
| 2026-09-21 | Agent | PR #689 round 4: VEX suppression mapping, sbom_url scan_id binding, advisory identity rules |
