# TASK-003: Bitbucket Cloud Provider

## Parent Requirement

REQ-016: Phase 2 SCM Providers (GitLab + Bitbucket)

## Status

Complete

## Description

Implement Bitbucket Cloud (API 2.0) with access-token Bearer and
`username:app_password` Basic auth conventions, plus tests.

## Prerequisites

- [x] TASK-001 must be complete

## Implementation Notes

1. Implement Cloud branch / commit / pullrequest APIs.
2. Auth: Bearer for raw tokens; Basic when token contains `user:pass`.
3. Expose via registry key `bitbucket` when flavor is Cloud.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/apme_gateway/scm/bitbucket.py` | Create | Cloud (+ later Server) |
| `src/apme_gateway/scm/registry.py` | Modify | Register bitbucket |
| `tests/test_gateway_pull_request.py` | Modify | Cloud coverage |

## Verification

- [x] `tox -e lint`
- [x] `tox -e unit`

## Acceptance Criteria Reference

- Bitbucket Cloud Create PR
