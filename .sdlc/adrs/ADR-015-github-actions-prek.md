# ADR-015: GitHub Actions CI with prek

## Status

Accepted

## Date

2026-03

## Context

ADR-014 introduced ruff and prek for local pre-commit hooks, but there was no CI enforcement. Contributors who skip `prek install` or commit with `--no-verify` could merge code that fails lint or format checks.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| j178/prek-action | Official prek action, installs prek + runs hooks in one step, reuses `.pre-commit-config.yaml` | Newer action |
| astral-sh/ruff-action | Official ruff action, well-maintained | Doesn't reuse `.pre-commit-config.yaml`, would duplicate hook config |
| Manual `pip install ruff && ruff check` | No third-party action dependency | Manual setup, duplicates config, no hook caching |

## Decision

**Use j178/prek-action in a GitHub Actions workflow triggered on `pull_request` against `main`.** The action installs prek and runs `prek run --all-files`, reusing the existing `.pre-commit-config.yaml` — no duplicate configuration.

## Rationale

- Single source of truth: CI and local dev both use the same `.pre-commit-config.yaml`
- The action handles prek installation and caching automatically
- PRs that fail ruff lint or format checks are blocked from merging
- Minimal workflow config: checkout + prek-action (two steps)

## Consequences

### Positive
- CI enforces the same checks as local pre-commit hooks
- No configuration drift between local and CI
- PRs blocked on lint/format failures

### Negative
- Dependency on third-party GitHub Action (mitigated by SHA pinning)

## Related Decisions

- ADR-014: Ruff linter and prek pre-commit hooks
- ADR-016: Single-branch `main` strategy
