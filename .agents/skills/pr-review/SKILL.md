---
name: pr-review
description: >
  Guide for handling pull request reviews, including automated (Copilot) and
  human reviewer feedback. Use when responding to PR comments, resolving
  review threads, or updating PRs after review.
---

# PR Review

This skill defines how to handle PR review feedback in the APME project.

## Responding to review comments

Every review comment MUST receive a response and resolution. Unanswered
comments block merge.

### Rules

- Address ALL review comments before requesting re-review. Do not leave
  comments unanswered.
- Reply to each comment with a brief explanation of what was done, referencing
  the commit hash (e.g., "Fixed in abc1234.").
- If a comment is a false positive or you disagree, reply with a clear
  technical explanation. Do not dismiss without justification.
- After pushing fixes, update the PR description to reflect the expanded scope
  (per the submit-pr skill).

## Copilot review patterns

Copilot automated reviews surface recurring categories. Address these
proactively before pushing to avoid review round-trips:

### Supply-chain security

Pin GitHub Actions to commit SHAs instead of mutable tags (`@v1`). Mutable
tags allow upstream changes to affect CI without review. Use a comment to
note the original tag:

```yaml
- uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4
```

### Inaccurate documentation

Documentation MUST accurately describe the actual behavior. If a workflow
triggers on `pull_request` targeting `main`, don't document it as running
on "every pull request". Be specific about triggers, branches, and conditions.

### Markdown table formatting

Tables must use a single leading `|` on each line. Double leading `||` renders
as an extra empty column. Validate table rendering before committing.

### Inaccurate comments

Code comments and docstrings MUST accurately describe what the code does. If
you rename a function, change behavior, or remove functionality, update all
associated comments in the same commit.

### Secrets in documentation

Never show API keys, tokens, or credentials on command lines in docs or
examples. Demonstrate env var usage instead. Shell history and process lists
expose command-line arguments.

## Workflow

1. After pushing a PR, wait for both CI and Copilot review.
2. Read all Copilot comments and CI logs.
3. Fix all issues in a single commit (or minimal commits).
4. Reply to each comment with the fix commit hash.
5. **Resolve each review thread** after replying. Replying alone does not
   resolve the thread. Use the GitHub GraphQL API:

   ```bash
   # List unresolved threads
   gh api graphql -f query='{
     repository(owner: "ansible", name: "apme") {
       pullRequest(number: N) {
         reviewThreads(first: 20) {
           nodes { id isResolved comments(first:1) { nodes { body } } }
         }
       }
     }
   }'

   # Resolve a thread
   gh api graphql -f query='mutation {
     resolveReviewThread(input: {threadId: "THREAD_ID"}) {
       thread { isResolved }
     }
   }'
   ```

6. Update the PR description to include the new commit(s).
7. If CI failure is unrelated to your changes (e.g., flaky test, transient
   network issue), fix it anyway — the PR owns the green build.
