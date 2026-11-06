# TASK-002: Validate Molecule Files

## Parent Requirement

[REQ-023: Static Molecule Validation](../requirement.md)

## Status

Complete

## Description

Implement L115-L119 against the pinned ADT configuration profile, including source locations, references, lifecycle actions, and Ansible playbook structure.

## Prerequisites

- User selected automatic L rules with a molecule tag and all four assessment areas.
- Initial compatibility profile: documented ADT/AAP 2.5 Molecule 24.9.0 contract.
- [ADR-076](../../../adrs/ADR-076-native-file-validation.md) preserves existing invariants.

## Implementation Notes

1. Bundle the 24.9.0 schema/defaults with immutable provenance and MIT attribution.
2. Implement safe configuration parsing, inheritance, static substitutions, and coverage notices.
3. Validate explicit references, active driver overrides, and supported sequence actions.
4. Cover malformed inputs, missing context, secrets, and independent scenarios.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/apme_engine/validators/native/molecule.py` | Create | Built-in file assessment |
| `src/apme_engine/validators/native/molecule_data/` | Create | Offline schema/defaults and provenance |
| `src/apme_engine/validators/native/rules/L115.md through L119.md` | Create | Rule documentation |
| `tests/test_molecule_validation.py` | Create | Behavioral coverage |
| `VENDORED.md` | Modify | Attribution and dependency review |

## Verification

- [x] `tox -e lint`
- [x] `tox -e unit`

## Acceptance Criteria Reference

REQ-023: automatic assessment, inherited configuration, missing context, independent configuration validation, scenario playbooks, and deterministic static behavior.

## Completion Checklist

- [x] Implementation complete
- [x] Required checks pass
- [x] Status updated
