# Contract: REQ-016 Phase 2 SCM Providers

## ScmProvider Protocol

Unchanged from ADR-050. New implementations must provide:

- `create_branch(repo_url, base_branch, new_branch, token) -> sha`
- `push_files(repo_url, branch, files, commit_message, token, *, parent_commit_sha=None) -> sha`
- `create_pull_request(repo_url, base_branch, head_branch, title, body, token) -> PullRequestResult`
- Optional: `branch_head_sha(repo_url, branch, token) -> sha | None`

## Registry

| Key | Implementation |
|-----|----------------|
| `github` | `GitHubProvider` (existing) |
| `gitlab` | `GitLabProvider` |
| `bitbucket` | Cloud or Server flavor |

## Configuration

| Env | Default |
|-----|---------|
| `APME_GITLAB_API_URL` | `https://gitlab.com/api/v4` |
| `APME_BITBUCKET_API_URL` | `https://api.bitbucket.org/2.0` |

## REST

No breaking changes. Existing `POST /api/v1/projects/{id}/operation/submit` gains working providers.
