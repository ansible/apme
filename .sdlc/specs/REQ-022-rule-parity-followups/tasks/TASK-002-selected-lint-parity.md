# TASK-002: Add Selected ansible-lint Parity Checks

## Parent Requirement

REQ-022: Close Rule Parity and Risk Assessment Gaps

## Status

Complete

## Description

Add separate graph rules for unpinned Git/Hg revisions, complete role-name
format validation, and collection `galaxy.yml` version validity.

## Prerequisites

- TASK-001 should establish the updated rule documentation and runtime baseline.

## Implementation Notes

1. Inspect scanner graph fields and existing role/collection model data.
2. Assign unique L IDs and implement graph rules with narrow matching.
3. Add per-rule documentation and update the generated rule catalog.
4. Update the detailed review with exact coverage and remaining gaps.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/apme_engine/graph/rules/` | Modify | Add selected parity rules |
| `docs/rules/` | Modify | Document new rules and parity status |
| `src/apme_engine/graph/rules/rule_versions.json` | Modify | Register upstream lineage where applicable |

## Verification

- `tox -e lint`
- `tox -e unit`
- Manually check defaults, FQCN and short-name forms, valid and invalid role
  names, and collection version boundaries.

## Acceptance Criteria Reference

- REQ-022: selected lint gaps implemented under distinct IDs.

## Notes

Review validation added parameterized L112–L114 cases in
`tests/test_rule_parity_followups.py` for VCS defaults and collection search
order, role metadata/name precedence, and SemVer syntax and version boundaries.
All 50 cases in the review regression file pass through `tox -e unit`.
The rule catalog was regenerated to include this behavior coverage.
