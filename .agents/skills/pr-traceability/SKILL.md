---
name: pr-traceability
description: >-
  Verify ADR and GitHub issue traceability for a pull request. Use when
  preparing, updating, or reviewing a PR to find related ADRs/issues, ensure
  governing ADRs are updated (status, revision history, index), and reference
  issues in commits and the PR body. Invoked by pr-new, pr-address-feedback,
  and pr-contributor-review — also use standalone with a PR number or branch.
argument-hint: "[PR number or branch]"
user-invocable: true
metadata:
  author: APME Team
  version: 1.0.0
---

# PR Traceability — ADRs and Issues

Mandatory checklist for **authoring**, **updating**, and **reviewing** PRs.
Agents must run this before opening a PR, after substantive review fixes, and
when assessing a contributor PR for merge readiness.

## When to run

| Trigger | Who |
|---------|-----|
| Before first `gh pr create` | Author (`pr-new`) |
| After pushing fix commits that change behavior | Author (`pr-address-feedback`) |
| Before declaring a contributor PR merge-ready | Reviewer (`pr-contributor-review`) |
| User asks to check traceability | Anyone |

## Step 1: Collect context

```bash
# Full PR diff (unopened branch)
git diff upstream/main...HEAD

# Existing PR
gh pr view <N> --repo ansible/apme --json title,body,headRefName,baseRefName,closingIssuesReferences
gh pr diff <N> --repo ansible/apme
```

Also read: branch name, commit messages, `.sdlc/specs/REQ-*/`, linked TASK/DR
files in the diff, and `AGENTS.md` invariants touched by the change.

## Step 2: Find related GitHub issues

1. **Explicit links** — parse branch, commits, PR body, and TASK/REQ specs for
   `#123`, `Fixes #123`, `Closes #456`, `Refs #789`.
2. **Search** — when the work clearly fixes or implements tracked work but no
   issue is linked:

   ```bash
   gh issue list --repo ansible/apme --search "<keywords from PR title/summary>" --limit 10
   gh issue view <N> --repo ansible/apme
   ```

3. **Classify each issue**

   | Relationship | PR/commit action |
   |--------------|------------------|
   | Fixes / completes | `Closes #N` in commit footer **and** PR body |
   | Partial / related | `Refs #N` in PR body (do not close) |
   | No issue exists | Create one with `gh issue create` **or** document in PR why none is needed |
   | Deferred from review | Create follow-up issue immediately (see `pr-address-feedback`) |

**Rules**

- At least one of: `Closes #N`, `Refs #N`, or an explicit "no issue" note in
  the PR body for non-trivial work.
- Do not use `Closes #N` unless this PR fully resolves the issue.
- Prefer linking the issue in **both** the squash-merge commit message and the
  PR body (CONTRIBUTING.md).

## Step 3: Find related ADRs

Search **before** deciding the PR is docs-only:

```bash
# ADRs cited in changed files
git diff upstream/main...HEAD | rg -i 'ADR-[0-9]{3}'

# ADRs whose title/slug matches the feature area
rg -l -i '<service or feature keyword>' .sdlc/adrs/ADR-*.md

# Read the index for status
head -120 .sdlc/adrs/README.md
```

Also check:

- `AGENTS.md` architectural invariants referenced by the code change
- REQ/TASK specs in the diff (`Related Decisions`, `ADR-NNN` links)
- `.sdlc/decisions/` DRs that led to an ADR

## Step 4: Decide required ADR action

For **each** related ADR, pick one outcome:

| Situation | Required action |
|-----------|-------------------|
| PR completes ADR scope | Set `## Status` to `Implemented`; add revision history; regenerate index |
| PR ships meaningful but incomplete scope | Set `Partially Implemented (...)` with gap note; revision history; regenerate index |
| PR starts work on an accepted ADR | Ensure status reflects reality (usually `Partially Implemented`) |
| PR implements a **new** architectural choice | Run `/adr-new` in the same PR (or block until ADR is accepted) |
| PR changes behavior **contradicting** an ADR | Stop — new ADR or DR required before merge |
| ADR unrelated | No ADR edit (say so in PR `## Related ADRs`) |

**Status lifecycle:** `Proposed → Accepted → Partially Implemented → Implemented`

After editing ADR files:

```bash
python scripts/generate_adr_index.py   # or rely on pre-commit adr-index hook
```

## Step 5: PR body requirements

Include these sections (add if missing):

```markdown
## Related issues
- Closes #123 — <one-line why>
- Refs #456 — <partial / follow-up context>

## Related ADRs
- [ADR-052](.sdlc/adrs/ADR-052-project-operation-sse-architecture.md) — status → Partially Implemented; SSE shipped, WebSocket removal still pending
- None — docs-only typo fix, no architectural or ADR impact
```

For **reviewers**, flag as blocking when:

- Code implements or completes an ADR decision but **no ADR file** appears in
  the diff and status is still `Accepted`.
- PR body claims `Closes #N` but the diff does not match the issue scope.
- Related ADR status contradicts shipped code (e.g. still `Accepted` while
  feature is clearly in production).

## Step 6: Report (agent output)

Summarize in the PR conversation or before `gh pr create`:

```markdown
### Traceability check
| Item | Result |
|------|--------|
| Issues | Closes #123; Refs #456 |
| ADRs updated | ADR-052 → Partially Implemented |
| ADRs reviewed, no change | ADR-007 (already Implemented) |
| New ADR needed | No |
| Gaps / follow-ups | #789 filed for WebSocket removal |
```

## Related skills

- `/adr-new` — new architectural decision
- `/dr-new` — blocking question before deciding
- `/pr-new` — full PR submission workflow
- `/pr-address-feedback` — review responses and deferred-issue tracking
- `/pr-contributor-review` — contributor PR merge readiness
