# REQ-022: Close Rule Parity and Risk Assessment Gaps

## Metadata

- **Phase**: PHASE-001 - CLI Scanner
- **Status**: Implemented
- **Created**: 2026-11-05

## Purpose

Close the highest-impact gaps found in APME's source comparison with
ansible-lint and Ansible Risk Insights (ARI). APME should report unresolved
modules based on Ansible plugin resolution, make its inbound-transfer finding
operational, and describe remaining lint and risk coverage accurately. Selected
low-complexity lint gaps will be implemented; larger schema, YAML-formatting,
and sanity-ignore-file gaps will be explicitly deferred with scope recorded.

## User Stories

- As an Ansible developer, I want misspelled or unavailable modules reported
  without flagging valid short module names, so that module errors are found
  before execution.
- As a security reviewer, I want inbound-transfer findings to appear from the
  active scan pipeline, so that the documented risk check is usable.
- As a rule maintainer, I want the coverage map and per-rule documentation to
  reflect actual behavior, so that users can distinguish parity from overlap.

## Acceptance Criteria

### Scenario: Resolve task modules semantically

- **GIVEN** a task using an unknown module name
- **WHEN** the Ansible validator resolves task modules
- **THEN** APME reports `L037` with the module and source location
- **AND** valid short names that resolve through ansible-core do not produce
  `L037`
- **AND** resolution honors the task's in-scope `collections` search order and
  checks action plugins as well as module plugins
- **AND** project-level Ansible plugin paths configured from the scan root are
  available to resolution
- **AND** removed modules retain the `M004` result instead of a duplicate
  unresolved-module result

### Scenario: Report inbound transfers in normal scans

- **GIVEN** a task covered by APME's inbound risk profiles
- **WHEN** the graph is serialized for OPA
- **THEN** the R118 input contains a risk annotation derived from the task's
  module and options
- **AND** local `unarchive` sources do not become inbound-transfer findings
- **AND** templated unarchive sources are treated as potentially remote when
  `remote_src` is true

### Scenario: Make rule documentation match implementation

- **GIVEN** the argspec, Galaxy metadata, lint mapping, and ARI risk rules
- **WHEN** users read the rule documentation
- **THEN** it distinguishes implemented checks from partial parity and
  documented deferrals
- **AND** it does not claim type checks or conditional argument validation
  that L058/L059 do not perform

### Scenario: Close selected lint gaps

- **GIVEN** Git/Hg checkout tasks, role directories, and collection metadata
- **WHEN** APME scans them
- **THEN** it checks unpinned VCS revisions, invalid role-name format, and
  collection versions for valid SemVer at least 1.0.0 under distinct rule IDs
- **AND** remaining schema, YAML-formatting, and sanity-ignore-file gaps are
  listed as deferred coverage

## Dependencies

### Internal

- REQ-001: Core Scanning Engine
- ADR-003: Vendored ARI engine
- ADR-008: Rule ID conventions
- ADR-009: Validators are read-only

### External

- `ansible-core` plugin loader in the session venv: semantic module resolution.

### Architectural compatibility

Verified: validators remain read-only, use the existing Ansible validator
subprocess, and do not add outbound engine calls or third-party rule loading.

## Non-Functional Requirements

- **Performance**: Reuse the existing cached plugin introspection pass.
- **Security**: Do not execute playbook actions; the existing L059 module
  introspection boundary remains unchanged.
- **Compatibility**: Preserve existing rule IDs and output fields where
  possible; new checks use unused L-rule IDs.

## Security Considerations

N/A — no new external input or network surface. Module names and task options
are already part of scan input.

## Open Questions

None. Schema, YAML-formatting, and sanity-ignore-file parity are out of scope
for this delivery and will remain documented gaps.

## References

- [APME ansible-lint and ARI review](../../../docs/rules/APME_ANSIBLE_LINT_ARI_REVIEW.md)
- [ansible-lint v26.10.1 source](https://github.com/ansible/ansible-lint/tree/v26.10.1/src/ansiblelint/rules)
- [ARI rule source](https://github.com/ansible/ansible-risk-insight/tree/18cbf07185a4b9cf209b40b470a92ac2393db1b6/ansible_risk_insight/rules)

## Change History

| Date | Author | Change |
|------|--------|--------|
| 2026-11-05 | Codex | Initial requirement from the rule-coverage review |
