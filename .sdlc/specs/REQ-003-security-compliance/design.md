# REQ-003: Design

## Architecture

See [architecture.md](../../context/architecture.md) for system design.

## SBOM Implementation (Phase 1 Complete)

### Module Structure

```
src/apme_engine/sbom/
    __init__.py      # Public API re-exports
    models.py        # CycloneDX 1.5 dataclasses (Bom, Component, Dependency, etc.)
    purl.py          # PURL generation with PEP 503 normalization
    validation.py    # Multi-error validation (advisory, never drops components)
```

### Design Decisions

- **Stdlib-only**: Zero external dependencies per ADR-014
- **Dataclasses**: `@dataclass` with `field(default_factory=...)` matching existing `engine/models.py` patterns
- **Enums**: `ComponentType(str, Enum)` following project convention
- **Sentinel values**: `"unversioned"` for missing versions, `"unknown"` for missing supplier/author — never silently drop components
- **Inferred names**: `mark_name_inferred()` annotates components with `apme:name-source=inferred-from-directory` property for auditability
- **Validation philosophy**: Collect all errors, never reject components — maximum inventory visibility

### Test Coverage

- 38 tests across `tests/sbom/` (models: 12, purl: 12, validation: 14)
- Tests run with: `PYTHONPATH=src pytest tests/sbom/ -v`

## Remaining Implementation (Future Phases)

- **Inventory Collection**: Discover installed collections, roles, Python packages
- **Serialization**: CycloneDX JSON output
- **CLI Integration**: `--sbom` and `--validate` flags
- **gRPC Integration**: SBOM generation via service API

## Key Components

- Gitleaks Integration
- SBOM Generator
- Custom Policy Engine (OPA/Rego)
