# Design: REQ-016 Phase 2 SCM Providers

## Approach

Extend the existing `ScmProvider` registry with `GitLabProvider` and Bitbucket
Cloud/Server implementations. Extract shared HTTP/TLS helpers from
`GitHubProvider`. Add `APME_GITLAB_API_URL` and `APME_BITBUCKET_API_URL`.
Harden clone token injection to honor `scm_provider` and `user:pass` tokens.

## Key Modules

- `src/apme_gateway/scm/_http.py` — shared TLS / httpx client
- `src/apme_gateway/scm/gitlab.py` — GitLab REST v4
- `src/apme_gateway/scm/bitbucket.py` — Cloud 2.0 + Server/DC 1.0 behind `bitbucket` registry key
- `src/apme_gateway/scan/driver.py` — provider-aware clone auth
- `src/apme_gateway/config.py` / `operation_router.py` — API base wiring

## Flavor Selection (Bitbucket)

- Default / `api.bitbucket.org` / host `bitbucket.org` → Cloud
- Non-default `APME_BITBUCKET_API_URL` or self-hosted host with `scm_provider=bitbucket` → Server/DC
