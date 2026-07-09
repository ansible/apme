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
  version: 1.2.0
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

**Context rule.** The diff alone is not sufficient for Q1, Q4, and Q7.
Before evaluating those questions, **read the full function, class, or
module** surrounding each changed hunk — not just the hunk itself:

- **Q1 (statements mean what they say):** Read the function body to
  verify that new or modified docstrings/comments accurately describe
  what the code actually does. A docstring that says "all validators
  receive scoped data" is wrong if one validator receives the full
  graph.
- **Q4 (still true after this change):** Search for all prose that
  describes the behavior you changed — not just prose you edited.
  If you changed how a function is called, grep for every docstring
  and comment that references that function.
- **Q7 (internally consistent):** Read sibling files and functions
  before writing new code. If you add a `.pyi` field, read the other
  `.pyi` files for conventions. If you write a test, read the sibling
  tests for mock patterns. If you add an env var check, read how
  existing env var checks handle the missing case.

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
   `docs/` for stale references. When implementation adds an
   intentional filter or exception (e.g. stamp only compatible
   members of a mixed group), update any ADR/doc that still says
   "all" / "every" — prose that overclaims is drift even if the
   code is correct.

5. **Are dependencies and versions pinned to intent?** Check every
   version range, action tag, and base image. Does each one express
   what you actually mean — not tighter, not looser?

6. **Is there dead weight?** Check for unused imports, unreachable
   branches, written-but-never-read variables, parameters accepted
   but ignored. Also flag **paid-for-but-wasteful work**: parsing
   the same JSON twice in one loop body; `list.pop(0)` / repeated
   `list.insert(0, …)` when a `deque` (or reverse + `pop()`) would
   be O(1); `len(list(seq))` when `len(seq)` works; nested scans
   that are O(P×V) when an id→row map would be O(P+V) — reviewers
   treat these as dead weight even when functionally correct.

7. **Is this internally and externally consistent?** Within each
   module: do all code paths use the same patterns (e.g., registry
   lookups vs hardcoded values)? Are exports named consistently?
   When a function accepts multiple input shapes (dataclass vs
   mapping, ORM vs dict), do both paths normalize and branch the
   same way for the same logical fields — or can one path skip a
   transform the other applies? When overlaying fields from a
   second source (e.g. outcome ``tier`` onto a pre-grouped
   proposal), do dependent fields (``source``/``gate``/``tier``,
   status→review mapping) stay aligned for *all* pre-group
   sources — not only when the pre-group source was a sentinel
   like ``"outcome"``? When adding fields to an existing
   Pydantic/schema module, match sibling default patterns
   (`Field(default_factory=list)` vs mutable `=[]`)? Across the
   repo: do proto RPCs have matching servicer methods in `daemon/`?
   Do rule IDs follow ADR-008 conventions (L/M/R/P/SEC)? Does
   `_DEFAULT_PORTS` match the services actually started? Are
   `event_emitter` calls consistent with the reporting sink
   protocol? Cross-artifact mismatches (proto declaration vs Python
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
   For persistence and aggregation helpers, also construct:
   - **Concurrent writers** — two sessions calling the same
     select-then-insert / claim path before either commits (lost
     updates, double-counts, `IntegrityError`). Prefer atomic
     upsert or compare-and-set claims.
   - **Non-unique lookup keys** — dicts keyed by `(file, rule_id)`
     or similar when duplicates are realistic; last-write-wins
     silently corrupts overlays.
   - **Mixed members of a group** — after grouping/bucketing, does
     a single decision stamp apply to every member, including ones
     of a different class (e.g. Tier 1 fixed + AI-candidate +
     MANUAL_REVIEW on the same node path)? Filters must be
     allowlists of the intended class, not "everything except X"
     — the latter silently includes unrelated classes.
   - **Docstring vs deny-by-default** — if prose says "only when
     compatible" / "same class", the fallback branch must not return
     ``True`` unconditionally for unknown/sentinel sources.

9. **Do inherited contracts hold?** When implementing a Protocol
   or extending a base class, check that the subclass honors the
   full runtime contract — not just the compiler-required members,
   but expected behaviors (validators must be read-only per ADR-009,
   gRPC servicers must use `grpc.aio` per ADR-007, transforms must
   call `submit()` for changes to take effect per ADR-044).

Only proceed to Step 3b after completing this review.

### Step 3b: Rule of Five (cold multi-agent review)

**This step is mandatory.** Step 3 is necessary but insufficient — it
suffers from confirmation bias because the reviewing agent wrote the
code. Spin up **independent read-only subagents** with no conversation
history. Each pass uses a progressively broader lens (in-the-small →
in-the-large). Findings that appear in **≥2 passes** are ship-blockers
until fixed or explicitly demoted with a documented follow-up.

This replaces the former single cold 9-question subagent. The nine
questions are **folded into the five lenses** below so coverage is not
lost.

#### Scale

| PR size | Passes | Which lenses |
|---------|--------|--------------|
| Small / chore / docs-only / single-file fix | **3** | Pass 1, 2, 5 |
| Nontrivial feature, ADR, multi-service, persistence, API | **5** | Pass 1–5 |

When unsure, use **5**.

#### Model selection (capabilities, not slugs)

Do **not** pin vendor model names or slugs in this skill — they rot.
When launching each Task subagent, choose a model by **capability
tier**, mapping to whatever the current agent runtime offers.

| Tier | Attributes to prefer | Use for |
|------|----------------------|---------|
| **fast** | Low latency, lower cost, solid at local reasoning and grep-style consistency checks; does not need deep architectural judgment | Pass **1**, Pass **2** |
| **strong** | Best available reasoning / long-context judgment in the current runtime; willing to challenge product framing and system design | Pass **3**, Pass **4**, Pass **5** |

**Selection rules:**

1. Match the tier’s **attributes**, not a remembered product name.
2. If the runtime only exposes one model, use it for every pass
   (correctness over cost).
3. If several models fit a tier, pick the cheapest that still matches
   the attributes — do not “upgrade” Pass 1–2 to strong by default.
4. Never downgrade Pass 3–5 to fast to save money; those passes catch
   ship-blockers that mechanical review misses.
5. Parent aggregation stays on the parent model (no extra subagent).

#### Shared preamble (every pass)

Every subagent prompt must start with this block (fill in repo path,
branch, and PR number if known):

```text
You are Review Pass <N> of <TOTAL> for an APME pull request.
You have no prior context about why these changes were made — review
every line at face value.

Repository: <absolute path to repo>
Branch: <branch-name>
Base: upstream/main
PR: #<number or "unopened">

Run `git diff upstream/main...HEAD` for the full diff. Then:
1. List every distinct artifact type in the diff (Python, proto, Rego,
   Ansible YAML, shell, Dockerfile, JSON/YAML config, Markdown, TS/TSX,
   etc.). Your findings must cover each type that appears — translate
   the lens if needed (e.g. "caller" in proto = any RPC invoker;
   "pin to intent" in Dockerfiles = base image / pip / COPY paths).
2. Read each changed file in full (not just hunks). For new functions,
   tests, or stubs, also read sibling files for convention consistency
   (mocks, typing style, error handling).
3. Return ONLY actionable findings. No praise. No padding.
   Format: **[Pass N / severity] file:line — description**
   If truly nothing for your lens: "No findings."
```

Launch all passes in **one message** (parallel Task calls). Set each
subagent’s model according to the tier table above (fast vs strong) —
pass the runtime’s current model id that best matches the tier’s
attributes; omit `model` only when the runtime has a single option.

```text
Launch each Task subagent with:
  subagent_type: "generalPurpose"
  readonly: true
  run_in_background: false
  model: <runtime id matching Pass N's tier>
```

#### Pass 1 — classic bugs (narrow / in-the-small)

**Tier: fast.** Covers former cold-review **Q1, Q3, Q8** (bugs and edge paths).

```text
<shared preamble with N=1>

**Lens — classic code review:** Find concrete bugs in the changed code:
logic errors, off-by-ones, incorrect conditionals, race conditions,
wrong types, missing null checks, broken edge cases, return values that
violate declared types, caller-surprising behavior (nullable returns,
hidden mutation/I/O, throws the signature denies).

Also construct at least one realistic failure per public entry point:
empty-but-not-falsy values, post-filter field combinations, async
dependency never responds / asyncio.gather(return_exceptions=True)
mix, concurrent select-then-insert, non-unique dict keys, mixed members
of a group stamped with one decision, unbounded SQL IN vs SQLite
limits (prefer ``col.in_(select(...))`` over materializing large id
lists into bound parameters; chunk at ~900 when a Python list is
unavoidable; when checking membership of a *small* candidate set
against a large table, query the intersection of those candidates —
never load the full table into Python just to filter a handful of ids).

Do NOT discuss architecture philosophy. Rank findings
critical/high/medium/low.
```

#### Pass 2 — consistency, drift, and waste

**Tier: fast.** Covers former cold-review **Q4, Q5, Q6, Q7**.

```text
<shared preamble with N=2>

**Lens — consistency & drift:** Assume Pass 1 caught obvious bugs. Hunt for:
- Docstring/ADR/comment vs code drift (especially "all"/"every" claims
  vs filtered implementation)
- Dual input shapes that normalize differently (ORM vs dict, dataclass
  vs mapping, servicer vs flush path)
- Overlay fields that drift (tier/source/gate/status→review must stay
  aligned for all pre-group sources). When a column documents
  "empty means fall back to X" (e.g. ``stamp_rule_ids_json`` →
  ``rule_ids_json``), every stamp/filter path must honor that
  fallback — skipping the filter when empty is a contract break.
- Schema/API mistakes (ADR-060: no breaking changes to /api/v1).
  Tightening previously-lenient validation (e.g. turning ignored
  unknown ids into hard 400s) is a semantic break even if the
  OpenAPI shape is unchanged — prefer log+ignore / intersection
  unless an ADR explicitly hardens the contract.
- Cross-artifact parity (proto RPC ↔ daemon servicer; rule IDs ADR-008;
  _DEFAULT_PORTS ↔ started services)
- Test gaps for behaviors the code/docs claim
- Silent no-ops, dead branches, wrong defaults
- **Dependencies pinned to intent** — version ranges, GitHub Action
  tags, base images, pip/uv specs (not tighter, not looser)
- **Dead weight** — unused imports/params; paid-for-but-wasteful work:
  double JSON parse, list.pop(0)/insert(0,…) vs deque, len(list(seq)),
  O(P×V) nested scans that should be O(P+V)
- Pydantic/schema mutable defaults (`=[]`) vs sibling Field(default_factory=list)

Explain briefly why a Pass-1-style review would miss each finding.
```

#### Pass 3 — Right Thing / product fitness

**Tier: strong.** Covers former cold-review **Q3 (product surprise), Q9 (invariants)**.

```text
<shared preamble with N=3>

**Lens — are we doing the Right Thing?** Read the governing ADR(s),
AGENTS.md architectural invariants, and the implementation. Ask:
- Does this actually solve the stated goal, or is it a half-measure /
  framing overclaim?
- Are lifecycle triggers complete, or will users lose state unexpectedly?
- Is the grain/grouping/API shape honest for the UX story?
- Do filters or demotions contradict docs/ADR claims?
- Any invariant violations (validators read-only ADR-009, grpc.aio
  ADR-007, engine never queries out ADR-020/029, REST additive-only
  ADR-060, transforms submit() ADR-044, tox-only ADR-047, etc.)?
- Do Protocol / base-class implementations honor full runtime contracts,
  not just compiler-required members?

Return **[Right Thing / Design Risk]** with recommendation:
fix-now vs follow-up issue. Be skeptical.
```

#### Pass 4 — system architecture (in-the-large)

**Tier: strong.** Covers former cold-review **Q7–Q9** at system scale.

```text
<shared preamble with N=4>

**Lens — system architecture:** Zoom out beyond this PR's files:
- Dependency direction (gateway vs engine; no inverted imports)
- Where state lives and failure modes (concurrency, restart,
  multi-instance, partial flush/claim)
- Scaling: algorithmic cost, SQLite parameter limits, fan-out under load
- Whether schemas/analytics support the views claimed in ADR/docs
- Frontend/API contract readiness for the stated UX
- Migration/backfill for existing deployments
- Interaction with surrounding RPC/event timing (e.g. FixSession /
  ReportFixCompleted)

Compare ADR/doc claims to what shipped. Label each finding
"fix in this PR" vs "track as issue".
```

#### Pass 5 — adversarial / exposure / simplify

**Tier: strong.** Covers former cold-review **Q2, Q8** plus deliberate simplification.

```text
<shared preamble with N=5>

**Lens — break it / rethink it:** Be creatively adversarial:
- Weird but realistic scenarios that corrupt durable state, double-count
  analytics, or bypass filters
- **Information exposure** — logs, errors, user-facing strings,
  persisted diffs/explanations: credentials, secrets, user content,
  internal paths? Capability grants, CORS origins, container caps —
  minimum necessary?
- Multi-tenant / empty project_id / confused or hostile clients against
  additive API fields
- Time travel: clock skew, flush-then-rebuild inconsistency, partial
  claim failures
- If you deleted 50% of this design, what still ships the goal?

Return: (1) adversarial findings that could be real bugs,
(2) simplify/kill recommendations,
(3) short verdict: converged enough to merge, or what must change first?
```

#### Aggregate, act, converge

After all passes return, the parent agent must:

1. **Deduplicate** findings and build a table: finding → which passes →
   severity → fix-now vs follow-up.
2. **Ship-blockers** = any finding in **≥2 passes**, plus any single-pass
   **critical** or **major** finding (data corruption, security exposure,
   ADR-060 break, invariant violation, or a Pass-rated major defect in
   one lens). Single-pass **medium/low** may be follow-ups; single-pass
   major must be fixed or explicitly demoted with human approval before
   Step 4.
3. **Act on ship-blockers.** Fix code (or demote ADR/PR framing and open
   a tracked follow-up). Re-run `tox -e lint` and `tox -e unit`.
4. **Re-run Rule of Five** after substantive fixes until:
   - no new multi-pass or single-pass major/critical ship-blockers, and
   - Pass 5's verdict is merge-ready (or framing is honestly demoted).
5. Document dismissed findings with a clear technical justification.

Do not proceed to Step 4 until convergence (or an explicit user decision
to accept remaining follow-ups).

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
