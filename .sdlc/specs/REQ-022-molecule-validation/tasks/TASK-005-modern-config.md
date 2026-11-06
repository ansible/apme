# TASK-005: Support Molecule 24.9.0+ Configuration

## Parent Requirement

[REQ-022](../requirement.md)

## Status

Complete

## Description

Extend automatic L112-L116 checks to legacy and modern native configuration,
using pinned offline profiles and preserving source attribution after migration.

## Prerequisites

- User selected 24.9.0+ as the compatibility assumption on 2026-11-06.
- Existing static Molecule rules and Native integration.

## Implementation Notes

1. Bundle the released 26.11.0 schema/defaults with hash, commit, and attribution.
2. Select the configuration family after merging supplied layers, then apply
   its defaults and upstream legacy-to-native migration precedence.
3. Check native playbook references and structures without changing legacy behavior.
4. Cover the original issue example, inherited native config, malformed executor
   fields, shared state, mixed inputs, and source locations in regression tests.
5. Update the guide and specs to describe 24.9.0+ as the minimum compatibility
   target and distinguish syntax compatibility from vendor support.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `validators/native/molecule.py` | Modify | Profile selection and native references |
| `validators/native/molecule_data/` | Add | Pinned modern schema/defaults |
| `tests/test_molecule_validation.py` | Modify | Legacy/native regression coverage |
| `docs/guides/MOLECULE.md` and rule docs | Modify | Compatibility and coverage |
| `VENDORED.md` | Modify | Source provenance |

## Verification

- [x] `tox -e lint`
- [x] `tox -e unit`

## Acceptance Criteria Reference

REQ-022: Modern native configuration, automatic assessment, inheritance,
references, sequences, and static playbook checks.

## Completion Checklist

- [x] Implementation complete
- [x] Verification complete
- [x] Status updated

## Verification Results

`tox -e lint` passed. The full unit suite passed 5,112 tests with 51 skipped,
85.72% coverage, local OPA 1.17.1, and the documented container-check skip:

```bash
OPA_USE_PODMAN=0 APME_SKIP_PODMAN=1 tox -e unit -x 'testenv:unit.pass_env+=OPA_USE_PODMAN,APME_SKIP_PODMAN'
```

Both modern JSON assets were verified in the built source package. The original
issue #5119 example passes without executing Molecule. Regression coverage also
checks legacy behavior, inherited native settings, executor schema, mixed input
migration/locations, missing references, unknown context, and playbook structure.
