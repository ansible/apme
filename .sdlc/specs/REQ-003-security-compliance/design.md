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

### Phase 4 — gRPC & CLI Integration (Complete)

- **GenerateSbom RPC**: Client-streaming gRPC RPC on Primary service. Receives file chunks, materializes to temp directory, resolves session venv, runs all three collectors in executor, serializes BOM, returns `SbomResponse` with JSON bytes and component summary
- **Summary-only mode**: `summary_only` flag on `ScanOptions` re-queries existing session inventory without file upload. Always renders verbose grouped table
- **Proto messages**: `SbomResponse` (sbom_json, collection_count, package_count, role_count, total_count, components) and `SbomComponentDetail` (type, name, version, license, name_inferred, version_missing)
- **CLI subcommand**: `apme sbom [target]` with `--output`/`-o`, `--session`, `--summary`, `--refresh`, `-v` flags
- **Summary rendering**: Counts-only one-liner (default) or verbose grouped table (Name/Version/License per component type) with `[inferred]` and version-missing flags
- **gRPC client integration**: Reuses `yield_scan_chunks` and `_resolve_session_id` from check module. Summary to stderr when no `--output` (keeps stdout clean for piping)

### Test Coverage

- 167 tests total: 143 across `tests/sbom/` (models: 18, purl: 12, validation: 14, yaml_subset: 12, collections: 20, packages: 28, roles: 11, serializer: 20, schema_validation: 8), 11 in `tests/test_sbom_grpc.py`, 13 in `tests/test_sbom_cli.py`
- Tests run with: `PYTHONPATH=src pytest tests/sbom/ tests/test_sbom_grpc.py tests/test_sbom_cli.py -v`

## Remaining Implementation (Future Phases)

- **`--validate` flag**: Validation report output for SBOM quality checks

## Key Components

- Gitleaks Integration
- SBOM Generator
- Custom Policy Engine (OPA/Rego)
