# TASK-001: Shared SCM Plumbing

## Parent Requirement

REQ-016: Phase 2 SCM Providers (GitLab + Bitbucket)

## Status

Complete

## Description

Extract shared HTTP/TLS helpers, add GitLab/Bitbucket API URL config, and
generalize `get_provider` / operation submit API-base wiring.

## Prerequisites

- None

## Implementation Notes

1. Create `src/apme_gateway/scm/_http.py` with CA bundle + `httpx` client helpers from `github.py`.
2. Refactor `github.py` to use the shared helpers.
3. Add `gitlab_api_url` and `bitbucket_api_url` to `GatewayConfig`.
4. Generalize `get_provider(..., api_base_url=)` for all providers.
5. Update `operation_router.py` to select API base by provider type.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/apme_gateway/scm/_http.py` | Create | Shared TLS/httpx |
| `src/apme_gateway/scm/github.py` | Modify | Use shared helpers |
| `src/apme_gateway/scm/registry.py` | Modify | Generic api_base_url |
| `src/apme_gateway/config.py` | Modify | New env vars |
| `src/apme_gateway/api/operation_router.py` | Modify | API base by provider |

## Verification

- [x] `tox -e lint`
- [x] `tox -e unit` (existing GitHub PR tests still pass)

## Acceptance Criteria Reference

- Shared plumbing supporting GitLab/Bitbucket scenarios
