# TASK-004: Bitbucket Server/DC + Clone Polish

## Parent Requirement

REQ-016: Phase 2 SCM Providers (GitLab + Bitbucket)

## Status

Complete

## Description

Implement Bitbucket Server/Data Center provider, flavor selection, and
provider-aware clone token injection for self-hosted hosts.

## Prerequisites

- [x] TASK-003 should be in progress or complete (shared `bitbucket.py`)

## Implementation Notes

1. Parse `/scm/PROJECT/repo.git` and `/projects/PROJECT/repos/repo` URLs.
2. Implement Server REST 1.0 branch/commit/PR flows.
3. Flavor selection: Cloud vs Server based on API URL / host.
4. Update `_inject_token_in_url` for provider hints and `user:pass` tokens.
5. Wire `scm_provider` from project into clone path.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/apme_gateway/scm/bitbucket.py` | Modify | Server/DC + flavor |
| `src/apme_gateway/scan/driver.py` | Modify | Clone auth polish |
| `src/apme_gateway/api/operation_router.py` | Modify | Pass scm_provider to clone |
| `tests/test_scan_driver.py` | Modify | Injection cases |
| `tests/test_gateway_pull_request.py` | Modify | Server coverage |

## Verification

- [x] `tox -e lint`
- [x] `tox -e unit`

## Acceptance Criteria Reference

- Bitbucket Server/DC Create PR
- Provider-aware private clone
