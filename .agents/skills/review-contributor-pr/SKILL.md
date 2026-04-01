---
name: review-contributor-pr
description: >
  Review and help prepare a contributor's pull request (upstream or fork).
  Use when the user asks to review a PR, get a contributor PR ready, update a
  contributor's branch, or ensure a PR meets project standards before merge.
  Follow this skill so contributor PRs are reviewed consistently and avoid
  rework (lint/test failures, outdated base, weak description).
argument-hint: "<PR number or URL>"
user-invocable: true
metadata:
  author: APME Team
  version: 1.0.0
---

# Review Contributor PR

This skill defines how to review and assist with a **contributor's** pull
request (someone else's PR, e.g. from a fork or another branch). Use it when
you are helping make a contributor PR merge-ready, not when submitting your
own PR (use `submit-pr` for that).

## Goals

- PR is **up to date with upstream main** (no merge conflicts, clean rebase).
- **Quality gates pass**: `tox -e lint` and `tox -e unit` on the full tree.
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

- Fetch `upstream main` (or the base branch).
- Compare base ref of the PR to current `upstream/main`. If upstream has
  newer commits, the contributor's branch should be rebased (or merged) onto
  `upstream/main` before merge.

If you are going to push changes to the contributor's branch (e.g. adding
fixes or improving the PR):

- Rebase the **local** branch that mirrors their PR onto `upstream/main`
  before pushing. That way the PR stays mergeable and CI runs against the
  latest main.

### 3. Run quality gates before pushing

Run tox quality gates on the **entire** tree, not only the changed files:

```bash
tox -e lint
tox -e unit
```

Fix any failures (line length, untyped decorators, docstring sections, format,
test regressions) before pushing to the contributor's branch.

Do **not** run `ruff`, `mypy`, `pytest`, or `prek` directly — always use tox
(ADR-047). See the `/tox` skill for the full environment reference.

Do **not** push to the contributor's branch if tox fails; fix in a new commit
and then push so CI stays green.

### 4. PR description quality

- If the PR body is minimal or missing structure, suggest or apply the
  **submit-pr** template: Summary, Changes, Test plan (and optionally Related
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

  1. Rebase onto `upstream/main` so the PR is up to date.
  2. Ensure `tox -e lint` and `tox -e unit` pass (see §3).
  3. Use `--force-with-lease` when pushing a rebased branch:
     `git push <remote> <local-branch>:<their-branch> --force-with-lease`.

- After pushing, the PR will update automatically. Optionally update the PR
  description to mention the new commits.

### 5a. Comment on review threads (same as pr-review)

When you push fixes that address a review comment, **reply on that thread** so
the resolution is visible. Use the same method as the **pr-review** skill:

- Reply via the REST API (use the **top-level** comment id for the thread, not
  a reply). Replace `PR` with the pull request number (e.g. `22`) and
  `COMMENT_ID` with the top-level comment's `id`:

  ```bash
  gh api -X POST "repos/ansible/apme/pulls/PR/comments/COMMENT_ID/replies" \
    -f body="Brief explanation of the fix. Fixed in COMMIT_SHA."
  ```

- To find comment IDs: `gh api repos/ansible/apme/pulls/PR/comments` — use the
  top-level comment's `id` (the one with `in_reply_to_id: null` for that thread).
- Each reply should state **how** the issue was resolved and include the commit
  hash. Optionally resolve the thread via GraphQL (see pr-review skill).

### 6. What not to include in the skill

- **Local-only or environment-specific issues** (e.g. commit signing, SSH
  config, IDE settings) should not be part of the contributor-PR review
  checklist unless they are project policy (e.g. DCO). Document those
  separately or in maintainer docs if needed.

## Checklist (quick reference)

When reviewing or preparing a contributor PR:

- [ ] Fetched PR and know base/head and remotes.
- [ ] Branch is up to date with upstream main (rebase if needed before push).
- [ ] `tox -e lint` and `tox -e unit` pass.
- [ ] PR description has Summary, Changes, and Test plan (submit-pr style).
- [ ] If pushing to their branch: rebase onto upstream main, tox green, then
      `git push <remote> <local>:<their-branch> --force-with-lease`.
- [ ] If you addressed a review comment: reply on that thread (same as pr-review)
      with explanation + commit SHA, using the replies endpoint (replace PR and COMMENT_ID).

## References

- **tox skill** (`/tox`): Full tox environment reference.
- **submit-pr** skill: PR body template and commit conventions.
- **pr-review** skill: Responding to review comments and resolving threads.
- **CONTRIBUTING.md**: PR template, testing, security checklist.
- **CLAUDE.md**: Quality gates (tox -e lint, tox -e unit).

