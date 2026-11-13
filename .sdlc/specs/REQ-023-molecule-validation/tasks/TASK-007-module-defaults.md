# TASK-007: Validate Arguments with Module Defaults

## Parent Requirement

[REQ-023: Static Molecule Validation](../requirement.md)

## Status

Complete

## Description

Close the argument-validation gap for scenario playbooks using Ansible module
defaults, including inherited scopes, resets, role invocations, and action groups.
Apply the same correction to ordinary playbook argument checks.

## Implementation Notes

- Preserve play defaults in the graph and serialize distinct ordered invocation
  contexts, including blocks, role declarations/dependencies, role/task imports,
  and include `apply` arguments. Separate executable role entry points from its
  file inventory. Bound context expansion and literal traversal.
- Use installed Ansible metadata for canonical module and group resolution,
  including historical group aliases and extended groups. Do not evaluate Jinja.
- Reuse prepared arguments in L058/L059 while retaining authored graph arguments
  for source attribution and remediation. Report incomplete context explicitly.
- Update rule documentation and the documented parity gap.
- Keep defaults-related findings as manual review; omit uploaded values from
  choice diagnostics. Reuse one executor-based context pass during rescans.
- Bound expanded JSON nodes and bytes across defaults serialization, contexts,
  and graph transport; report unsafe transport as an incomplete scan. Ignore
  inactive include paths when applying edge-specific context.
- Preserve distinct coverage and argument findings through the ledger, rescans,
  and output. Keep explicit manual-review findings out of remediation routing.
- Carry bounded assessment coverage independently of visible findings so a
  rescan does not mark unassessed arguments fixed, even when notices are filtered.
- Refresh role invocation selectors after edits, and apply supported Ansible type
  conversion before checking scalar and list choices.

## Test Cases

| Input | Expected Output |
|-------|-----------------|
| Required parameter supplied by defaults | No false missing-argument finding |
| Play/block/task and explicit argument overrides | Ansible precedence retained |
| Empty module entry | Earlier defaults cleared |
| Role/include/import invoked with different defaults | Contexts assessed separately |
| Fully qualified, historical, and extended action groups | Metadata membership respected |
| Unknown collection or templated mapping | Explicit coverage notice; no lookup execution |
| Unsupported inherited parameter | Ordinary argspec finding |

## Verification

- [x] Focused tests via `tox -e unit -- ... --no-cov`
- [x] `tox -e lint`
- [x] Full `tox -e unit` with local OPA and container-only skips: 5,891 passed,
      52 skipped, 87.18% coverage

## Completion Checklist

- [x] Implementation complete
- [x] All verification steps pass
- [x] Status updated to Complete
