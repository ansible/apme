# REQ-016: Phase 2 SCM Providers (GitLab + Bitbucket)

## Metadata

- **Phase**: PHASE-003 - Enterprise Dashboard
- **Status**: In Progress
- **Created**: 2026-08-12
- **Priority**: High

## Overview

Complete ADR-050 Phase 2 by adding GitLab and Bitbucket (Cloud and Server/Data Center)
as first-class Gateway SCM providers. Users can clone private repos for check/remediate
and create merge/pull requests after remediation via the existing `/operation/submit`
flow, using the same `ScmProvider` protocol as GitHub (Phase 1).

## User Stories

**As a Platform Engineer**, I want APME to create merge requests on GitLab after
remediation so that I can review fixes without leaving the APME workflow.

**As a Platform Engineer**, I want APME to create pull requests on Bitbucket Cloud
after remediation so that Bitbucket-hosted Ansible content gets the same remediation
loop as GitHub.

**As an Enterprise Administrator**, I want APME to support self-hosted GitLab and
Bitbucket Server/Data Center so that on-prem SCM deployments work with configurable
API base URLs.

**As a Platform Engineer**, I want private Bitbucket Server clones to authenticate
correctly even when the hostname does not contain "bitbucket", so that check and
remediate work for on-prem remotes.

## Acceptance Criteria

### Scenario: GitLab Create MR

- **GIVEN**: A project with a GitLab HTTPS repo URL and a valid SCM token
- **WHEN**: The user submits a completed remediation via `/operation/submit` with `create_pr: true`
- **THEN**: The Gateway creates a branch, pushes patched files, opens a merge request, and stores `pr_url`

### Scenario: Bitbucket Cloud Create PR

- **GIVEN**: A project with a `bitbucket.org` repo URL and a valid SCM token (access token or `username:app_password`)
- **WHEN**: The user submits a completed remediation via `/operation/submit`
- **THEN**: The Gateway creates a PR on Bitbucket Cloud and returns the PR URL

### Scenario: Bitbucket Server/DC Create PR

- **GIVEN**: A project with `scm_provider=bitbucket`, a self-hosted repo URL, `APME_BITBUCKET_API_URL` set to the Server REST base, and a valid HTTP access token
- **WHEN**: The user submits a completed remediation
- **THEN**: The Gateway creates a PR on Bitbucket Server/DC

### Scenario: Self-hosted GitLab

- **GIVEN**: A project with `scm_provider=gitlab` and `APME_GITLAB_API_URL` pointing at a self-hosted instance
- **WHEN**: The user submits a remediation
- **THEN**: API calls target the configured GitLab base URL

### Scenario: Provider-aware private clone

- **GIVEN**: A self-hosted Bitbucket or GitLab HTTPS URL with `scm_provider` set and an SCM token
- **WHEN**: The Gateway runs check or remediate
- **THEN**: The clone authenticates using the correct username scheme (not the generic `git:` fallback)

### Scenario: Unsupported provider remains explicit

- **GIVEN**: A project whose provider cannot be detected and `scm_provider` is unset
- **WHEN**: The user submits for PR creation
- **THEN**: The Gateway returns HTTP 422 with a clear error (unchanged behavior)

## Inputs / Outputs

### Inputs

| Name | Type | Description | Required |
|------|------|-------------|----------|
| `scm_token` | string | Project or global token | Yes for private clone / submit |
| `scm_provider` | string | `github` / `gitlab` / `bitbucket` | For self-hosted |
| `APME_GITLAB_API_URL` | env | GitLab API base | No (default gitlab.com) |
| `APME_BITBUCKET_API_URL` | env | Bitbucket API base | No (default Cloud 2.0) |

### Outputs

| Name | Type | Description |
|------|------|-------------|
| `pr_url` | string | Web URL of created PR/MR |
| `branch_name` | string | Head branch name |
| `provider` | string | `gitlab` or `bitbucket` |

## Behavior

### Happy Path

1. User remediates a project backed by GitLab or Bitbucket.
2. User clicks Create PR (or calls `/operation/submit`).
3. Gateway resolves provider (`scm_provider` or URL detection).
4. Provider creates branch, pushes files, opens PR/MR.
5. Activity stores `pr_url`; UI links to it.

### Edge Cases

| Case | Handling |
|------|----------|
| Bitbucket app password | Store as `username:app_password`; Basic auth for API; clone uses same pair |
| Nested GitLab groups | URL-encode full project path |
| Bitbucket Server `/scm/PROJECT/repo.git` | Parse project key + slug |
| Existing open PR/MR | Reuse when API reports conflict (parity with GitHub) |

### Error Conditions

| Error | Cause | Response |
|-------|-------|----------|
| 422 unsupported provider | Provider not registered / undetectable | Clear detail message |
| 422 no token | Missing SCM token | Clear detail message |
| Upstream 4xx/5xx | SCM API failure | Propagate as operation failure |

## Dependencies

### Internal

- ADR-050: Post-remediation PR creation (`ScmProvider` protocol)
- ADR-056: APME owns SCM commit/push
- REQ-004: Enterprise Integration (Gateway projects / operations)
- Architectural compatibility: Verified (no invariant conflicts — Gateway owns SCM; engine unchanged)

### External

- GitLab REST API v4
- Bitbucket Cloud REST API 2.0
- Bitbucket Server/Data Center REST API 1.0

## Non-Functional Requirements

- **Security**: Tokens never logged or returned in API responses; HTTPS only
- **Compatibility**: Additive only (ADR-060); no breaking REST changes
- **Maintainability**: Shared TLS/httpx helpers across providers

## Security Considerations

- Single `scm_token` string; document auth conventions per provider
- Token encryption at rest is **not** implemented in Phase 1/2; `scm_token` is stored
  as plaintext in the database until a follow-up adds `APME_SECRET_KEY`-based encryption
  (see ADR-050 revision note)

## Related Artifacts

- ADR-050, ADR-056, ADR-037, ADR-029
- `src/apme_gateway/scm/`
