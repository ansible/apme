# REQ-010: Dependency Health Assessment

## Metadata

- **Phase**: PHASE-003 - Enterprise Dashboard
- **Status**: Draft
- **Created**: 2026-03-25
- **Supersedes**: Original REQ-010 proposal (PR #93)
- **Prerequisite**: ADR-040 (Scan Metadata Enrichment)

## Overview

A sidecar service that consumes project dependency metadata from the Gateway (ADR-040) and produces health assessments for Ansible collections and Python packages. The service queries the Gateway API for discovered dependencies, runs analysis (collection scanning via the engine, Python CVE checking via pip-audit/OSV), and reports results back to the Gateway for persistence and correlation with projects.

This separates concerns: the engine scans content, the Gateway stores data and serves APIs, and this service orchestrates derivative analysis using both.

## User Stories

**As a Security Engineer**, I want to know if the Ansible collections my projects depend on have security issues in their own code, so that I can assess supply-chain risk.

**As a Platform Admin**, I want Python dependency CVE scanning across all projects, so that I can identify vulnerable packages in execution environments before deployment.

**As an Automation Architect**, I want collection health scores correlated to my projects, so that I can see which projects are affected when a collection has a problem.

**As a CI Pipeline Operator**, I want a single API query to check whether a project's dependencies are healthy, so that I can gate deployments on dependency status alongside code violations.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                          apme-pod                                │
│                                                                  │
│  ┌──────────┐    ┌──────────┐    ┌───────────────────────────┐  │
│  │  Engine   │    │  Galaxy   │    │   Dependency Health       │  │
│  │  :50051   │    │  Proxy    │    │   Service (sidecar)       │  │
│  │           │    │  :8765    │    │                           │  │
│  │  scans    │◄───┤  resolves │◄───┤  1. GET /api/v1/collections│  │
│  │  content  │    │  collections│   │     from Gateway          │  │
│  │           │    │           │    │  2. Download collection    │  │
│  └─────▲─────┘    └──────────┘    │     from Galaxy Proxy     │  │
│        │                          │  3. Scan via Engine gRPC   │  │
│        │  Scan(collection)        │  4. pip-audit for Python   │  │
│        └──────────────────────────┤  5. POST health back to   │  │
│                                   │     Gateway API            │  │
│  ┌──────────┐                     └───────────────────────────┘  │
│  │  Gateway  │                              │                    │
│  │  :8080    │◄─────────────────────────────┘                    │
│  │           │  GET /api/v1/collections                          │
│  │  persists │  POST /api/v1/collections/{fqcn}/health           │
│  │  serves   │  GET /api/v1/python-packages                      │
│  │  API      │  POST /api/v1/python-packages/{name}/health       │
│  └──────────┘                                                    │
└──────────────────────────────────────────────────────────────────┘
```

## Acceptance Criteria

### Collection Health Scanning

- **GIVEN** the Gateway has project manifests with collection references (ADR-040)
- **WHEN** the dependency health service runs its periodic scan
- **THEN** each unique collection+version is downloaded from Galaxy Proxy, scanned through the engine, and the health score is stored in the Gateway

### Python CVE Scanning

- **GIVEN** the Gateway has project manifests with Python package references (ADR-040)
- **WHEN** the dependency health service runs its periodic scan
- **THEN** each unique package+version is checked against a vulnerability database (OSV/pip-audit) and results are stored in the Gateway

### Project Correlation

- **GIVEN** a collection with a Critical finding from its health scan
- **WHEN** a consumer queries `GET /api/v1/projects/{id}/dependencies`
- **THEN** the response includes the collection's health score and any CVE/violation data

### Collection Health API

- **GIVEN** collection health data in the Gateway
- **WHEN** a consumer queries `GET /api/v1/collections/{fqcn}`
- **THEN** the response includes health score, violation summary, version, and list of projects using it

### Incremental Scanning

- **GIVEN** a collection+version that was already scanned
- **WHEN** the periodic job runs again
- **THEN** the collection is skipped (immutable content = same results) unless a rescan is forced

### New Version Detection

- **GIVEN** a project scan discovers `community.general@9.0.0` (previously only 8.0.0 was known)
- **WHEN** the dependency health service runs
- **THEN** it scans the new version and updates the collection health data

## Inputs / Outputs

### Inputs (from Gateway API)

| Name | Type | Description | Required |
|------|------|-------------|----------|
| Collections list | `GET /api/v1/collections` | All unique collections across projects | Yes |
| Python packages list | `GET /api/v1/python-packages` | All unique packages across projects | Yes |

### Outputs (to Gateway API)

| Name | Type | Description |
|------|------|-------------|
| Collection health | `POST /api/v1/collections/{fqcn}/health` | Violation summary, health score, scan_id |
| Package health | `POST /api/v1/python-packages/{name}/health` | CVE list, severity, fix versions |

## Behavior

### Happy Path — Collection Scanning

1. Service queries Gateway: `GET /api/v1/collections` (returns all known collection+version pairs)
2. For each unscanned collection+version:
   a. Resolve and retrieve the collection artifact via the Galaxy Proxy's PEP 503 simple index (e.g., install via `uv pip install` against `/simple/` or fetch the wheel/tarball from the index)
   b. Submit to Engine via gRPC `FixSession` (same API the CLI uses per ADR-039)
   c. Receive violations, compute health score
   d. `POST` health assessment back to Gateway
3. Gateway correlates collection health to projects that use it

### Happy Path — Python CVE Scanning

1. Service queries Gateway: `GET /api/v1/python-packages` (returns all known package+version pairs)
2. For each unscanned package+version:
   a. Query vulnerability database (OSV.dev API or local pip-audit)
   b. Collect CVE IDs, severity (CVSS), affected ranges, fix versions
   c. `POST` health assessment back to Gateway
3. Gateway correlates package health to projects

### Edge Cases

| Case | Handling |
|------|----------|
| Collection not on Galaxy | Skip; log warning. Mark as "unresolvable" |
| Galaxy Proxy unreachable | Retry with backoff; use cached results if available |
| Engine unavailable | Skip collection scanning; continue with Python CVE checks |
| Vulnerability DB unreachable | Use cached data; note staleness in assessment |
| Collection+version already scanned | Skip (immutable = same results) |

### Error Conditions

| Error | Cause | Response |
|-------|-------|----------|
| Scan timeout | Large collection | Mark as "scan_failed"; don't block other collections |
| CVE API rate limit | Too many queries | Batch queries; respect rate limits; backoff |
| Gateway API error | Gateway unavailable | Retry with backoff; halt periodic job if persistent |

## Dependencies

### Internal

- **ADR-040**: Scan Metadata Enrichment (prerequisite — provides the dependency data)
- **ADR-038**: Public Data API (the Gateway API this service consumes and writes to)
- **REQ-001**: Core Scanning Engine (the engine scans collection content)
- Galaxy Proxy (resolves and serves collections)

### External

- Vulnerability database API (OSV.dev recommended — free, no API key, supports batch)
- `pip-audit` (optional — can run as a subprocess for Python package auditing)

## Non-Functional Requirements

- **Performance**: Periodic scan should process 100 collections in < 10 minutes (parallelized)
- **Caching**: Collection+version scan results are immutable — scan once, cache forever. Python CVE data has a 1-hour TTL.
- **Isolation**: Runs as a sidecar container in the pod. Failure does not affect engine or Gateway operation.
- **Security**: Vulnerability database queries should not leak dependency information (use batch API or local mirror for sensitive environments)

## Open Questions

- [ ] Should the sidecar use gRPC or REST to communicate with the Gateway? (REST aligns with ADR-038; gRPC aligns with ADR-001)
- [ ] Should collection health factor into the project health score (ADR-037), or be a separate metric?
- [x] What vulnerability database should be the default? → OSV via pip-audit (ADR-051) + Gateway lazy OSV (ADR-072 / REQ-020)
- [ ] Should the service support on-demand scans (triggered by webhook on new collection version)?
- [x] Should SBOM generation (DR-002) be a feature of this service or a separate concern? → **Separate: REQ-020 / ADR-072**

## Relationship to ADR-051 and REQ-020

**Scan-path dependency health** (Collection Health + Dep Audit / `R200`) is implemented
inline per [ADR-051](../../adrs/ADR-051-dependency-health-scanning.md). This REQ's
original **sidecar** remains the design for **cross-project / periodic** aggregation;
it is not required for per-scan CVE detection.

**SBOM + CVE/CWE export and lazy Gateway OSV enrichment** are owned by
[REQ-020](../REQ-020-unified-supply-chain-security/requirement.md) and
[ADR-072](../../adrs/ADR-072-unified-supply-chain-sbom-cve-cwe.md). REQ-020 does not
reintroduce the sidecar. Cross-project `/dep-health` views can consume the same
persisted vulnerability tables.

## References

- ADR-040: Scan Metadata Enrichment (prerequisite)
- ADR-038: Public Data API (Gateway API contract)
- ADR-020: Reporting service (event delivery)
- ADR-029: Web Gateway architecture
- ADR-051: Dependency Health Scanning (inline validators)
- ADR-072 / REQ-020: Unified supply chain SBOM/CVE/CWE export
- DR-002: SBOM generation (format decided via ADR-072)
- PR #93: Original REQ-010 proposal

---

## Change History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-25 | Brad (cidrblock) | Initial draft, replacing original REQ-010 (PR #93) |
| 2026-09-21 | Agent | Clarify ADR-051 inline path vs sidecar; point SBOM/CVE export to REQ-020 |
