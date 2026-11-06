# TASK-001: Discover Scenario Playbooks Structurally

## Parent Requirement

[REQ-022: Static Molecule Validation](../requirement.md)

## Status

Complete

## Description

Close the static assessment gap for scenario playbooks consisting solely
of import_playbook. Keep Molecule scenario content out of runtime syntax checks,
which may evaluate lookups and require execution context. Avoid sending
configuration or inventory mappings to ansible-playbook merely because they
contain a nested hosts/tasks key. Include
collection scenario playbooks under extensions/molecule in ordinary graph
loading when test content is enabled.

## Prerequisites

- User selected scenario playbook validation as part of the feature scope.
- This discovery correction is independent of the pending Molecule version profile.

## Implementation Notes

1. Inspect YAML document structure using a parser that does not execute tags.
2. Recognize bare and FQCN import_playbook in Native's static playbook checks.
3. Retain ordinary malformed playbook candidates for L057 and keep scenario
   content in Native's inert structural checks.
4. Cover classification, ordinary subprocess dispatch, and scenario non-execution.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/apme_engine/validators/ansible/rules/L057_syntax.py` | Modify | Structural discovery |
| `src/apme_engine/engine/model_loader.py` | Modify | Collection scenario graph discovery |
| `tests/test_L057_molecule_playbooks.py` | Create | Scenario/config regression cases |

## Test Cases

| Input | Expected Output |
|-------|-----------------|
| Bare or FQCN import-only scenario playbook | Static Native assessment without execution |
| Molecule config with nested hosts/tasks | Excluded as config |
| Inventory or vars mapping | Excluded as data |
| Malformed playbook with play markers | Retained for syntax diagnostics |
| Ansible YAML tags | Parsed as data without tag execution |

## Verification

- [x] `tox -e unit -- tests/test_L057_molecule_playbooks.py --no-cov`
- [x] `tox -e lint`

## Acceptance Criteria Reference

- REQ-022: Scenario playbooks, including import-only playbooks.

## Completion Checklist

- [x] Implementation complete
- [x] Relevant checks pass
- [x] Status updated

## Notes

This task improves static scenario assessment and runtime discovery. It does not invoke Molecule
or establish that any upstream release is Red Hat supported.
