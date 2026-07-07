---
name: pr-new
description: >
  Prepare and submit a pull request for the APME project. Syncs with upstream,
  creates a feature branch, runs quality gates (tox -e lint, tox -e unit),
  updates documentation and ADRs as needed, commits with conventional commits,
  then creates the PR via gh. Use when the user asks to submit, create, or open
  a pull request, or says "submit PR", "open PR", "create PR", "new PR".
argument-hint: "[branch-name] [--title 'PR title']"
user-invocable: true
metadata:
  author: APME Team
  version: 1.1.0
---

# PR New

## Workflow

### Step 1: Sync with upstream and create a feature branch

Always start from the latest upstream main:

```bash
git fetch upstream
git checkout -b <branch-name> upstream/main
```

Use a descriptive branch name (e.g., `feat/add-ruff-prek`, `fix/parser-context-manager`).

If changes already exist on the current branch (e.g., from an in-progress session), cherry-pick or rebase them onto the new branch.

### Step 2: Run quality gates

```bash
tox -e lint
tox -e unit
```

**Both must pass cleanly on the full tree** — not just the files you changed.
If the branch has pre-existing violations (e.g., from an old base), rebase onto `upstream/main` first.

Do **not** run `ruff`, `mypy`, `prek`, or `pytest` directly — always use tox (ADR-047).
See the `/tox` skill for the full environment reference.

### Step 3: Self-review the diff

**This step is mandatory.** Do not skip it. Do not combine it with
Step 2. After quality gates pass, review the **full PR diff** — all commits
since the branch diverged from the base branch, not just the last
commit or unstaged changes:

```bash
git diff upstream/main...HEAD
```

Read every changed line against these questions. For each question,
name at least one specific file and line you verified. If you cannot,
you haven't actually reviewed the diff.

**Artifact-type sweep.** Before answering the questions below, list
every distinct artifact type in the diff (e.g., Python, proto, Rego,
Ansible YAML, shell script, Dockerfile, JSON config, Markdown). For
each question, you must cite at least one file of _each_ artifact
type — not just Python. If a question feels inapplicable to an
artifact type, translate it:

- "caller" in proto means any service or CLI that invokes an RPC
  (e.g., Primary calling `Validator.Validate`, CLI calling
  `FixSession`)
- "type signature" in Rego means a rule's expected input/output
  shape (e.g., `input.hierarchy` must contain certain keys)
- "manifest parity" means every gRPC service in `proto/*.proto` has
  a matching servicer in `daemon/` and every RPC method has a handler
- "constructed scenario" for async servers means: what happens when
  `run_in_executor()` blocks longer than expected, when a validator
  never responds, or when `asyncio.gather()` returns a mix of results
  and exceptions?
- "dependencies pinned to intent" for Dockerfiles means: does the
  base image tag, `pip install` version spec, or `COPY` path express
  exactly what you mean?

1. **Does every statement mean what it says?** Check every type
   annotation, return value, error code, version range, log level,
   comment, and docstring. If the code declares it, the runtime must
   honor it on every path.

2. **Does this expose more than it should?** Check every log call,
   error message, and user-facing string. Does it contain user content,
   credentials, or internal state? Could a caller or log reader learn
   something they shouldn't? Also check every capability grant:
   permission scopes, CORS origins, container capabilities. Does each
   grant the minimum necessary?

3. **Would a caller be surprised?** Read every public function from
   the caller's perspective. Can it return a value the type doesn't
   cover? Does it mutate an argument the caller owns? Does it throw
   where the signature implies it won't? Does it have side effects
   (logging, I/O, global state) that its name or signature doesn't
   advertise? Does it behave differently from sibling functions in
   the same module?

4. **Is everything still true after this change?** Diff comments and
   docstrings against the code they describe. Did you rename something
   but leave the old name in prose? Did you change behavior but leave
   an old description? Check ADRs, `CLAUDE.md`, `AGENTS.md`, and
   `docs/` for stale references.

5. **Are dependencies and versions pinned to intent?** Check every
   version range, action tag, and base image. Does each one express
   what you actually mean — not tighter, not looser?

6. **Is there dead weight?** Check for unused imports, unreachable
   branches, written-but-never-read variables, parameters accepted
   but ignored.

7. **Is this internally and externally consistent?** Within each
   module: do all code paths use the same patterns (e.g., registry
   lookups vs hardcoded values)? Are exports named consistently?
   Across the repo: do proto RPCs have matching servicer methods in
   `daemon/`? Do rule IDs follow ADR-008 conventions (L/M/R/P/SEC)?
   Does `_DEFAULT_PORTS` match the services actually started? Are
   `event_emitter` calls consistent with the reporting sink protocol?
   Cross-artifact mismatches (proto declaration vs Python
   implementation) are the easiest to miss and the most embarrassing
   to ship.

8. **Would a constructed scenario break this?** For each public
   function, construct one realistic failure case: an edge-case
   input, a specific field combination after deletion/filtering,
   an empty-but-not-falsy value. Trace it through the code path.
   If it fails silently, sends a vacuous request, or produces a
   return value that violates the declared type, that's a finding.
   Also construct _temporal_ failures: what happens when an async
   dependency never responds, times out, or responds after the
   consumer has moved on? What happens when `asyncio.gather()`
   returns a mix of results and exceptions — does every caller
   handle `return_exceptions=True` correctly?

9. **Do inherited contracts hold?** When implementing a Protocol
   or extending a base class, check that the subclass honors the
   full runtime contract — not just the compiler-required members,
   but expected behaviors (validators must be read-only per ADR-009,
   gRPC servicers must use `grpc.aio` per ADR-007, transforms must
   call `submit()` for changes to take effect per ADR-044).

Only proceed to Step 3b after completing this review.

### Step 3b: Cold subagent review

**This step is mandatory.** The self-review in Step 3 is necessary but
insufficient — it suffers from confirmation bias because the reviewing
agent wrote the code. Spin up a **read-only subagent** with no
conversation history to review the diff cold.

The subagent sees only the diff and the review questions. It has no
memory of the intent, iterations, or trade-offs that led to the code.
This forces it to read every line at face value — the same way Copilot
or a human reviewer would.

```text
Launch a Task subagent with:
  subagent_type: "generalPurpose"
  readonly: true
  run_in_background: false
```

Use this prompt template (fill in the repository path and diff):

```text
You are reviewing a pull request diff. You have no prior context about
why these changes were made — review every line at face value.

Repository: <absolute path to repo>
Base branch: upstream/main

Run `git diff upstream/main...HEAD` to get the full diff, then read
every changed file in full (not just the diff hunks — you need
surrounding context to evaluate contracts and consistency).

Evaluate the diff against these 9 questions. For each question, either
report a concrete finding (file, line, what's wrong, why it matters)
or state "No findings." Do not pad with observations that aren't
actionable.

1. Does every statement mean what it says? (types, return values,
   comments, docstrings — does the runtime honor them on every path?)
2. Does this expose more than it should? (logs, errors, user strings,
   capability grants, container permissions)
3. Would a caller be surprised? (nullable returns, hidden side effects,
   undisclosed I/O, inconsistency with sibling functions)
4. Is everything still true after this change? (prose vs code drift —
   renamed symbols with old docstrings, changed behavior with old
   descriptions, stale ADR/doc references)
5. Are dependencies and versions pinned to intent?
6. Is there dead weight? (unused imports, unreachable branches,
   written-but-never-read variables)
7. Is this internally and externally consistent? (patterns, naming,
   cross-artifact parity — e.g., proto RPCs must have matching
   servicer methods in daemon/, rule IDs must follow ADR-008)
8. Would a constructed scenario break this? (edge-case inputs,
   empty-but-not-falsy values, temporal failures — async dependency
   never responds, asyncio.gather with return_exceptions=True
   returning a mix of results and exceptions)
9. Do inherited contracts hold? (Protocol/base class implementations
   honor runtime semantics — validators are read-only per ADR-009,
   gRPC servicers use grpc.aio per ADR-007)

Return ONLY findings. Format each as:
  **[Q#] file:line — description**

If there are no findings across all 9 questions, return:
  "No findings."
```

**Act on every finding.** Fix the code, then re-run `tox -e lint` and
`tox -e unit`. Do not dismiss findings without a clear technical
justification documented in the self-review output.

If the subagent returns "No findings", proceed to Step 4.

### Step 4: Update documentation

Check whether your changes affect areas covered by existing docs. Update any that apply:

| Doc | When to update |
|-----|----------------|
| `docs/guides/DEVELOPMENT.md` | New dev workflows, setup changes, new rule patterns |
| `docs/architecture/` | Container topology, gRPC contract changes, new services |
| `docs/architecture/` | Request lifecycle, serialization, payload shape changes |
| `docs/guides/DEPLOYMENT.md` | Podman pod spec, container config, env vars |
| `docs/rules/LINT_RULE_MAPPING.md` | New or renamed rule IDs |
| `docs/design/DESIGN_VALIDATORS.md` | Validator abstraction changes |
| `docs/design/DESIGN_REMEDIATION.md` | Remediation engine changes |

If a new rule was added, regenerate the catalog:

```bash
python tools/generate_rule_catalog.py
```

### Step 5: Update SDLC artifacts (if applicable)

If the change involves an architectural decision (new service, new protocol, new deployment strategy, new tooling adoption), create an ADR in `.sdlc/adrs/` using the `adr-new` skill. The file should follow the naming convention `ADR-NNN-slug.md`.

If open questions or decisions emerged during the session, create a Decision Request using the `dr-new` skill.

If requirements or tasks were affected, update them using the `req-new` or `task-new` skills.

The agent should invoke these skills proactively when context warrants it, informing the user of any artifacts created. All artifacts are reviewed in the PR diff.

### Step 6: Commit with conventional commits

Use the [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) format:

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

Common types for this project:

| Type | When to use |
|------|-------------|
| `feat` | New feature (rule, validator, CLI subcommand, service) |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `style` | Code style/formatting (no logic change) |
| `refactor` | Code restructuring (no feature or fix) |
| `test` | Adding or updating tests |
| `build` | Build system, dependencies, containers |
| `ci` | CI/CD configuration |
| `chore` | Maintenance tasks |

Scopes reflect project areas: `engine`, `native`, `opa`, `ansible`, `gitleaks`, `daemon`, `cli`, `formatter`, `remediation`, `cache`, `proto`.

Examples:
- `feat(native): add L060 jinja2-spacing rule`
- `fix(engine): use context manager for file reads in parser`
- `build: add ruff linter and prek pre-commit hooks`
- `docs: add prek section to DEVELOPMENT.md`

### Step 7: Check branch/artifact alignment

Before pushing, verify the branch name matches the artifact IDs being committed:

```
Checking branch/artifact alignment...
- Branch: docs/req-005-aa-deprecated-reporting
- SDLC artifacts in diff: REQ-011, DR-013
```

**If mismatch detected:**
```
⚠️  Branch name contains 'req-005' but artifacts use REQ-011

Options:
1. Rename branch to match artifacts (recommended)
2. Continue with mismatched names (not recommended)

Choice (1/2):
```

If option 1 selected, use `/branch-align` to rename before pushing.

**Why this matters:** Reviewers and future contributors use branch names to find related work. A branch named `req-005` that contains `REQ-011` creates confusion.

### Step 8: Push and create the pull request

```bash
git push -u origin HEAD

gh pr create --repo upstream-owner/repo --title "conventional commit style title" --body "$(cat <<'EOF'
## Summary
- Concise description of what changed and why

## Changes
- List of notable changes

## Quality of life
- List any non-functional improvements bundled in this PR: skill updates,
  workflow fixes, SDLC artifact changes, rule/template tweaks, documentation
  for contributor experience, etc.
- Omit this section entirely if there are none.

## Test plan
- [ ] `tox -e lint` passes
- [ ] `tox -e unit` passes
- [ ] Docs updated (if applicable)
- [ ] ADR added (if applicable)
EOF
)"
```

The PR targets upstream's `main` branch from the fork. Return the PR URL to the user.

### Including non-code changes (Quality of life)

PRs often include changes that are not directly part of the feature or fix but
improve the development workflow: skill updates, SDLC template tweaks, rule
improvements, documentation for contributor experience, or process fixes.

These changes belong in the **Quality of life** section of the PR body. Use
this section whenever the PR touches files like `.agents/skills/`, `.sdlc/`,
`CLAUDE.md`, `AGENTS.md`, `SOP.md`, `CONTRIBUTING.md`, or similar workflow
artifacts. This makes it easy for reviewers to separate functional changes
from process improvements.

If a PR contains **only** quality-of-life changes (no production code), use
`chore` or `docs` as the commit type.

### Maintaining the PR

When pushing additional commits to an existing PR, **always update the PR body** to reflect the new changes:

```bash
gh pr edit <pr-number> --body "$(cat <<'EOF'
...updated body...
EOF
)"
```

The Summary, Changes, and Test plan sections must stay current with all commits on the branch, not just the initial one.

### Responding to review feedback

After pushing the PR, reviewers (human, Copilot, or CodeRabbit) may leave
comments. Follow the **`pr-address-feedback`** skill for the full procedure:
checking CI status, replying to comments, resolving threads, and re-checking
for new automated reviews.
