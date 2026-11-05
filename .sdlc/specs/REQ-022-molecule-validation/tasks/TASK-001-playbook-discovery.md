# TASK-001: Discover Scenario Playbooks Structurally

## Parent Requirement

[REQ-022: Static Molecule Validation](../requirement.md)

## Status

Complete

## Description

Close the existing L057 discovery gap for scenario playbooks consisting solely
of import_playbook, and avoid sending configuration or inventory mappings to
ansible-playbook because they contain a nested hosts/tasks key. Include
collection scenario playbooks under extensions/molecule in ordinary graph
loading when test content is enabled.

## Prerequisites

- User selected scenario playbook validation as part of the feature scope.
- This discovery correction is independent of the pending Molecule version profile.

## Implementation Notes

1. Inspect YAML document structure using a parser that does not execute tags.
2. Recognize bare and FQCN import_playbook alongside ordinary plays.
3. Retain malformed playbook candidates so L057 can report their syntax errors.
4. Cover classification and subprocess dispatch with regression fixtures.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/apme_engine/validators/ansible/rules/L057_syntax.py` | Modify | Structural discovery |
| `src/apme_engine/engine/model_loader.py` | Modify | Collection scenario graph discovery |
| `tests/test_L057_molecule_playbooks.py` | Create | Scenario/config regression cases |

## Test Cases

| Input | Expected Output |
|-------|-----------------|
| Bare or FQCN import-only playbook | Included in syntax assessment |
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

This task improves static syntax-check discovery. It does not invoke Molecule
or establish that any upstream release is Red Hat supported.
