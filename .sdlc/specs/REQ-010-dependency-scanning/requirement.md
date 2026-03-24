# REQ-010: Dependency & Collection Security Scanning

## Metadata

- **Phase**: PHASE-003 - Enterprise Dashboard
- **Status**: Draft
- **Created**: 2026-03-24

## Overview

Extends APME's scanning capabilities to cover the dependency layer: Ansible collections used by playbooks and the Python packages required to make the whole environment work. Performs ARI-based security analysis on collection dependencies and vulnerability scanning (CVE lookup) on Python dependencies. Provides both a backend scanning engine and UI components for viewing and managing dependency health.

## User Stories

**As a DevOps Engineer**, I want to know which Ansible collections my playbooks depend on and whether those collections have known security issues so that I can avoid deploying vulnerable automation.

**As a Security Engineer**, I want Python dependency vulnerability scanning so that I can ensure the AAP execution environment doesn't include packages with known CVEs.

**As an Automation Architect**, I want a dependency graph view so that I can understand the full transitive dependency tree of my automation project.

**As a Platform Admin**, I want to enforce dependency policies (e.g., "no collections older than 6 months") so that teams stay on supported versions.

**As a CI Pipeline Operator**, I want dependency scans as part of the scan pipeline so that vulnerable dependencies block deployment alongside code violations.

## Acceptance Criteria

### Collection Dependency Discovery
- **GIVEN** a project with playbooks using FQCNs
- **WHEN** a dependency scan runs
- **THEN** all referenced Ansible collections are identified with their versions (from `requirements.yml`, Galaxy metadata, or FQCN auto-discovery)

### Collection Security Scanning (ARI-based)
- **GIVEN** a discovered collection dependency
- **WHEN** the collection is analyzed by ARI
- **THEN** security issues within the collection code are detected (hardcoded secrets, unsafe practices, deprecated patterns)
- **AND** results are attributed to the collection, not the consuming playbook

### Python Dependency Scanning
- **GIVEN** a project's execution environment (EE) definition or `requirements.txt`
- **WHEN** a Python dependency scan runs
- **THEN** all Python packages are enumerated with versions
- **AND** each package is checked against a vulnerability database (e.g., OSV, NVD, pip-audit)

### CVE Reporting
- **GIVEN** a Python dependency with a known CVE
- **WHEN** scan results are displayed
- **THEN** the CVE ID, severity (CVSS), affected versions, and fix version (if available) are shown

### Dependency Health Dashboard (UI)
- **GIVEN** a project with dependency scan results
- **WHEN** the user views the Dependencies tab in the project UI
- **THEN** they see: collection list with health status, Python package list with vulnerability status, dependency graph visualization

### Transitive Dependency Analysis
- **GIVEN** a collection that depends on other collections or Python packages
- **WHEN** transitive dependency analysis runs
- **THEN** the full dependency tree is resolved and scanned (not just direct dependencies)

### Dependency Policy Enforcement
- **GIVEN** a configured policy (e.g., "no collections with Critical CVEs")
- **WHEN** a scan completes
- **THEN** policy violations are reported as P-category violations alongside code violations

### Integration with Existing Scan Pipeline
- **GIVEN** a scan request via CLI or UI
- **WHEN** dependency scanning is enabled (opt-in or default)
- **THEN** dependency results are included in the `ScanResponse` alongside code violations
- **AND** diagnostics include dependency scan timing

## Inputs / Outputs

### Inputs

| Name | Type | Description | Required |
|------|------|-------------|----------|
| requirements_yml | File | Ansible collection requirements | If available |
| requirements_txt | File | Python package requirements | If available |
| ee_definition | File | Execution Environment definition (EE) | If available |
| discovered_fqcns | list[string] | FQCNs from code scanning | Auto-generated |
| vuln_db_source | enum | osv, nvd, pip_audit | No (default: osv) |

### Outputs

| Name | Type | Description |
|------|------|-------------|
| collection_results | list[CollectionScanResult] | Per-collection ARI scan findings |
| python_results | list[PythonPackageResult] | Per-package vulnerability findings |
| dependency_graph | DependencyGraph | Full transitive dependency tree |
| policy_violations | list[Violation] | Dependency policy violations (P-category) |

## Behavior

### Happy Path — Collection Scanning

1. Scanner discovers collection dependencies (from `requirements.yml` + FQCN auto-discovery)
2. Galaxy Proxy resolves collection versions and downloads tarballs
3. ARI engine scans each collection's code for security issues
4. Results are attributed to the collection (not the consuming project)
5. Collection health is summarized in the UI Dependencies tab

### Happy Path — Python Dependency Scanning

1. Scanner discovers Python requirements from EE definition, `requirements.txt`, or collection metadata
2. Packages are enumerated with pinned versions (resolved via `uv pip compile` or similar)
3. Each package+version is checked against vulnerability database
4. CVE matches are reported with severity, affected range, and fix version
5. Results appear in the UI Dependencies tab under "Python Packages"

### Dependency Graph

```
Project
├── ansible.builtin (2.16.0) ✓
├── community.general (8.0.0) ⚠ 1 advisory
│   ├── python: jmespath (1.0.1) ✓
│   └── python: ncclient (0.6.15) ✗ CVE-2023-XXXX
├── amazon.aws (7.1.0) ✓
│   └── python: boto3 (1.34.0) ✓
└── Python Environment
    ├── ansible-core (2.16.3) ✓
    ├── jinja2 (3.1.3) ✓
    └── cryptography (42.0.0) ⚠ CVE-2024-YYYY
```

### Edge Cases

| Case | Handling |
|------|----------|
| Collection not on Galaxy | Skip collection scan; warn "Collection not found on Galaxy" |
| No requirements.yml | Rely on FQCN auto-discovery only |
| No Python requirements | Skip Python scan; report "No Python requirements found" |
| Pinned version not in vuln DB | Mark as "Unknown" status; don't flag as vulnerable |
| Private/internal collections | Support custom Galaxy server URL for resolution |
| Circular dependencies | Detect and report cycle; scan each node once |

### Error Conditions

| Error | Cause | Response |
|-------|-------|----------|
| Galaxy unreachable | Network issue or Galaxy downtime | Use cached data if available; warn about staleness |
| Vulnerability DB unreachable | OSV/NVD API down | Use cached data; warn about staleness |
| Collection download fails | Auth required or tarball corrupt | Skip collection; report error in results |
| ARI scan fails on collection | Incompatible collection format | Report as scan error; don't block overall scan |

## Dependencies

### Internal

- REQ-001: Core Scanning Engine (FQCN auto-discovery, scan pipeline integration)
- REQ-003: Security & Compliance (security policy enforcement)
- REQ-005: Rule Rating & Severity (severity for dependency violations)
- REQ-009: Project-Centric UI (Dependencies tab in project view)

### External

- Galaxy Proxy (already in architecture — collection resolution)
- ARI engine (vendored — collection code scanning)
- Vulnerability database API (OSV.dev, NVD, or pip-audit)
- `uv` or `pip-audit` for Python dependency resolution and auditing

## Non-Functional Requirements

- **Performance**: Dependency scan should complete within 60 seconds for projects with <50 dependencies; parallelized collection scanning
- **Caching**: Collection scan results cached by collection+version (immutable); vulnerability data cached with 1-hour TTL
- **Security**: Vulnerability database queries must not leak project dependency information (use batch API or local mirror)
- **Compatibility**: Support `requirements.yml` (Ansible Galaxy), `requirements.txt` (pip), and EE definition format

## Open Questions

- [ ] Should collection scanning be a new validator container or extend the existing Native validator?
- [ ] Should we use OSV.dev API, pip-audit, or maintain a local vulnerability mirror?
- [ ] How do we handle collections from private/internal Galaxy servers?
- [ ] Should dependency scan results generate their own rule IDs (e.g., `DEP:` prefix) or use existing categories?
- [ ] Should we generate an SBOM (Software Bill of Materials) as an output format?

## References

- Architecture: Galaxy Proxy (PEP 503 collection resolution)
- ADR-003: Vendored ARI Engine (collection scanning capability)
- REQ-001: Core Scanning Engine (FQCN auto-discovery)
- REQ-003: Security & Compliance

---

## Change History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-24 | APME Team | Initial draft |
