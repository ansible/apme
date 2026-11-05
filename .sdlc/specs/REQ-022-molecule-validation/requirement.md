# REQ-022: Static Molecule Validation

## Metadata

- **Phase**: PHASE-001 - CLI Scanner
- **Status**: Implemented
- **Created**: 2026-11-05

## Purpose

Automatically validate Molecule configuration, scenario references, action
sequences, and Ansible scenario playbooks during ordinary APME assessment.
Use L-series rules tagged `molecule`. The compatibility target is the Molecule
distribution delivered with Red Hat Ansible development tools; upstream
version numbers alone do not establish Red Hat support.

## User Stories

- As an automation developer, I want configuration and scenario errors reported
  before executing Molecule so that I can correct them during development.
- As an AAP user, I want diagnostics aligned with my Red Hat toolchain so that
  valid supported content is accepted.

## Acceptance Criteria

### Scenario: Automatic assessment

- **GIVEN**: A project containing Molecule scenarios
- **WHEN**: The user runs `apme check`
- **THEN**: Configuration, references, sequences, and Ansible scenario playbooks
  are assessed automatically with configurable, suppressible L-series findings.

### Scenario: Configuration inheritance

- **GIVEN**: Shared configuration and scenario overrides, including empty scenarios
- **WHEN**: APME validates their effective configuration
- **THEN**: Available layers and defaults are merged in the target version's order,
  and diagnostics identify the original file and line responsible for a value.

### Scenario: Missing or dynamic context

- **GIVEN**: A reference outside the scan, unresolved environment values, or a
  configuration layer that was not supplied
- **WHEN**: APME cannot determine conformance
- **THEN**: It reports incomplete validation without asserting a definite error
  in the unavailable content or substituting daemon environment values.

### Scenario: Independent configuration validation

- **GIVEN**: Invalid configuration with no successfully parsed Ansible graph
- **WHEN**: The project or an individual configuration is assessed
- **THEN**: Configuration diagnostics are still returned through FixSession.

### Scenario: Scenario playbooks

- **GIVEN**: Referenced Ansible playbooks, including import-only playbooks
- **WHEN**: APME assesses the scenario
- **THEN**: YAML and Ansible structure are validated and ordinary applicable
  playbook/task rules are retained; config files are not classified as plays.

### Scenario: No execution and deterministic results

- **GIVEN**: Molecule configuration containing dependency commands or plugins
- **WHEN**: APME validates it
- **THEN**: Validation reads the supplied snapshot and bundled schema only;
  it never invokes Molecule, dependency commands, dynamic inventory, or lookups.

## Inputs / Outputs

| Input | Description | Required |
|-------|-------------|----------|
| Uploaded project files | Existing ValidateRequest.files snapshot | Yes |
| ContentGraph | Existing Ansible graph for ordinary rules | No for config rules |
| Compatibility profile | Bundled schema and semantics tied to a verified release | Yes |

| Output | Description |
|--------|-------------|
| Violation | L rule, severity, source file/line, YAML path, and non-task scope |
| Validation coverage | Explicit notice when context prevents a check |

## Behavior

Discover role and collection layouts, including nested collection scenarios.
Parse configuration with duplicate-key detection, preserve source locations,
merge supplied shared layers, and resolve only statically known substitutions.
Validate shape, references, and action names independently. Inspect referenced
Ansible YAML and retain ordinary playbook validation coverage.

| Edge case | Handling |
|-----------|----------|
| Empty inherited scenario | Accept if effective configuration is valid |
| Optional lifecycle playbook | Do not require every stage in every scenario |
| Shared state / repeated verify | Respect target-version semantics |
| Malformed file | Report location and continue assessing other scenarios |
| Excluded reference / external path | Report unavailable context; obey scan boundaries |
| Unknown driver extension | Do not import it; state driver-specific coverage limit |
| Other verifier languages | Retain config checks; do not execute their tests |

## Dependencies

- REQ-001: Core Scanning Engine
- ADR-075: File validation in the Native service
- ADR-008, ADR-009, ADR-019, ADR-026, ADR-041, ADR-047, ADR-059
- Architectural compatibility: Verified; read-only validators, existing gRPC
  contract and pod topology, executor discipline, closed built-in bundles,
  Engine-owned venvs, stateless engine, dependency direction, and FixSession
  client path are preserved. Engine performs no external schema lookups.

## Non-Functional Requirements

- **Performance**: Cache trusted profile assets, bound YAML depth/traversal work,
  and deduplicate findings within each assessment.
- **Security**: Safe YAML, bounded inputs, no remote refs or host file access;
  error messages must not expose interpolated secrets.
- **Compatibility**: Versioned offline schema with provenance and update procedure;
  do not describe all upstream Molecule 6+ releases as Red Hat supported.

## Compatibility Decision

Use the publicly documented AAP 2.5 / Molecule 24.9.0 distribution as the initial
schema profile, under the user's instruction to select a verified Red Hat
baseline. This does not assert that an old package build remains the currently
supported patch release. Newer native configuration requires a verified target.

## References

- [ansible-lint issue 5119](https://github.com/ansible/ansible-lint/issues/5119)
- [Red Hat Molecule package reference](https://access.redhat.com/solutions/7101613)
- [Molecule configuration](https://docs.ansible.com/projects/molecule/configuration/)
- [Design](design.md), [Contract](contract.md)

## Change History

| Date | Author | Change |
|------|--------|--------|
| 2026-11-05 | Codex | Captured scope and user decisions; compatibility target pending |

| 2026-11-05 | Codex | Implemented L112-L116, graph/file integration, scenario discovery, and verified coverage |
