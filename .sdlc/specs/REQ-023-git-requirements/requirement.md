# REQ-023: Git collection and role requirements

## Status

Implemented

## Purpose

Install Git-hosted Ansible dependencies for gateway project checks and remediation,
with a deployment-wide opt-in and optional project requirements credential.

## User Stories

- As an administrator, I can enable Git requirements globally alongside Galaxy access.
- As a project owner, I can use my project Git credential by default and configure
  a separate credential for dependency repositories.

## Acceptance Criteria

### Scenario: Global policy

- **GIVEN** Git requirements are disabled by default
- **WHEN** a project declares a Git requirement
- **THEN** the gateway fails the operation before fetching that dependency
- **AND** enabling the policy exposes the project requirements credential control

### Scenario: Credential selection

- **GIVEN** a Git requirement and the policy enabled
- **WHEN** the gateway fetches it
- **THEN** an explicit requirements credential overrides the project credential
- **AND** otherwise the project credential is used, followed by the existing deployment fallback
- **AND** only credential-presence flags are returned by project APIs

### Scenario: Dependency use

- **GIVEN** HTTPS collection or role Git requirements with a branch, tag or SHA
- **WHEN** a project check or remediation starts
- **THEN** the gateway fetches the dependency and transfers files over gRPC
- **AND** the Engine installs the snapshot without external queries
- **AND** dependency files are excluded from project edits and returned patches

## Inputs / Outputs

Inputs: standard Ansible requirements.yml/.yaml at root, collections/ or roles/;
global enabled policy; optional write-only project requirements_token.
Outputs: global policy, has_requirements_token, credential-free dependency payload.

## Behavior

Git requirements accept HTTPS URLs, optional git+ prefix, version branch/tag/SHA,
and collection repository #subdirectory notation. Other transports and embedded
credentials are rejected. Fetch errors fail the operation. Existing Galaxy entries
continue through Galaxy Proxy. Dependencies are subject to aggregate upload caps.
Direct declarations are supported; nested Git dependency manifests are not resolved.

## Dependencies

ADR-020, ADR-029, ADR-039, ADR-045, [ADR-076](../../adrs/ADR-076-git-requirements.md).

## Verification

Run focused tests with `tox -e unit -- --no-cov tests/test_git_requirements.py`,
`tox -e lint`, `tox -e grpc`, `tox -e openapi`, and frontend build/tests via tox.
