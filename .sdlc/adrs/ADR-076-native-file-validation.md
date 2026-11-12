# ADR-076: File Validation in the Native Service

## Status

Implemented

## Date

2026-11-05

## Context

Molecule configuration requires static validation even when no Ansible playbook
graph exists. Native currently evaluates only GraphRule instances, although its
existing ValidateRequest already carries uploaded files. Triggering configuration
checks from an arbitrary playbook node misses configuration-only inputs and
couples file coverage to graph topology.

## Decision

We will assess built-in file rules in the Native validator alongside graph rules
using the existing ValidateRequest.files snapshot. File and graph passes share
the executor, diagnostics, catalog, and ordinary severity/suppression handling.
No additional service, protocol field, network lookup, or runtime plugin loading
is required. Configuration validation is independent of graph availability.

This decision preserves all AGENTS.md invariants and ADR-059's graph/engine
dependency boundary. It does not amend the read-only validator contract.

## Alternatives Considered

### Alternative 1: Trigger file scans from GraphRule playbook nodes

**Pros**: Reuses graph discovery unchanged.

**Cons**: Empty graphs cannot trigger checks; repeated nodes require deduplication;
incremental node hints do not describe changes to shared configuration.

**Why not chosen**: Coverage depends on unrelated valid Ansible content.

### Alternative 2: Dedicated Molecule validator service

**Pros**: Independent packaging and diagnostics.

**Cons**: Adds daemon/container wiring for pure YAML and file checks that Native
can perform using an existing contract.

**Why not chosen**: Additional service lifecycle is unnecessary for static rules.

## Consequences

- Configuration-only input can produce ordinary Native findings.
- File rules require explicit catalog and documentation discovery.
- File-level findings need meaningful non-task scope and source provenance.
- Engine handling of missing hierarchy and graph-only rescans must be audited.
- Schema assets ship with the built-in bundle; scans perform no schema downloads.

## Implementation Notes

REQ-023 defines a Molecule 24.9.0+ minimum compatibility target, with pinned
legacy (24.9.0) and native/shared-state (26.11.0) profiles.
The built-in driver schemas supplement the core schemas. Molecule scenario
playbooks and their local imports receive inert structural checks rather than
L057 runtime dispatch, which can evaluate lookups. Ansible's runtime classifier
reuses the pure snapshot assessment helper to exclude resolved shared/imported
targets outside scenario directories as well. File findings retain original source excerpts for
suppression and survive graph-only remediation without entering its ledger.
Add tests for independent file validation and preserve graph rule behavior.

Scenario playbook structure checks include `module_defaults` shapes. Existing
Ansible argument checks consume separate ordered defaults contexts carried in
the hierarchy JSON, preserving authored task arguments. Installed Ansible
metadata resolves aliases and action groups without templating or executing
scenario content. Unknown or bounded-out contexts produce coverage notices.
This extends ordinary argument validation as well as scenario coverage without
changing service boundaries, transport fields, or the graph library dependency.

## Related Decisions

- ADR-008: Rule IDs are independent of validator implementation.
- ADR-009: Validators detect; remediation is separate.
- ADR-019: Justify any schema-validation runtime dependency.
- ADR-026: Rule scope controls remediation routing.
- ADR-041: Catalog registration and overrides.
- ADR-047: Verification through tox.
- ADR-059: Shared graph library boundary.

## References

- [REQ-023](../specs/REQ-023-molecule-validation/requirement.md)

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-11-05 | Codex | Proposed file validation design for Molecule rules |
| 2026-11-05 | Codex | Accepted within the user-authorized static validation scope; no invariant changes |
| 2026-11-05 | Codex | Implemented and verified file rules, catalog integration, and session reporting |
| 2026-11-06 | Codex | Extended configuration compatibility to 24.9.0+ with native schema and source-preserving migration |
| 2026-11-06 | Codex | Hardened static playbook dispatch, snapshot completeness, source suppression, YAML expansion/merges, and approval reporting following independent review |
| 2026-11-12 | Codex | Distinguished collection imports and symbolic runtime paths from missing local files; clarified optional-stage scenarios, bounded source-map merging, and limited inline suppression to its diagnostic line |
| 2026-11-12 | Codex | Added inert module-defaults structure checks and context-aware Ansible argument validation for inherited defaults, resets, and action groups |
