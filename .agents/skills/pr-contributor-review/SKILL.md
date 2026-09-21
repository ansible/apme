---
name: pr-contributor-review
description: >
  Review and help prepare a contributor's pull request (upstream or fork).
  Use when the user asks to review a PR, get a contributor PR ready, update a
  contributor's branch, or ensure a PR meets project standards before merge.
  Prefer reviewing hosted CI results over reproducing the full test suite
  locally. Follow this skill so contributor PRs are reviewed consistently and
  avoid rework (failed CI, outdated base, weak description).
argument-hint: "<PR number or URL>"
user-invocable: true
metadata:
  author: APME Team
  version: 1.1.0
---

# Review Contributor PR

This skill defines how to review and assist with a **contributor's** pull
request (someone else's PR, e.g. from a fork or another branch). Use it when
you are helping make a contributor PR merge-ready, not when submitting your
own PR (use `pr-new` for that).

## Goals

- PR is **up to date with its upstream base branch** (no merge conflicts, clean rebase).
- **Hosted quality gates pass**: every applicable required CI check for the
  latest PR head is green. Missing, pending, or failing checks block a
  merge-ready assessment unless a maintainer explicitly grants an exception.
- **PR description** follows the project template (Summary, Changes, Test plan)
  so reviewers and history have clear context.
- Avoid pushing to the contributor's branch with failing CI or an outdated base.

## Workflow

### 1. Fetch PR metadata and diff

Use the GitHub API or `gh pr view` to get:

- PR number, title, body, base/head refs, author.
- List of changed files and patch/diff.

Confirm the **base** branch (e.g. `ansible:main`) and that you know which
remote/branch you will push to if you make changes (e.g. `djdanielsson:branch`).

### 2. Check if the branch is up to date with upstream

- Fetch the PR's actual base branch from the upstream remote, not necessarily
  `main`.
- Ensure the PR head commit is available locally before checking ancestry. For
  example, fetch the reported head SHA (or use the GitHub API comparison
  endpoint as a fallback), then run
  `git merge-base --is-ancestor <upstream-base-ref> <head-sha>` using the PR's
  actual base ref.
- Confirm the PR is reported as mergeable by GitHub and inspect the commit
  range for an unintended merge-based update when a clean rebase is required.
  If upstream has newer commits, the contributor's branch should be rebased
  onto the actual upstream base branch, or updated by fast-forward only, before
  merge. Do not merge the base branch into the contributor branch where the
  repository requires linear history.

GitHub's REST `mergeable` value can temporarily be `null` while GitHub computes
it. Retry the metadata request a bounded number of times. If it remains
`null`, report mergeability as pending and do not assess the PR as ready.

- If you are going to push changes to the contributor's branch (e.g. adding
  fixes or improving the PR):

- Rebase the **local** branch that mirrors their PR onto the resolved upstream
  base ref before pushing. That way the PR stays mergeable and CI runs against
  the latest base branch.

### 3. Review hosted CI checks

Review every applicable required workflow and check for the **latest PR head**,
rather than rerunning the full quality gates locally. Include path-triggered
checks and repository gates such as lint, unit, integration, UI, AI, OpenAPI,
and Helm checks when the changed files require them. Compare the result with
branch-protection requirements and workflow path filters; `gh pr checks` alone
cannot show a check that was never scheduled. This is faster, avoids
duplicating CI work, and keeps the review focused on the contributor's actual
execution environment.

```bash
gh pr checks <N> --repo ansible/apme
```

For deeper investigation, inspect failed workflow/job logs with
`gh run view <run-id> --repo ansible/apme --log-failed`, or use the GitHub web
UI. Confirm that checks correspond to the current head SHA, not an older
commit.

Do not rerun `tox -e lint` or `tox -e unit` locally by default when the
corresponding hosted checks are complete and green. Run local quality gates
when the user explicitly requests local validation, the relevant CI check is
unavailable or inconclusive, or a failure cannot be diagnosed from hosted
logs. This review-specific optimization does not relax the repository's
tox-only quality-gate policy. If local validation is needed, use tox and never
invoke `ruff`, `mypy`, `pytest`, or `prek` directly (ADR-047).

If CI is still running, report validation as pending rather than claiming the
PR is ready, unless an explicit maintainer exception is recorded. If CI is
absent or does not cover an applicable required quality gate, report that
limitation and do not claim the PR is ready unless an explicit maintainer
exception is recorded.

See the `/tox` skill for the full environment reference when local validation
is warranted.

Do not push unrelated changes to the contributor's branch while required
hosted checks are failing. If the user has authorized a corrective push,
explain the failure, push the fix, and wait for the new checks on that head
before proceeding.

### 4. PR description quality

- If the PR body is minimal or missing structure, suggest or apply the
  **pr-new** template: Summary, Changes, Test plan (and optionally Related
  Specs, Type of Change, Security Checklist from CONTRIBUTING).

- You can update the PR body via GitHub (if you have permission) or draft
  text for the maintainer/contributor to paste:

  ```bash
  gh pr edit <N> --repo ansible/apme --body-file path/to/body.md
  ```

- Keep the description accurate: list what changed and how to verify (tests,
  manual steps).

### 5. Pushing to the contributor's branch

- Only push to the contributor's fork/branch if you have permission and the
  user has asked you to (e.g. "push our updates to djdanielsson:fix_errors").

- Before pushing:

  1. Rebase onto the resolved upstream base ref so the PR is up to date.
  2. Push the rebased or corrected head, then wait for and review its hosted
     CI checks (see §3).
  3. Use `--force-with-lease` when pushing a rebased branch:
     `git push <remote> <local-branch>:<their-branch> --force-with-lease`.

- After pushing, the PR will update automatically. Optionally update the PR
  description to mention the new commits.

### 5a. Comment on review threads

When you push fixes that address a review comment, reply on that thread so
the resolution is visible. Follow the **`pr-address-feedback`** skill for the
full procedure (finding thread IDs / Node IDs and using the GraphQL-based thread workflow).

### 5b. Track all deferred work as issues

When reviewing a contributor PR, any concrete work that is intentionally
deferred to a follow-up PR — whether from you, the contributor, or another
reviewer — **MUST** be captured as a GitHub issue immediately. Do not leave
"TODO for later" or "out of scope, will address separately" without creating
an issue. Untracked follow-ups are invisible debt.

```bash
gh issue create --repo ansible/apme \
  --title "<type>(scope): <description from review>" \
  --body "$(cat <<'EOF'
## Context

<What was deferred and why>

Flagged during review of PR #N: <link to comment>

## Proposal

<What should be done>

EOF
)"
```

Include the issue URL in the PR comment thread so reviewers can verify tracking.

### 6. What not to include in the skill

- **Local-only or environment-specific issues** (e.g. commit signing, SSH
  config, IDE settings) should not be part of the contributor-PR review
  checklist unless they are project policy (e.g. DCO). Document those
  separately or in maintainer docs if needed.

## Checklist (quick reference)

When reviewing or preparing a contributor PR:

- [ ] Fetched PR and know base/head and remotes.
- [ ] Branch is up to date with the PR's upstream base branch (rebase if needed before push).
- [ ] Every applicable required hosted CI check passes for the latest PR head;
  missing, pending, and failing checks block a ready assessment unless an
  explicit maintainer exception is recorded.
- [ ] PR description has Summary, Changes, and Test plan (pr-new style).
- [ ] If pushing to their branch: rebase onto the resolved upstream base ref, push with
  `git push <remote> <local>:<their-branch> --force-with-lease`, then wait for
  and review hosted CI on the new head.
- [ ] If you addressed a review comment: follow the `pr-address-feedback` skill
      to reply on the thread with explanation + commit SHA and resolve it.

## References

- **tox skill** (`/tox`): Full tox environment reference.
- **pr-new** skill: PR body template and commit conventions.
- **pr-address-feedback** skill: Responding to review comments and resolving threads.
- **CONTRIBUTING.md**: PR template, testing, security checklist.
- **CLAUDE.md**: Quality gates (tox -e lint, tox -e unit).

