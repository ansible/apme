---
name: pr-address-feedback
description: >
  Guide for handling pull request reviews, including automated (Copilot,
  CodeRabbit) and human reviewer feedback. Use when responding to PR comments,
  resolving review threads, or updating PRs after review.
argument-hint: "<PR number>"
user-invocable: true
metadata:
  author: APME Team
  version: 1.2.0
---

# PR Address Feedback

This skill defines how to handle PR review feedback in the APME project.

## Responding to review comments

Every review comment MUST receive a response. Resolve threads only after the
feedback has been addressed and accepted; leave threads unresolved when disputing
feedback and escalate to a human reviewer. Unanswered comments or unresolved
disputed threads block merge.

### Rules

* Address ALL review comments before requesting re-review. Do not leave
  comments unanswered.

* Every comment requires a **closing reply**. When the feedback is addressed
  or accepted, also **resolve the thread** via the GitHub API. When
  disputing or flagging a false positive, leave the thread unresolved for
  human escalation.

* Reply to each comment with a **brief explanation of how it was resolved** and
  the commit hash (e.g., "Removed the unused imports so Ruff F401 passes.
  Fixed in abc1234."). Do not reply with only the SHA; explain the fix.

* If a comment is a false positive or you disagree, reply with a clear
  technical explanation. Do not resolve the thread. This will require human
  intervention. Do not dismiss without justification.

* After pushing fixes, update the PR description to reflect the expanded scope
  (per the pr-new skill).

### Deferred work MUST be tracked

Any time a review response includes language like "follow-up PR", "subsequent
PR", "leaving as a follow-up", "future enhancement", "out of scope for this
PR", or "logging this for later" — you **MUST** create a GitHub issue
immediately using `gh issue create`. Do not reply to the comment without also
creating the issue. Include the issue URL in your reply so the reviewer can
verify tracking.

Untracked follow-ups are invisible debt. If it is worth mentioning, it is
worth an issue.

```bash
# Create a follow-up issue and capture the URL
gh issue create --repo ansible/apme \
  --title "<type>(scope): <brief description>" \
  --body "$(cat <<'EOF'
## Context

<What was the review comment and why it wasn't addressed in this PR>

Flagged in: <link to PR comment thread>

## Proposal

<What should be done>

## References

- PR #N
EOF
)"
```

## Automated reviews as agent learning opportunities

Every automated reviewer comment (Copilot, CodeRabbit) on an agent-authored
PR is a defect in our own review process. These tools apply the same
principles every time — if one found something, the agent's pre-submit
self-review (see the `pr-new` skill, Step 3 + Step 3b Rule of Five)
should have found it first. Without tightening that loop, we will never
ship a PR without comments.

After fixing each automated finding, ask: _which principle from the
pr-new self-review (Step 3 questions or Rule of Five pass 1–5) should
have caught this?_ If one exists but didn't trigger, the principle needs
to be clearer or the agent didn't apply it. If no principle covers it,
strengthen the matching Rule of Five lens (or Step 3 question) — frame
it as a general evaluation criterion, not a specific instance. Adding
"don't do X" only prevents X; strengthening a principle prevents the
entire class of issues X belongs to.

## How automated reviewers evaluate code

Automated reviewers (Copilot, CodeRabbit) succeed because they apply a
small number of universal evaluation criteria to every line of the diff.
Understanding these criteria lets the agent anticipate findings rather
than react to them.

**Semantic truthfulness.** Every declaration is read as a contract.
The reviewer checks whether types, return values, error codes, version
ranges, log levels, comments, and docstrings accurately describe what
the code actually does. Any gap — a comment that says "all" when the
code means "some", a return type that promises `T` but can produce
`None`, a docstring that lists parameters the function no longer
accepts — gets flagged.

**Information exposure.** The reviewer asks "should this data be visible
here?" for every piece of information that escapes internal scope:
logged data, error messages, API responses, documentation examples.
User content in info-level logs, credentials on CLI examples, internal
paths in error messages — all get flagged because the reviewer assumes
the minimum-exposure principle.

**Caller safety.** The reviewer reads every public interface from the
caller's perspective and asks "could this surprise me?" Nullable
returns not reflected in the type, missing null guards on
platform-provided arguments, unsafe casts, optional fields typed as
required, hidden side effects (logging, I/O, global state) that the
name or signature doesn't advertise — all get flagged because the
reviewer assumes callers trust the type signature and expect no
undisclosed behavior.

**Drift between prose and code.** Any time a comment, docstring, ADR,
README, or inline annotation co-exists with the code it describes,
the reviewer checks whether the prose is still true after the diff.
Renamed functions with old docstrings, changed triggers with old
workflow descriptions, removed features with lingering references —
all flagged.

**Supply-chain mutability.** References to external resources (action
tags, dependency versions, base images) that can change without
review get flagged. The reviewer assumes that anything not pinned to
a specific immutable identifier is a vector for silent behavior change.

**Internal consistency.** Every module's exports, code paths, and
naming conventions are checked against each other. If nine code paths
use a registry lookup but the tenth hardcodes a value, or if one
export capitalizes differently from its siblings, the reviewer flags
the deviation because inconsistency signals copy-paste drift or an
unfinished refactor.

**Adversarial input tracing.** The reviewer constructs edge-case
scenarios for public functions: what happens with an empty-but-not-falsy
value, a field combination after partial deletion, a response that
satisfies the gRPC status check but lacks expected fields? If the traced
path fails silently, sends a vacuous request, or produces a return value
that violates the declared type, the reviewer flags it.

**Inherited contract completeness.** When the diff extends a class or
implements a Protocol, the reviewer checks that the subclass honors the
full runtime contract — not just the compiler-required members but
expected behaviors. A validator that writes files (violating ADR-009),
a gRPC servicer using synchronous calls (violating ADR-007), a
transform that modifies state without calling `submit()` — all get
flagged because mypy enforces structure but runtime contracts include
semantics the type checker cannot verify.

**Dead weight.** Unused imports, unreachable branches, written-but-never-
read variables, parameters accepted but ignored — anything the code
pays for but doesn't use. The reviewer assumes dead code obscures intent
and may mask bugs.

### APME-specific patterns

These are known project-specific applications of the principles above.
They serve as a quick reference, not an exhaustive list — the principles
above should catch novel issues these don't cover.

- **Validator read-only invariant**: validators must never modify files
  (ADR-009). If a validator writes, creates, or deletes any file, flag it.
- **Proto-servicer parity**: every RPC in `primary.proto` /
  `validate.proto` must have a matching servicer method in
  `daemon/`. Missing handlers mean gRPC returns UNIMPLEMENTED at runtime.
- **Rule ID conventions**: L/M/R/P/SEC per ADR-008, EXT- for plugins
  (ADR-042). Misformatted rule IDs break filtering and reporting.
- **Executor discipline**: blocking work (engine scan, subprocess calls,
  venv builds) must use `run_in_executor()`, never block the event loop
  (ADR-007). Look for synchronous `subprocess.run()` or file I/O in
  async server methods.
- **Dependency direction**: engine never imports from gateway
  (ADR-020, ADR-029). If `apme_engine` imports `apme_gateway`, flag it.
- **Session venv ownership**: only Primary writes to `/sessions`
  (ADR-022). Other services mount it read-only.
- **tox-only invocation**: never invoke `ruff`, `mypy`, `pytest`, or
  `prek` directly in docs, scripts, or CI (ADR-047). Always use
  `tox -e <env>`.
- **GitHub Actions pinning**: pin to commit SHAs with a tag comment
  (`actions/checkout@SHA # v4`), not mutable tags.
- **OPA is subprocess, not REST**: the OPA validator invokes `opa eval`
  via subprocess — do not introduce httpx or HTTP client dependencies
  for OPA (verified in code, documented in AGENTS.md invariant 9).

## Automated review patterns

Copilot and CodeRabbit surface recurring categories. Address these
proactively before pushing to avoid review round-trips:

### Supply-chain security

Pin GitHub Actions to commit SHAs instead of mutable tags (`@v1`). Mutable
tags allow upstream changes to affect CI without review. Use a comment to
note the original tag:

```yaml
- uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4
```

### Inaccurate documentation

Documentation MUST accurately describe the actual behavior. Be specific about triggers, branches, and conditions.

### Markdown table formatting

Tables must use a single leading `|` on each line. Double leading `||` renders
as an extra empty column. Validate table rendering before committing.

### Inaccurate comments

Code comments and docstrings MUST accurately describe what the code does. If
you rename a function, change behavior, or remove functionality, update all
associated comments in the same commit.

### Secrets in documentation

Never show API keys, tokens, or credentials on command lines in docs or
examples. Demonstrate env var usage instead.

### Unused imports (Ruff F401)

Remove unused imports or use the symbol. Prefer trimming the import list over `# noqa: F401` unless the import is intentionally side-effect only.

## Workflow

1. **Sync Branch:** Ensure the PR branch is up to date with upstream main.

   ```bash
   git fetch upstream
   git rebase upstream/main
   ```

2. **Review & Plan:** Check CI status and read all review comments. **Write out a brief Action Plan** detailing which files you will edit to fix the comments.

3. **Fix & Validate Locally:** Fix all issues in minimal commits. **Crucially, run local validation before pushing** (e.g., `tox -e lint` or `tox -e unit`) to ensure your fix doesn't break something else.

4. **Push:** `git push --force-with-lease`

5. **Wait & Verify Remote CI:** Wait 2-3 minutes for remote CI pipelines to run, then check their status. Some tests only run remotely; fix any remote-only failures before proceeding.

6. **Reply & Resolve (GraphQL):** Reply to *every single comment* with how it was handled (fixed + hash, deferred + issue link, or disputed). **Only resolve the threads you actually fixed or formally deferred.** Leave disputed threads unresolved for human review.

7. **Verify Actions:** Query the threads one last time to ensure every thread has your reply, and that you didn't accidentally resolve a thread you didn't fix.

### Checking CI status

Always check CI checks as part of the review workflow.

```bash
# After pushing, wait a few minutes, then list pending or failing checks (replace N with PR number)
gh pr checks N --json name,state --jq '.[] | select(.state != "SUCCESS")'

# View failed job logs directly
gh run view RUN_ID --log-failed 2>&1 | tail -80
```

Do **not** run `ruff`, `mypy`, `pytest`, or `prek` directly — always use tox.

### Replying to and Resolving review threads (GraphQL ONLY)

**CRITICAL:** Always use GraphQL (Base64 Node IDs) for both listing, replying, and resolving. Do NOT mix REST integer IDs with GraphQL Node IDs. Do NOT use `minimizeComment`.

**Step 1: List unresolved threads to get `THREAD_ID`**
Replace `N` with the PR number. This gets the `id` for each unresolved thread.

```bash
gh api graphql -f query='{
  repository(owner: "ansible", name: "apme") {
    pullRequest(number: N) {
      reviewThreads(first: 50) {
        nodes { id isResolved comments(last: 5) { nodes { body author { login } } } }
      }
    }
  }
}' --jq '.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved == false) | {id, snippet: .comments.nodes[0].body[0:120]}'
```

**Step 2: Reply to the thread**
Replace `THREAD_ID` with the `id` fetched above. State how the issue was resolved and the commit hash, OR explain why it is being disputed.

```bash
gh api graphql -f query='mutation {
  addPullRequestReviewThreadReply(input: {pullRequestReviewThreadId: "THREAD_ID", body: "Removed the unused imports so Ruff F401 passes. Fixed in abc1234."}) {
    comment { id }
  }
}'
```

**Step 3: Resolve the thread (CONDITIONAL)**
Only run this if you successfully addressed the comment or filed a follow-up issue. **Do NOT run this if you are disputing the comment.**

```bash
gh api graphql -f query='mutation {
  resolveReviewThread(input: {threadId: "THREAD_ID"}) {
    thread { isResolved }
  }
}'
```

*(You may combine Step 2 and Step 3 in a single bash script or execute them sequentially.)*

### Verification Check

After replying and selectively resolving, run this query to list **all** threads (resolved and unresolved) with their latest replies. This differs from Step 1, which only shows unresolved threads.

```bash
gh api graphql -f query='{
  repository(owner: "ansible", name: "apme") {
    pullRequest(number: N) {
      reviewThreads(first: 50) {
        nodes { id isResolved comments(last: 3) { nodes { body author { login } } } }
      }
    }
  }
}' --jq '.data.repository.pullRequest.reviewThreads.nodes[] | {id, isResolved, lastReplyBy: .comments.nodes[-1].author.login, lastReplySnippet: .comments.nodes[-1].body[0:120]}'
```

* **Verify Replies:** Every thread must have a reply from you. If `lastReplyBy` is still the original reviewer, that thread was missed.

* **Verify Intentional State:** Unresolved threads should only be ones you intentionally left open (disputed). Verify that any thread still showing `isResolved: false` was left open on purpose. Do NOT blindly resolve all threads just to clear the list.

### After pushing fixes: check for new automated reviews

Copilot and CodeRabbit may run again on new commits. Re-check whether
they left new reviews or line comments so you can reply and resolve any
new threads.

```bash
# New Copilot review (replace N with PR number, ISO8601 with last push time)
gh api repos/ansible/apme/pulls/N/reviews --jq '.[] | select(.user.login == "copilot-pull-request-reviewer[bot]" and .submitted_at > "ISO8601") | {submitted_at, state, body: .body[0:200]}'

# New Copilot inline comments
gh api repos/ansible/apme/pulls/N/comments --jq '.[] | select(.user.login == "copilot-pull-request-reviewer[bot]" and .created_at > "ISO8601") | {created_at, path, body: .body[0:200]}'

# New CodeRabbit review
gh api repos/ansible/apme/pulls/N/reviews --jq '.[] | select(.user.login == "coderabbitai[bot]" and .submitted_at > "ISO8601") | {submitted_at, state, body: .body[0:200]}'

# New CodeRabbit inline comments
gh api repos/ansible/apme/pulls/N/comments --jq '.[] | select(.user.login == "coderabbitai[bot]" and .created_at > "ISO8601") | {created_at, path, body: .body[0:200]}'
```

