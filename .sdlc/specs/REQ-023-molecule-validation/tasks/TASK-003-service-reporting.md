# TASK-003: Integrate File Assessment and Reporting

## Parent Requirement

[REQ-023: Static Molecule Validation](../requirement.md)

## Status

Complete

## Description

Run file checks through Native Validate independently of graph data and retain file findings through assessment and approval reporting.

## Prerequisites

- User selected automatic L rules with a molecule tag and all four assessment areas.
- Initial compatibility profile: documented ADT/AAP 2.5 Molecule 24.9.0 contract.
- [ADR-075](../../../adrs/ADR-075-native-file-validation.md) preserves existing invariants.

## Implementation Notes

1. Run file and graph assessment in the existing executor and RPC.
2. Keep validators reachable when project loading has no hierarchy.
3. Preserve file findings across graph convergence and approval gates.
4. Cover graph-free RPC adaptation and the real assessment workflow.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/apme_engine/daemon/native_validator_server.py` | Modify | Native file pass |
| `src/apme_engine/daemon/engine_server.py` | Modify | File coverage and reporting |
| `src/apme_engine/daemon/session.py` | Modify | File finding lifecycle |
| `tests/test_molecule_validation.py` | Modify | RPC and reporting regression coverage |

## Verification

- [x] `tox -e lint`
- [x] `tox -e unit`

## Acceptance Criteria Reference

REQ-023: automatic assessment, inherited configuration, missing context, independent configuration validation, scenario playbooks, and deterministic static behavior.

## Completion Checklist

- [x] Implementation complete
- [x] Required checks pass
- [x] Status updated
