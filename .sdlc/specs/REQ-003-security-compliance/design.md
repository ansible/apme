# REQ-003: Design

## Architecture

See [architecture.md](../../context/architecture.md) for system design.

## SBOM Implementation

### Module Structure

```
src/apme_engine/sbom/
    __init__.py              # Public API re-exports (models + collectors + serializer)
    models.py                # CycloneDX 1.5 dataclasses (Bom, Component, Dependency, LicenseChoice, etc.)
    purl.py                  # PURL generation with PEP 503 normalization
    validation.py            # Multi-error validation (advisory, never drops components)
    serializer.py            # CycloneDX 1.5 JSON serializer (bom_to_dict, bom_to_json)
    _yaml_subset.py          # Minimal stdlib-only YAML parser for galaxy.yml / meta/main.yml
    collect_collections.py   # Ansible collection inventory collector
    collect_packages.py      # Python package inventory collector
    collect_roles.py         # Ansible role inventory collector
```

### Phase 1 — Data Model (Complete)

- **Dataclasses**: `Bom`, `Component`, `Dependency` with `@dataclass` and `field(default_factory=...)`
- **Enums**: `ComponentType(str, Enum)` following project convention
- **PURL generation**: PEP 503 normalized identifiers for collections, roles, and Python packages
- **Multi-error validation**: Advisory validation collects all findings without rejecting components

### Phase 2 — Inventory Collection (Complete)

- **LicenseChoice model**: CDX-05 license metadata (SPDX ID or free-text name)
- **YAML subset parser**: Stdlib-only parser for `galaxy.yml` and `meta/main.yml` — handles strings, lists, nested mappings, comments, and multi-line values without external dependencies
- **Collection collector**: Walks `ansible_collections/` in venv site-packages with 3-tier metadata cascade (MANIFEST.json → galaxy.yml → directory inference). Maps collection-to-collection dependencies via PURL-keyed graph
- **Package collector**: Enumerates Python packages via `importlib.metadata` with infrastructure filtering (pip, setuptools, etc.). Extracts license metadata from METADATA files
- **Role collector**: Scans target directories for roles with `galaxy_info` parsing and bare role inference

### Design Decisions

- **Stdlib-only**: Zero external dependencies per ADR-014
- **Dataclasses**: `@dataclass` with `field(default_factory=...)` matching existing `engine/models.py` patterns
- **Enums**: `ComponentType(str, Enum)` following project convention
- **Sentinel values**: `"unversioned"` for missing versions, `"unknown"` for missing supplier/author — never silently drop components
- **Inferred names**: `mark_name_inferred()` annotates components with `apme:name-source=inferred-from-directory` property for auditability
- **Validation philosophy**: Collect all errors, never reject components — maximum inventory visibility
- **Never-raise collectors**: Collectors return partial results on error rather than raising — maximizes inventory visibility even with malformed metadata

### Phase 3 — Serialization & Schema Validation (Complete)

- **JSON serializer**: `bom_to_dict()` and `bom_to_json()` in `serializer.py` — explicit dict-builder approach converting internal data model to CycloneDX 1.5 spec-compliant JSON. Handles all CycloneDX camelCase mappings (bom-ref, bomFormat, specVersion, tools.components)
- **Schema validation**: Vendored official CycloneDX 1.5 JSON Schema (`bom-1.5.schema.json`, Draft-07) and SPDX schema in `tests/sbom/schemas/`. Integration tests prove serializer output is fully spec-compliant
- **TDD approach**: RED commit (failing tests) before GREEN commit (implementation)

### Test Coverage

- 143 tests across `tests/sbom/` (models: 18, purl: 12, validation: 14, yaml_subset: 12, collections: 20, packages: 28, roles: 11, serializer: 20, schema_validation: 8)
- Tests run with: `PYTHONPATH=src pytest tests/sbom/ -v`

## Remaining Implementation (Future Phases)

- **CLI Integration**: `--sbom` flag for BOM generation, `--validate` flag for validation reports
- **gRPC Integration**: SBOM generation via service API

## Key Components

- Gitleaks Integration
- SBOM Generator
- Custom Policy Engine (OPA/Rego)
