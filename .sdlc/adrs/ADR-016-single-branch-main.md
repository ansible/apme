# ADR-016: Single-branch `main` Strategy

## Status

Accepted

## Date

2026-03

## Context

The repository used a `master` default branch. The industry has broadly adopted `main` as the default branch name. Additionally, we needed to decide whether to introduce long-lived feature branches, release branches, or other branching strategies.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Single branch (`main`) | Simple, all work merges to one place, easy to reason about | No release isolation |
| Gitflow (`main` + `develop` + release branches) | Release isolation, hotfix support | Complex, overhead for a project without versioned releases yet |
| Trunk-based with short-lived branches | Industry best practice for CI/CD | Requires mature CI — we're getting there |

## Decision

**Rename the default branch from `master` to `main`. Use a single-branch strategy with `main` as the sole long-lived branch.** All work is done on short-lived feature branches forked from `main` and merged back via pull request. No `develop`, `release/*`, or `staging` branches.

Multi-branch strategies (release branches, maintenance branches) will be introduced only when a concrete need arises that cannot be addressed with tags or the single-branch model.

## Rationale

- The project does not yet have versioned releases — there is nothing to isolate
- A single branch eliminates merge conflicts between long-lived branches and reduces cognitive overhead
- Short-lived feature branches + PR review + CI (prek) provide sufficient quality gating
- Tags can mark release points when versioned releases begin
- This decision is explicitly revisable: when multi-branch is required, we adopt it then

## Consequences

### Positive
- Simplified workflow
- No merge conflicts between long-lived branches
- Clear single source of truth

### Negative
- No release isolation (acceptable until versioned releases begin)

## Related Decisions

- ADR-014: Ruff linter and prek pre-commit hooks
- ADR-015: GitHub Actions CI with prek
