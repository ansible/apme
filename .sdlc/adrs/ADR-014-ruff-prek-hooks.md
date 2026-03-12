# ADR-014: Ruff Linter and prek Pre-commit Hooks

## Status

Accepted

## Date

2026-03

## Context

The project had no linter, code formatter, or pre-commit hooks. Code style inconsistencies and latent issues (unused imports, bare raises, missing context managers, ambiguous variable names) accumulated across the codebase. There was no automated gate to prevent these from entering the repository.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| ruff | Extremely fast (Rust), replaces flake8+isort+pyupgrade+pycodestyle, minimal config, auto-fix | Newer tool, not all plugins ported |
| flake8 + isort + black | Mature ecosystem, widely adopted | Multiple tools to configure and run, slower |
| pylint | Deep analysis, type inference | Very slow, high noise, heavy configuration |

| Option | Pros | Cons |
|--------|------|------|
| prek (pre-commit) | Single Rust binary, no Python dependency, drop-in `.pre-commit-config.yaml` compatibility, faster than pre-commit | Newer tool |
| pre-commit (Python) | Mature, large hook ecosystem | Requires Python, slower hook execution |
| Custom shell script | No external tool | Not standard, no hook management, no caching |

## Decision

**Use ruff for linting and formatting, managed via prek pre-commit hooks.** Configuration lives in `pyproject.toml` under `[tool.ruff]`. The `.pre-commit-config.yaml` uses `astral-sh/ruff-pre-commit` with `ruff` (lint + auto-fix) and `ruff-format` hooks.

## Rationale

- ruff is a single tool that replaces flake8, isort, pyupgrade, and pycodestyle — one config, one dependency
- ruff runs in milliseconds on the full codebase, making it practical as a pre-commit hook
- prek is a faster, dependency-free drop-in for pre-commit — no Python runtime required to run hooks
- `.pre-commit-config.yaml` is the standard format understood by both prek and pre-commit
- All existing violations were remediated at adoption time, so the codebase starts clean

## Consequences

### Positive
- Consistent code style enforced automatically
- Fast feedback loop (milliseconds, not seconds)
- Single tool replaces multiple linters

### Negative
- Newer tools with smaller community (mitigated by Astral's rapid adoption)

## Related Decisions

- ADR-015: GitHub Actions CI with prek
