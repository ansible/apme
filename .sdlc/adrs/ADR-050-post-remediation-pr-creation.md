# ADR-050: Post-Remediation PR Creation via Gateway SCM Integration

## Status

Accepted

## Date

2026-04-07 (revised 2026-07-06)

## Context

When APME remediates an Ansible project through the Gateway, the engine returns
patched files in the `SessionResult` protobuf message. The Gateway persists the
remediation record and streams results to the UI — but there is no automated path
to push those changes back to the source repository as a pull request.

Today the remediation lifecycle ends at "here are your fixed files." For
SCM-backed projects (which is the Gateway's primary use case per ADR-037), the
natural next step is committing those fixes to a branch and opening a PR against
the project's default branch. Without this, users must manually download patched
files, create a branch, commit, and open a PR — friction that undermines the
value of automated remediation.

ADR-030 already identifies "PR creation (direct SCM API calls)" as a Gateway
responsibility. This ADR formalizes the design.

### Constraints

- **Engine stays stateless** (ADR-020, invariant 5). The engine never touches
  SCM. PR creation is purely a Gateway concern.
- **Engine never queries out** (invariant 11). The Gateway owns all external
  API calls — SCM provider APIs included.
- **Gateway depends on engine, not the reverse** (invariant, dependency
  direction). The engine has no knowledge of PRs or SCM providers.
- **gRPC between backend services** (invariant 2). PR creation is a Gateway-to-
  external-API operation, not inter-service communication, so this invariant is
  not affected. No new gRPC services are introduced.

### Decision Drivers

- Users expect a "one-click" path from remediation results to a reviewable PR.
- The Gateway already clones repos (it has the SCM URL and branch). Pushing
  changes back is the natural extension.
- Enterprise deployments (AAP, RHDH) need audit trails — a PR with APME as
  author provides that.
- Multiple SCM providers exist (GitHub, GitLab, Bitbucket). The design must
  accommodate all of them without coupling to one.

## Decision

**The Gateway will provide a REST endpoint to create a pull request from a
completed remediation's patched files, using a pluggable SCM provider
abstraction with a hierarchical auth model (project-level overrides global).**

PR creation is user-initiated (not automatic). After reviewing remediation
results in the UI, the user clicks "Create PR" which calls the Gateway API.

### 1. SCM Provider Abstraction

A `ScmProvider` protocol defines the contract for interacting with any SCM
platform:

```python
class ScmProvider(Protocol):
    async def create_branch(self, repo_url: str, base_branch: str, new_branch: str, token: str) -> None: ...
    async def push_files(self, repo_url: str, branch: str, files: dict[str, bytes], commit_message: str, token: str) -> str: ...
    async def create_pull_request(self, repo_url: str, base_branch: str, head_branch: str, title: str, body: str, token: str) -> PullRequestResult: ...
```

A registry maps SCM provider types (`github`, `gitlab`, `bitbucket`) to
implementations. The provider type is inferred from the repo URL
(`github.com` → `github`, `gitlab.com` → `gitlab`) or set explicitly in the
project/global config for self-hosted instances.

### 2. Hierarchical Auth Model

SCM authentication tokens are resolved with project-level precedence over
global:

| Level | Configuration | Scope |
|-------|---------------|-------|
| **Project** | `scm_token` field on the Project record | Overrides global for this project |
| **Global** | `APME_SCM_TOKEN` environment variable | Default for all projects without a project-level token |

Token resolution: if the project has `scm_token` set, use it. Otherwise, fall
back to `APME_SCM_TOKEN`. If neither is set, the "Create PR" action is
unavailable (UI disables the button, API returns 422).

Tokens are stored encrypted at rest in the database (project-level) or read
from the environment (global). Tokens are never logged — `[REDACTED]` per
SECURITY.md.

### 3. REST API

A single unified endpoint handles both push-only and push-and-PR workflows:

```
POST /api/v1/projects/{project_id}/operation/submit
```

Request body:

```json
{
  "activity_id": null,
  "branch_name": "apme/remediate-2026-04-07",
  "create_pr": true,
  "title": "fix: APME automated remediation",
  "body": "Auto-generated PR with APME remediation results.\n\n...",
  "scm_token": null
}
```

All fields are optional — the Gateway generates sensible defaults:
- `activity_id`: when provided, loads patched files from the database
  (historical scan); when omitted, uses the live in-memory operation
- `branch_name`: `apme/remediate-{short_scan_id}`
- `create_pr`: `true` (default — creates a PR after pushing)
- `title`: `fix: APME remediation — {fixed_count} findings resolved`
- `body`: Markdown summary with violation counts, what was fixed, and a link
  back to the activity detail in the APME UI

**Two state sources:** The endpoint supports both live operations (from the
in-memory `OperationRegistry`) and historical activities (from the database).
This is critical for scheduled/CI-triggered scans where no user is present at
the operation panel — the patched files persist in the database and can be
submitted to SCM at any later time.

When `create_pr` is `false`, the endpoint creates the branch and pushes
patched files but does **not** open a pull request. This supports workflows
where teams use branch-based policies, CI triggers, or prefer to open PRs
manually.

Response:

```json
{
  "branch_name": "apme/remediate-abc123",
  "commit_sha": "a1b2c3d4e5f6",
  "pr_url": "https://github.com/org/repo/pull/42",
  "provider": "github"
}
```

When `create_pr` is `false`, `pr_url` is `null`.

Error responses:
- `404` — operation/activity not found, project not found, or no patched files
- `409` — PR already created (idempotency guard), operation not completed,
  operation is not a remediate scan, or no patches available
- `422` — no SCM token configured (project or global), or SCM provider
  cannot be detected
- `502` — SCM provider API error (upstream failure)

#### Superseded endpoints

The following endpoints are **removed** in favour of the unified
`/operation/submit`:

- `POST /api/v1/activity/{activity_id}/pull-request` — original per-activity
  PR creation (ADR-050 initial design)
- `POST /api/v1/projects/{project_id}/operation/create-pr` — duplicate
  per-operation PR creation (ADR-052)

Both lacked the `create_pr` flag and duplicated SCM logic. The unified
endpoint consolidates them under the project operation model (ADR-052) with
a single SCM code path.

### 4. Workflow

```
User clicks "Remediate" in UI
        │
        ▼
Gateway runs FixSession → SessionResult with patched files
        │
        ▼
Gateway persists activity record (patched files stored in DB)
        │
        ▼
User reviews results in Operation panel
        │
        ▼
User clicks "Create PR" (or "Push Branch" if create_pr=false)
        │
        ▼
Gateway:
  1. Resolve SCM token (inline → project → global fallback)
  2. Determine SCM provider from repo_url
  3. Create branch from project's default branch
  4. Push patched files to the new branch
  5. If create_pr=true: create PR against default branch
  6. Store PR URL (if created) in scan record
  7. Transition operation to PR_SUBMITTED
  8. Return branch_name, commit_sha, pr_url to UI
        │
        ▼
UI shows PR link (or branch link) — user reviews in SCM
```

### 5. Patched File Storage

The Gateway must retain patched file content from `SessionResult` so that PR
creation can happen asynchronously (user reviews first, clicks later). Patched
files are stored alongside the activity record in the database. Storage is
ephemeral — files are cleaned up when the activity record is deleted or after
a configurable retention period.

### 6. PR Body Generation

The PR body is auto-generated from the activity record and includes:

- Summary of findings resolved (count by severity and rule ID)
- List of files modified
- Link to the APME activity detail page (if the Gateway URL is configured)
- APME version that performed the remediation
- Note that the PR was auto-generated by APME

### 7. Phased SCM Provider Rollout

| Phase | Provider | Implementation |
|-------|----------|----------------|
| **Phase 1** | GitHub | GitHub REST API v3 (`api.github.com` and GitHub Enterprise Server). Uses PAT or GitHub App installation token. Supports `github.com` and self-hosted via configurable base URL. |
| **Phase 2** | GitLab | GitLab REST API v4. Supports `gitlab.com` and self-hosted. |
| **Phase 2** | Bitbucket | Bitbucket Cloud REST API v2 and Bitbucket Server/Data Center. |

Phase 1 is scoped to this ADR. Phase 2 providers follow the same
`ScmProvider` protocol and are added without architectural changes.

## Alternatives Considered

### Alternative 1: Automatic PR Creation on Every Remediation

**Description**: The Gateway automatically creates a PR immediately after every
remediation completes, with no user interaction.

**Pros**:
- Zero friction — fully hands-off
- CI/CD pipelines could trigger remediation and get PRs automatically

**Cons**:
- Creates noise — not every remediation result is worth a PR (user may want to
  review first)
- No opportunity to customize branch name or PR title
- Harder to handle errors (no user present to see failures)
- Unwanted PRs in the repo if remediation produces unexpected changes

**Why not chosen**: Remediation results should be reviewed before committing to
a repository. User-initiated PR creation preserves human oversight. Automatic
mode can be added later as an opt-in project setting without architectural
changes.

### Alternative 2: PR Creation in the Engine or CLI

**Description**: The engine or CLI handles SCM operations (branch, commit, push,
PR) directly.

**Pros**:
- CLI users get PR creation without the Gateway

**Cons**:
- Violates invariant 11 (engine never queries out) and invariant 5 (engine
  stateless)
- CLI already writes patched files to disk — the user can commit from there
- Adds SCM API dependencies to the engine package
- Engine would need SCM tokens, which crosses security boundaries

**Why not chosen**: SCM operations are an external integration — they belong in
the Gateway (presentation/integration layer), not the engine. CLI users already
have local files and can use their own git workflow.

### Alternative 3: Webhook-Based PR via External CI

**Description**: The Gateway emits a webhook event on remediation completion.
An external CI system (GitHub Actions, Jenkins) picks it up and creates the PR.

**Pros**:
- Gateway stays simple — no SCM API client needed
- Leverages existing CI infrastructure

**Cons**:
- Requires external CI configuration per project — not self-contained
- Adds latency (webhook → CI → PR)
- Failure modes are opaque (CI failure != Gateway failure)
- Not available out of the box — users must set up the CI side

**Why not chosen**: Adds operational complexity and external dependencies. The
Gateway already has repo context and auth tokens; calling the SCM API directly
is simpler and self-contained.

## Consequences

### Positive

- **Complete remediation loop**: Users go from "violations found" to "PR open"
  without leaving the APME UI.
- **Audit trail**: PRs created by APME are visible in the repo's history,
  providing traceability for compliance.
- **Gateway-contained**: No engine changes. The engine remains stateless and
  SCM-unaware, consistent with invariants 5 and 11.
- **Pluggable providers**: The `ScmProvider` protocol supports multiple SCM
  platforms without architectural changes.
- **Hierarchical auth**: Project-level tokens allow multi-org setups where
  different repos use different credentials, with a global fallback for
  simpler deployments.

### Negative

- **New dependency**: The Gateway gains an HTTP client dependency for SCM API
  calls (e.g., `httpx`). Subject to ADR-019 governance.
- **Token management**: Users must configure SCM tokens. Project-level tokens
  require secure storage (encrypted at rest in the database).
- **Patched file retention**: Storing patched file content in the database
  increases storage requirements. Mitigated by configurable retention and
  cleanup on activity deletion.
- **SCM API rate limits**: High-volume deployments may hit SCM provider rate
  limits. Mitigated by PR creation being user-initiated (not bulk/automatic).

### Neutral

- The CLI is unaffected. CLI users continue to write patched files to disk and
  manage their own git workflow.
- The engine is unaffected. No proto changes, no new RPCs.
- The UI gains a "Create PR" button on the Activity Detail page — a frontend
  change scoped to ADR-037's project-centric model.

## Implementation Notes

### GitHub Provider (Phase 1)

The GitHub provider uses the REST API v3:

1. **Create branch**: `POST /repos/{owner}/{repo}/git/refs` — create a ref
   from the base branch's HEAD SHA.
2. **Push files**: `PUT /repos/{owner}/{repo}/contents/{path}` for each file,
   or use the Git Trees/Commits API for atomic multi-file commits.
3. **Create PR**: `POST /repos/{owner}/{repo}/pulls` with `head`, `base`,
   `title`, `body`.

For GitHub Enterprise Server, the base URL is configurable per project or
globally via `APME_GITHUB_API_URL`.

### Database Changes

- **Project table**: Add optional `scm_token` column (encrypted text) and
  optional `scm_provider` column (enum: `github`, `gitlab`, `bitbucket`, or
  auto-detected from URL).
- **Scan/Activity table**: Add optional `pr_url` column (text). Patched file
  content is stored in a related `patched_files` table with columns
  `(activity_id, path TEXT, content BLOB)`. Files are Ansible YAML (UTF-8
  text) but stored as BLOBs to avoid encoding assumptions. A size cap per
  activity (default 50 MiB total) prevents unbounded growth. Rows are
  cascade-deleted with the parent activity record.

### Configuration

| Setting | Level | Description |
|---------|-------|-------------|
| `APME_SCM_TOKEN` | Global (env) | Default SCM token for all projects |
| `APME_GITHUB_API_URL` | Global (env) | GitHub API base URL (default: `https://api.github.com`) |
| `APME_SECRET_KEY` | Global (env) | Symmetric application secret used for encrypting stored SCM tokens at rest |
| `scm_token` | Project (DB) | Per-project SCM token override |
| `scm_provider` | Project (DB) | Explicit provider type (auto-detected if unset) |

### Security Considerations

- Tokens are encrypted at rest in the database using a symmetric key derived
  from the `APME_SECRET_KEY` environment variable (documented in the
  Configuration table above).
- Tokens are never included in API responses, logs, or error messages.
- The "Create PR" endpoint validates that the requesting user has access to
  the project (relevant for enterprise mode with auth).
- SCM API calls use HTTPS only — consistent with the existing `clone_repo`
  restriction to `https://` URLs.

## Related Decisions

- ADR-020: Reporting Service and Event Delivery Model (engine stays stateless)
- ADR-029: Web Gateway Architecture (Gateway owns SCM operations)
- ADR-030: Frontend Deployment Model (PR creation listed as Gateway responsibility)
- ADR-037: Project-Centric UI Model (project as the top-level entity)
- ADR-039: Unified Operation Stream (remediation produces `SessionResult` with patched files)

## References

- [GitHub REST API — Create a pull request](https://docs.github.com/en/rest/pulls/pulls#create-a-pull-request)
- [GitHub REST API — Git References](https://docs.github.com/en/rest/git/refs#create-a-reference)
- [GitLab REST API — Merge Requests](https://docs.gitlab.com/ee/api/merge_requests.html)
- [Bitbucket REST API — Pull Requests](https://developer.atlassian.com/cloud/bitbucket/rest/api-group-pullrequests/)

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-04-07 | AI Agent | Initial proposal |
| 2026-07-06 | AI Agent | Consolidated to unified `/operation/submit` endpoint with `create_pr` flag; removed `POST /activity/{id}/pull-request` and `POST /operation/create-pr`; status → Accepted |
