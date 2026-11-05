# TASK-004: Register and Package Molecule Rules

## Parent Requirement

[REQ-022: Static Molecule Validation](../requirement.md)

## Status

Complete

## Description

Include the automatic Molecule lint rules in runtime and generated catalogs, package their schema assets, and document compatibility.

## Prerequisites

- User selected automatic L rules with a molecule tag and all four assessment areas.
- Initial compatibility profile: documented ADT/AAP 2.5 Molecule 24.9.0 contract.
- [ADR-075](../../../adrs/ADR-075-native-file-validation.md) preserves existing invariants.

## Implementation Notes

1. Register file-rule metadata, severity defaults, documentation, and rule versions.
2. Teach catalog generation to discover file rules separately from graph rules.
3. Declare and lock the schema validator dependency and type coverage.
4. Document the Red Hat profile, limits, rule overrides, and schema updates.
5. Run lint/type checks and the full unit suite through tox.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/apme_engine/rule_catalog.py` | Modify | Runtime catalog and documentation |
| `tools/generate_rule_catalog.py` | Modify | Generated catalog |
| `src/apme_engine/graph/severity.py` | Modify | Warning-level defaults |
| `pyproject.toml, uv.lock, .pre-commit-config.yaml` | Modify | Dependency and package data |
| `docs/guides/MOLECULE.md and docs/rules/` | Modify | User guide and catalog references |

## Verification

- [x] `tox -e lint`
- [x] `tox -e unit`

## Acceptance Criteria Reference

REQ-022: automatic assessment, inherited configuration, missing context, independent configuration validation, scenario playbooks, and deterministic static behavior.

## Completion Checklist

- [x] Implementation complete
- [x] Required checks pass
- [x] Status updated

## Verification Notes

The full suite passed 5,091 tests with 51 skipped and 85.69% coverage using
local OPA 1.17.1 (matching the container pin). The documented container check
was skipped because service images are unavailable in this workspace.

```bash
OPA_USE_PODMAN=0 APME_SKIP_PODMAN=1 tox -e unit -x 'testenv:unit.pass_env+=OPA_USE_PODMAN,APME_SKIP_PODMAN'
```

A final malformed-input regression and role/collection discovery checks passed
in the focused 118-test run. `tox -e lint` checks formatting, types, docstrings,
lockfile, catalogs, and architectural boundaries.
