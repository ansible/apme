# Vendored CycloneDX Schemas

These schema files are vendored from the official CycloneDX specification
repository for use in schema validation integration tests.

## Files

| File | Description | Source |
|------|-------------|--------|
| bom-1.5.schema.json | CycloneDX 1.5 JSON Schema | [CycloneDX/specification](https://raw.githubusercontent.com/CycloneDX/specification/master/schema/bom-1.5.schema.json) |
| spdx.schema.json | SPDX License ID enum schema | [CycloneDX/specification](https://raw.githubusercontent.com/CycloneDX/specification/master/schema/spdx.schema.json) |

## Details

- **Specification version:** CycloneDX 1.5
- **JSON Schema draft:** Draft-07
- **Retrieved:** 2026-03-27
- **Purpose:** Test-only -- validates that serializer output is spec-compliant

These files are NOT used at runtime. They are only loaded by
`tests/sbom/test_schema_validation.py` to prove CycloneDX compliance.

## Updating

To update these schemas, re-download from the URLs above and verify
they are valid JSON. Ensure the bom schema version matches the
`specVersion` used in `src/apme_engine/sbom/models.py`.
