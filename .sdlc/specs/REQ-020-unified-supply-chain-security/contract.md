# REQ-020: Unified Supply Chain Security — Contract

## Status

Draft

## REST API (Gateway `/api/v1`)

All endpoints follow ADR-060. Additive changes only; default SBOM response unchanged.

### `GET /projects/{project_id}/sbom`

Existing endpoint. Extended query parameters:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `scan_id` | UUID | latest completed | Manifest source scan. Gateway must validate that `scan_id` belongs to `{project_id}` and is authorized for the caller before export. When omitted, latest completed scan for the project is used. |
| `include` | string | _(empty)_ | Comma-separated: `vulnerabilities`, `vex` |

**Default response** (no `include`): CycloneDX 1.5 inventory only — backward compatible.

**With `include=vulnerabilities`**: adds top-level `vulnerabilities` array per
[CycloneDX 1.5 schema](https://cyclonedx.org/docs/1.5/json/). **Phase 2** sources entries
from persisted `R200` rows. **Phase 3** may trigger OSV backfill only for components that
need it (Dep Audit skipped / missing structured CVE / missing `cvss_score` / audit-coverage
not `completed_clean` with complete audit data; `findings_present` with missing CVE
rows or `cvss_score` remains eligible). On in-flight backfill saturation, Gateway
returns inventory + persisted `R200` findings with `apme:advisory_status=pending`
on eligible components whose backfill was queued or skipped (transitions to
`checked`/`none`/`error` when deferred work completes). On timeout, rate-limit
failure, or unreachable OSV, set `apme:advisory_status=error` on affected
components — the HTTP request must not fail solely because OSV is slow or
unavailable (CLI `apme sbom --vulns` must not exit on enrichment timeout alone).

Example vulnerability entry (normative shape):

```json
{
  "bom-ref": "vuln-CVE-2024-22195-pkg:pypi/jinja2@3.1.2",
  "id": "CVE-2024-22195",
  "source": {
    "name": "OSV",
    "url": "https://osv.dev/vulnerability/PYSEC-2024-229"
  },
  "ratings": [
    {
      "source": { "name": "OSV" },
      "score": 5.4,
      "severity": "medium",
      "method": "CVSSv3"
    }
  ],
  "cwes": [1336],
  "description": "Jinja2 sandbox escape via attr filter",
  "affects": [
    { "ref": "pkg:pypi/jinja2@3.1.2" }
  ],
  "recommendation": "Upgrade to jinja2>=3.1.3"
}
```

**With `include=vex`**: implies `include=vulnerabilities`. For suppressed dependency
advisories keyed by `(affected_purl, advisory_id)` (Phase 4; ADR-055 fingerprints alone are
insufficient for `R200`), include matching entries in `vulnerabilities[]` with CycloneDX
`analysis` (v1 — no sibling VEX document).

Suppression records for VEX export MUST include structured CycloneDX fields in addition to
the ADR-055 fingerprint. Free-form `reason` text alone MUST NOT map to
`analysis.state=not_affected` or any `justification` implying non-exploitability.

| Suppression `vex_state` | Required `vex_justification` | `evidence` required | CycloneDX output |
|---------------------------|------------------------------|---------------------|------------------|
| `false_positive` | _(omit)_ | No | `state=false_positive` |
| `not_affected` | `code_not_present` | No | `state=not_affected`, `justification=code_not_present` |
| `not_affected` | `requires_configuration` | Yes | `state=not_affected`, `justification=requires_configuration` |
| `not_affected` | `requires_dependency` | Yes | `state=not_affected`, `justification=requires_dependency` |
| `not_affected` | `requires_environment` | Yes | `state=not_affected`, `justification=requires_environment` |
| `not_affected` | `code_not_reachable` | Yes | `state=not_affected`, `justification=code_not_reachable` |
| `not_affected` | `protected_by_mitigating_control` | Yes | `state=not_affected`, `justification=protected_by_mitigating_control` |
| `resolved` | _(omit)_ | No | `state=resolved` |

When `evidence` is required, the suppression record MUST include a non-empty `evidence`
field; Gateway concatenates `reason` and `evidence` into `analysis.detail`. Suppressions
missing required `vex_state`, `vex_justification`, or `evidence` are ignored for VEX export
(the vulnerability appears without `analysis`).

Example vulnerability entry with VEX analysis (normative shape):

```json
{
  "id": "CVE-2024-22195",
  "affects": [{ "ref": "pkg:pypi/jinja2@3.1.2" }],
  "analysis": {
    "state": "not_affected",
    "justification": "code_not_reachable",
    "detail": "Accepted risk: template path not used in production. Evidence: jinja2 only referenced in dev inventory group vars."
  }
}
```

`id` uses canonical `advisory_id` (see Advisory identity below). All `state` and
`justification` values MUST be valid CycloneDX 1.5 analysis enums.

### `GET /projects/{project_id}/supply-chain` (new)

Summary posture for dashboards and CI gates.

**Response `SupplyChainSummary`:**

```json
{
  "project_id": "uuid",
  "scan_id": "uuid",
  "enriched_at": "2026-09-21T16:00:00Z",
  "components": {
    "collections": 12,
    "python_packages": 47,
    "ansible_core_version": "2.16.3"
  },
  "vulnerabilities": {
    "critical": 0,
    "high": 2,
    "medium": 5,
    "low": 1,
    "unknown": 0
  },
  "components_without_advisory_data": 3,
  "weaknesses": [
    { "cwe_id": 798, "name": "Use of Hard-coded Credentials", "occurrence_count": 2 }
  ],
  "sbom_url": "/api/v1/projects/{project_id}/sbom?scan_id={scan_id}&include=vulnerabilities,vex"
}
```

`sbom_url` MUST include the response `scan_id` so the linked SBOM matches the summarized
scan even if a newer scan completes before the link is followed.

`enriched_at` is `null` until lazy enrichment has run at least once for that scan.

### `GET /dep-health` (extend)

Add optional field on existing `DepHealthSummary`:

```json
{
  "collection_findings": [...],
  "python_cves": [...],
  "supply_chain_enriched_at": "2026-09-21T16:00:00Z"
}
```

Non-breaking additive field.

## CLI

```bash
apme sbom PROJECT_ID                    # inventory only (unchanged)
apme sbom PROJECT_ID --vulns            # include=vulnerabilities (may trigger lazy OSV)
apme sbom PROJECT_ID --vulns --vex      # include=vulnerabilities,vex
apme sbom PROJECT_ID -o sbom.json --vulns
```

Exit codes unchanged. Errors when Gateway unavailable.

## Violation Metadata (gRPC / REST)

Extended `Violation.metadata` keys (string map). Engine stamps `cwe_ids` at emit time
from `src/apme_engine/data/rule_cwe_map.yaml` when the rule is mapped.

| Key | Example | Source |
|-----|---------|--------|
| `advisory_id` | `CVE-2024-22195` or `PYSEC-2024-229` | Dep Audit, OSV (canonical dedup key) |
| `cve_id` | `CVE-2024-22195` | Dep Audit, OSV (nullable when no CVE alias) |
| `osv_id` | `PYSEC-2024-229` | Dep Audit, OSV |
| `cvss_score` | `5.4` | OSV / pip-audit when present |
| `affected_purl` | `pkg:pypi/jinja2@3.1.2` | Dep Audit |
| `dep_fix_versions` | `3.1.3,3.1.4` | pip-audit |
| `cwe_ids` | `798,1336` | engine rule map or OSV |

### Advisory identity

Normative rules for correlating advisories across Dep Audit, OSV, CycloneDX, and VEX:

1. **Canonical key**: `advisory_id` is the deduplication key for persistence,
   CycloneDX `vulnerabilities[].id`, and VEX matching `(affected_purl, advisory_id)`.
2. **Alias precedence**: When a CVE alias exists, `advisory_id` = `CVE-*`. Otherwise
   `advisory_id` = the OSV ecosystem id (`PYSEC-*`, `GHSA-*`, or OSV id). Store all
   known aliases in `cve_id` and `osv_id` when present.
3. **Alias normalization**: When pip-audit and OSV report the same advisory under
   different identifiers, merge to one row keyed by canonical `advisory_id`; prefer
   pip-audit field values when both sources agree.
4. **PYSEC-only advisories**: `advisory_id` = `PYSEC-*` (or GHSA/OSV id), `cve_id`
   = null. VEX suppressions MUST key on `(affected_purl, advisory_id)` so PYSEC-only
   advisories remain matchable without a CVE alias.
5. **CycloneDX `source`**: When `osv_id` is present, `source.name` = `"OSV"` and
   `source.url` references the OSV record; when only CVE is known, `source.name` may
   be `"NVD"` or the originating scanner.

## SARIF (CLI `apme check --sarif`)

For violations with `cwe_ids` in metadata, emit (no Gateway required). Each result's
`taxa` reference must resolve against a matching `run.taxonomies` entry:

```json
{
  "runs": [{
    "taxonomies": [{
      "name": "CWE",
      "guid": "6a1b2c3d-4e5f-6789-abcd-ef0123456789",
      "informationUri": "https://cwe.mitre.org/",
      "taxa": [
        { "id": "798", "name": "Use of Hard-coded Credentials" }
      ]
    }],
    "results": [{
      "ruleId": "SEC:generic-api-key",
      "taxa": [
        { "id": "798", "toolComponent": { "name": "CWE", "guid": "6a1b2c3d-4e5f-6789-abcd-ef0123456789" } }
      ]
    }]
  }]
}
```

Every referenced CWE id must have a descriptor in `run.taxonomies[].taxa`. SARIF
validation tests must confirm taxon references resolve.

## Rule CWE Catalog

File: `src/apme_engine/data/rule_cwe_map.yaml`

```yaml
# rule_id → list of CWE integer IDs
SEC:generic-api-key:
  - 798
R103:
  - 494
  - 829
```

Versioned with engine releases. Gateway reuses stamped metadata / same catalog for SBOM.
ADR-042 plugins may supply `cwe_ids` via `Describe` RPC in a future revision.

## CycloneDX Component Properties

Existing `apme:source` property retained. New optional properties:

| Property | Values | Meaning |
|----------|--------|---------|
| `apme:advisory_status` | `not_applicable`, `excluded`, `not_evaluated`, `pending`, `checked`, `none`, `error` | Mutually exclusive: `not_applicable` = Galaxy (no feed); `excluded` = private-index PyPI (not queried); `not_evaluated` = public PyPI before evaluation or inventory-only export; `pending` = backfill queued/in-flight (transitions on completion); `checked` = evaluated, ≥1 advisory; `none` = evaluated, zero advisories; `error` = evaluation failed (timeout, rate limit, unreachable OSV) |
| `apme:enriched_at` | ISO 8601 | Last OSV check timestamp (PyPI components) |

Galaxy collection components always use `apme:advisory_status=not_applicable` (ADR-072
Option A — collection CVE deferred indefinitely).

## External Integration (documented, not implemented)

Users may run external scanners on exported SBOM:

```bash
apme sbom PROJECT_ID --vulns -o sbom.cdx.json
grype sbom:sbom.cdx.json
# or
trivy sbom --format table sbom.cdx.json
```

APME does not bundle Grype/Trivy (ADR-072).

## OpenAPI

Update `docs/api/openapi.v1.json` via `tox -e openapi` when endpoints ship.
