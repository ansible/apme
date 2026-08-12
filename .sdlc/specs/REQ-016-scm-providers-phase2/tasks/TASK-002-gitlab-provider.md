# TASK-002: GitLab Provider

## Parent Requirement

REQ-016: Phase 2 SCM Providers (GitLab + Bitbucket)

## Status

Complete

## Description

Implement `GitLabProvider` (REST v4), register it, and add unit/submit tests
for gitlab.com and self-hosted via `APME_GITLAB_API_URL`.

## Prerequisites

- [x] TASK-001 must be complete

## Implementation Notes

1. Implement branch create, multi-file commit, MR create, optional `branch_head_sha`.
2. Support nested group project paths (URL-encoded).
3. Register `"gitlab"` in the provider registry.
4. Add mocked httpx unit tests and submit integration tests.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/apme_gateway/scm/gitlab.py` | Create | GitLab provider |
| `src/apme_gateway/scm/registry.py` | Modify | Register gitlab |
| `tests/test_gateway_pull_request.py` | Modify | GitLab coverage |

## Verification

- [x] `tox -e lint`
- [x] `tox -e unit`

## Acceptance Criteria Reference

- GitLab Create MR
- Self-hosted GitLab
