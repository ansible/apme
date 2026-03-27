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

### Serialization Contract

| Function | Module | Purpose |
|----------|--------|---------|
| `bom_to_dict()` | `serializer.py` | Convert Bom dataclass to CycloneDX 1.5 spec-compliant dict |
| `bom_to_json()` | `serializer.py` | Convert Bom dataclass to CycloneDX 1.5 JSON string (pretty-printed) |

- Output conforms to CycloneDX 1.5 JSON Schema (Draft-07)
- CamelCase field mapping: `bom_ref` → `bom-ref`, `spec_version` → `specVersion`, `bom_format` → `bomFormat`
- Empty/None fields omitted from output
- Schema validation via vendored `tests/sbom/schemas/bom-1.5.schema.json`

### gRPC Contract

| RPC | Service | Type | Purpose |
|-----|---------|------|---------|
| `GenerateSbom` | `Primary` | client-streaming | Stream file chunks, return SBOM JSON + component summary |

#### SbomResponse Message

| Field | Type | Description |
|-------|------|-------------|
| `sbom_json` | `bytes` | CycloneDX 1.5 JSON document |
| `collection_count` | `int32` | Number of Ansible collections discovered |
| `package_count` | `int32` | Number of Python packages discovered |
| `role_count` | `int32` | Number of Ansible roles discovered |
| `total_count` | `int32` | Total component count |
| `components` | `repeated SbomComponentDetail` | Per-component detail for summary rendering |

#### SbomComponentDetail Message

| Field | Type | Description |
|-------|------|-------------|
| `type` | `string` | Component type: "collection", "package", "role" |
| `name` | `string` | Component name |
| `version` | `string` | Version string (empty if missing) |
| `license` | `string` | License identifier or name |
| `name_inferred` | `bool` | True if name was inferred from directory |
| `version_missing` | `bool` | True if no version metadata found |

#### ScanOptions Extensions

| Field | Type | Description |
|-------|------|-------------|
| `summary_only` | `bool` | Re-query existing session inventory without file upload |
| `refresh` | `bool` | Force re-discovery even when session venv exists |

### CLI Contract

| Command | Description |
|---------|-------------|
| `apme sbom [target]` | Generate CycloneDX 1.5 SBOM (default: stdout) |
| `apme sbom --output FILE` | Write SBOM JSON to file |
| `apme sbom --summary --session ID` | Show summary of existing session (no upload) |
| `apme sbom --refresh` | Force re-discovery of inventory |
| `apme sbom -v` | Verbose grouped table instead of counts-only |

## Policy Rule Format

TBD - Define Rego rule structure for custom policies.
