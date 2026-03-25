# REQ-003: Contract

## SBOM Format

CycloneDX 1.5 (JSON). Implemented in `src/apme_engine/sbom/`.

### Data Model Types

| Type | Module | Purpose |
|------|--------|---------|
| `Bom` | `models.py` | Root BOM object (format, spec version, serial number, components, dependencies) |
| `Component` | `models.py` | Software dependency (type, name, version, purl, bom_ref, supplier, author) |
| `Dependency` | `models.py` | Dependency relationship (ref + depends_on list) |
| `BomMetadata` | `models.py` | BOM metadata (timestamp, authors, tool info) |
| `ComponentType` | `models.py` | Enum: application, framework, library, container, firmware, file |
| `OrganizationalEntity` | `models.py` | Supplier/manufacturer entity (name, urls) |
| `Property` | `models.py` | Key-value annotation on components |

### PURL Conventions

| Content Type | PURL Format | Example |
|-------------|-------------|---------|
| Ansible Collection | `pkg:generic/{ns}.{name}@{version}?repository_url=https://galaxy.ansible.com` | `pkg:generic/cisco.ios@2.0?repository_url=https://galaxy.ansible.com` |
| Ansible Role | `pkg:generic/{name}@{version}?repository_url=https://galaxy.ansible.com` | `pkg:generic/geerlingguy.docker@6.1.0?repository_url=https://galaxy.ansible.com` |
| Python Package | `pkg:pypi/{pep503_name}@{version}` | `pkg:pypi/ruamel-yaml@0.18.0` |

- Python package names are PEP 503 normalized (lowercased, hyphens only)
- Collection PURLs use dot-joined `namespace.name` (not slash-separated)
- All Galaxy content includes `repository_url` qualifier
- Unversioned components use `"unversioned"` sentinel
- Missing supplier/author use `"unknown"` placeholder

### Validation Contract

| Type | Module | Purpose |
|------|--------|---------|
| `ValidationError` | `validation.py` | Single finding (component_name, field, severity, message, suggestion) |
| `ValidationResult` | `validation.py` | Aggregated findings with `is_valid` property |
| `validate_component()` | `validation.py` | Validate one component against NTIA minimums |
| `validate_bom()` | `validation.py` | Validate all components + check duplicate bom_refs |

- Severity levels: `"error"` (required fields) and `"warning"` (recommended fields)
- Advisory only — invalid components stay in the BOM
- Multi-error collection — all findings gathered, no fail-fast
- Every finding includes an actionable `suggestion`

## Policy Rule Format

TBD - Define Rego rule structure for custom policies.
