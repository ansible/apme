# REQ-020: Unified Supply Chain Security — Design

## Status

Draft

## Overview

REQ-020 implements a **three-layer supply chain model** on top of existing scan
infrastructure:

| Layer | Responsibility | Owner |
|-------|----------------|-------|
| **SBOM** | Component inventory + dependency graph | Gateway `sbom.py` |
| **CVE** | Known advisories on **PyPI** components | Dep Audit (inline) + Gateway OSV enricher (lazy) |
| **CWE** | Weakness taxonomy on security findings | Engine rule catalog + OSV metadata + export serializers |

Galaxy collection **CVE** is deferred indefinitely (ADR-072 Option A). Collections stay in
the SBOM with `apme:advisory_status=not_applicable`; Collection Health (ADR-051) remains
the collection content-risk signal.

See [ADR-072](../../adrs/ADR-072-unified-supply-chain-sbom-cve-cwe.md) for architectural
decisions. This document covers component layout and phased implementation.

## Architecture

```text
┌─ Engine pod (scan path) ──────────────────────────────────────────┐
│  FixSession / check                                                 │
│    → ProjectManifest (collections, python_packages, dep_tree)       │
│    → Dep Audit: pip-audit → R200 + cve metadata (+ CVSS when known) │
│    → Native/Gitleaks/OPA: content security violations               │
│    → Stamp cwe_ids from engine rule_cwe_map.yaml on emit            │
│    → CLI sarif.py: CWE taxa (no Gateway required)                   │
│  (validators read-only; no Gateway OSV calls from engine)           │
└────────────────────────────┬────────────────────────────────────────┘
                             │ gRPC Reporting
┌─ Gateway ──────────────────┴────────────────────────────────────────┐
│  Persist: scan_manifests, scan_collections, scan_python_packages      │
│           violations (structured cve_id, cvss, purl, cwe_ids)         │
│  Enrich:  osv_enricher.py — lazy OSV batch for PyPI PURLs             │
│           (on first sbom?include=vulnerabilities)                     │
│  Export:  sbom.py — CycloneDX components + deps + vulnerabilities     │
│           + vulnerabilities[].analysis for VEX (ADR-055)              │
│           supply_chain summary REST                                   │
└─────────────────────────────────────────────────────────────────────┘
```

### Architectural compatibility

| Invariant | Impact |
|-----------|--------|
| Validators read-only | OK — Dep Audit unchanged; no remediation of deps |
| Engine never queries out | OK — OSV enrichment is Gateway-only (pip-audit may still use OSV as today) |
| gRPC between services | OK — no new inter-service protocols |
| Stateless engine | OK — enrichment/persistence at Gateway |
| ADR-060 REST versioning | OK — additive query params; default unchanged |
| Built-in bundles closed | OK — CWE map ships with engine image; no external rule dirs |

## Key Components

### Engine: `data/rule_cwe_map.yaml` (new)

Static mapping for built-in security rules. Loaded by engine code paths that emit
violations and by SARIF export. Gateway may import the same module/path for SBOM
`cwes` when assembling from persisted metadata (prefer metadata already stamped).

Initial scope:

- All `SEC:*` Gitleaks rules → CWE-798 (credentials) or rule-specific CWE where known
- Risk rules from ADR-051 Collection Health curated set (`R101`, `R103`–`R109`, `R111`–`R115`, `R117`, `R401`) where CWE is known
- OPA policy rules: defer to plugin `Describe` RPC (ADR-042 future)

### Gateway: `osv_enricher.py` (new)

- Input: list of PyPI `(purl, name, version)` from latest manifest, filtered by Phase 3
  backfill eligibility (Dep Audit skipped, missing structured CVE, missing `cvss_score`,
  or incomplete `findings_present` data; `completed_clean` with complete audit is skipped)
- Resolves OSV endpoint from Gateway configuration (`OSV_ENDPOINT`, default
  `https://api.osv.dev`); air-gapped deployments point to a private mirror
- Calls OSV batch API (`POST {OSV_ENDPOINT}/v1/querybatch`) **only** for eligible PyPI
  components when cache miss/expired on `include=vulnerabilities` (Phase 3)
- Excludes private-index packages unless explicitly opted in; emit
  `apme:advisory_status=excluded` (ADR-072)
- Shared in-flight backfill limit (default 1 concurrent OSV batch per project);
  saturated requests return inventory + `R200` without blocking
- Does **not** query Galaxy `pkg:generic` collections (ADR-072 Option A)
- Configurable per-request timeout and rate-limit controls; on timeout/rate-limit
  failure, set `apme:advisory_status=error` and return partial results (no 503)
- Output: `SupplyChainVulnerability` rows keyed by `scan_id` + `purl` + `advisory_id`
  where `advisory_id` is canonical (CVE when present, otherwise OSV/GHSA/PYSEC id);
  `cve_id` nullable
- Idempotent; TTL cache (e.g. 24h) per `(purl, version)` to limit API calls

**Value vs Dep Audit:** inline `R200` gates CI during `check`; Gateway OSV supports SBOM
assembly, severity backfill, and coverage when Dep Audit was skipped. Deduplicate on
export.

### Gateway: `sbom.py` (extend)

- Parse `dependency_tree` into `dependencies[].dependsOn` (uv pip tree format)
- Emit `apme:advisory_status` on every CycloneDX component; derive supply-chain
  summary counts from these per-component values
- Normalize `include`: `vex` implies `vulnerabilities` before serialization
- Add `vulnerabilities[]` when normalized `include` contains `vulnerabilities`
- Merge pip-audit (`R200`) + OSV enrichment rows (PyPI)
- When normalized `include` contains `vex`, set `vulnerabilities[].analysis` from
  ADR-055 suppressions
- Attach `cwes` on vulnerability entries from OSV/CVE data only (not content-rule CWE)

### Dep Audit: `auditor.py` (fix)

- Extract CVSS from pip-audit/OSV JSON when present; apply ADR-051 severity mapping
- When no score: documented fallback severity (today's behavior), not fabricated CVSS
- Populate metadata keys: `advisory_id` (canonical), nullable `cve_id`, `osv_id`,
  `cvss_score`, `affected_purl`, `dep_fix_versions`, `cwe_ids` (from OSV when present)
- Persist audit-coverage status per PyPI component on scan completion

### CLI: `sbom_cmd.py` / `sarif.py` (extend)

- `--vulns` → `include=vulnerabilities` on Gateway request
- `--vex` → `include=vex`
- SARIF reads `cwe_ids` from violation metadata (engine-stamped)
- Document Grype/Trivy post-processing in CLI guide (not bundled)

### Frontend (optional phase)

- Project detail: supply chain tab summarizing CVE/CWE counts
- Link to download full CycloneDX

## Phased Implementation

### Phase 1 — Foundation (SBOM graph + structured CVE)

| Task | Description |
|------|-------------|
| Parse dependency tree into CycloneDX `dependencies` | `sbom.py` |
| Fix Dep Audit CVSS severity when score available | `auditor.py` |
| Persist structured CVE columns on violations or new `supply_chain_vulns` table | Gateway DB migration |
| Unit tests: `test_sbom_serializer.py`, `test_dep_audit.py` | tox -e unit |

### Phase 2 — CVE in SBOM (R200) + CWE catalog

| Task | Description |
|------|-------------|
| `?include=vulnerabilities` serializes persisted `R200` rows | router + sbom.py |
| `apme sbom --vulns` | sbom_cmd.py |
| `rule_cwe_map.yaml` in engine + stamp `cwe_ids` on emit | engine + violation_convert |
| SARIF CWE taxa | `sarif.py` |
| OpenAPI update | tox -e openapi |

### Phase 3 — OSV backfill + supply-chain summary (PyPI)

| Task | Description |
|------|-------------|
| `osv_enricher.py` + persistence (backfill rules only) | Gateway |
| `GET /projects/{id}/supply-chain` summary endpoint | router |
| `apme:advisory_status` per CycloneDX component in `sbom.py` | sbom.py |
| Audit-coverage persistence table | Gateway DB migration |
| Air-gap / OSV endpoint configuration docs | DEPLOYMENT.md |

### Phase 4 — VEX + roles/modules

| Task | Description |
|------|-------------|
| VEX via `vulnerabilities[].analysis` (CVE-keyed suppressions) | sbom.py |
| Roles/modules in SBOM when ADR-044 manifest fields land | sbom.py |

Collection CVE feed is out of scope (ADR-072 Option A). Revisit only via a new ADR.

## Data Model (sketch)

```sql
-- Audit coverage: distinguishes clean audit from skipped/failed (backfill eligibility)

CREATE TABLE supply_chain_audit_coverage (
    id UUID PRIMARY KEY,
    scan_id UUID NOT NULL REFERENCES scans(id),
    project_id UUID NOT NULL REFERENCES projects(id),
    purl TEXT NOT NULL,
    coverage_status TEXT NOT NULL,  -- 'completed_clean' | 'findings_present' | 'skipped' | 'failed'
    checked_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (scan_id, purl)
);

-- Preferred: dedicated table for SBOM assembly performance

CREATE TABLE supply_chain_vulnerabilities (
    id UUID PRIMARY KEY,
    scan_id UUID NOT NULL REFERENCES scans(id),
    project_id UUID NOT NULL REFERENCES projects(id),
    purl TEXT NOT NULL,
    advisory_id TEXT NOT NULL,   -- canonical: CVE-* or OSV/GHSA/PYSEC id
    cve_id TEXT,                   -- nullable; populated when CVE alias exists
    osv_id TEXT,
    cvss_score FLOAT,
    severity TEXT,
    fix_versions TEXT,
    cwe_ids TEXT,                  -- JSON array
    source TEXT NOT NULL,          -- 'pip_audit' | 'osv_enrichment'
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (scan_id, purl, advisory_id)
);
```

Final schema in [contract.md](contract.md).

## Relationship to REQ-003 and REQ-010

- **REQ-003** SBOM generation AC is **delivered by REQ-020** (inventory + optional vulns).
  Remaining REQ-003 work (custom policies, etc.) stays on REQ-003.
- **REQ-010** proposed a sidecar for periodic dependency health. ADR-051 implemented
  **inline** Collection Health + Dep Audit. REQ-020 does **not** reintroduce the sidecar;
  it is the unified export and lazy enrichment layer on persisted scan results. REQ-010
  cross-project aggregation remains complementary via `/dep-health` extensions.

## Testing Strategy

- Unit: CycloneDX vulnerability serialization, OSV response parsing, CWE mapping, dedupe
- Integration: scan → persist → `GET .../sbom?include=vulnerabilities` golden file
- Contract: OpenAPI snapshot diff; ADR-060 backward compat test for default SBOM
- Negative: Galaxy components never receive fabricated OSV CVEs

## Key ADRs

- [ADR-072: Unified Supply Chain SBOM/CVE/CWE](../../adrs/ADR-072-unified-supply-chain-sbom-cve-cwe.md)
- [ADR-040: Scan Metadata Enrichment](../../adrs/ADR-040-scan-metadata-enrichment.md)
- [ADR-051: Dependency Health Scanning](../../adrs/ADR-051-dependency-health-scanning.md)
- [ADR-055: Violation Suppression](../../adrs/ADR-055-violation-fingerprint-suppression.md)
- [ADR-060: REST API Versioning](../../adrs/ADR-060-rest-api-versioning-contract.md)
