# APME Standard Operating Procedures

Consolidated navigation hub for the Ansible Policy & Modernization Engine.

This document provides brief summaries and links to the canonical sources for each area of project practice. It does **not** duplicate those sources — when you need full detail, follow the link.

> **Canonical sources always win.** If anything here conflicts with an ADR, SECURITY.md, AGENTS.md, or CLAUDE.md, the canonical source is authoritative.

---

## Table of Contents

1. [Security](#1-security)
2. [Development Workflow](#2-development-workflow)
3. [Code Quality Standards](#3-code-quality-standards)
4. [Pre-commit and CI Gates](#4-pre-commit-and-ci-gates)
5. [Git Workflow and PR Process](#5-git-workflow-and-pr-process)
6. [Architectural Invariants](#6-architectural-invariants)
7. [Rule Development](#7-rule-development)
8. [Release Process](#8-release-process)
9. [Industry Alignment](#9-industry-alignment)

---

## 1. Security

> This is a **public repository**. Security is a first-class concern.

**Full details:** [SECURITY.md](SECURITY.md) | [ADR-019](.sdlc/adrs/ADR-019-dependency-governance.md)

Key areas covered in SECURITY.md: secrets management (never commit secrets, use env vars), safe coding patterns (no `shell=True` with user input, safe YAML loading, path traversal prevention), container security (non-root, pinned tags), dependency governance (ADR-019 7-question checklist), gRPC security (TLS in production), vulnerability reporting (private disclosure, 48-hour acknowledgment SLA), and incident response (rotate credentials, audit history, scrub with `git filter-repo`).

CI/CD security: pin GitHub Actions to commit SHAs per the [lean-ci skill](.agents/skills/lean-ci/SKILL.md). Never add secrets or publishing steps without maintainer approval.

Every PR must pass the security checklist in [SECURITY.md](SECURITY.md) — no secrets, input validation, safe subprocess calls, non-root containers, `gitleaks` clean.

---

## 2. Development Workflow

APME follows **Spec-Driven Development**: every feature flows through a formal lifecycle before code is written.

**Full details:** [workflow.md](.sdlc/context/workflow.md) | [getting-started.md](.sdlc/context/getting-started.md) | [.sdlc/README.md](.sdlc/README.md)

The lifecycle: Assess (`/sdlc-status`) → Unblock (`/dr-review`) → Specify (`/req-new`) → Execute (`/task-new`). Use `/dr-new` for blocking questions and `/adr-new` for architectural decisions during implementation.

Artifacts live in `.sdlc/specs/REQ-NNN-name/` with requirement.md, design.md, contract.md, and tasks/. Decision Requests go through `.sdlc/decisions/`. ADRs require at least 2 options considered.

**Anti-patterns to avoid:** skipping `/sdlc-status`, letting DRs accumulate, skipping specs, making silent architectural decisions, modifying the codebase to work around local environment issues. If your environment cannot run something, fix the environment or raise a DR.

---

## 3. Code Quality Standards

**Full details:** [conventions.md](.sdlc/context/conventions.md)

Python 3.11+, Ruff formatting (88 chars), mypy strict, Google-style docstrings enforced by pydoclint. Type hints on all function signatures. Use `ruamel.yaml` for YAML (round-trip mode for writes to preserve comments). Custom exception hierarchy (`APMEError` subclasses). Structured logging via `structlog` with `[REDACTED]` for secrets.

### Testing Discipline

These rules supplement the testing standards in [conventions.md](.sdlc/context/conventions.md):

**Stop the daemon before and after test runs.** A running daemon may serve a stale build, causing misleading results. There is no tox environment for daemon lifecycle — run `apme daemon stop` directly as an exception to the tox-only policy:

```
apme daemon stop            # before testing
tox -e unit                 # run tests
apme daemon stop            # after testing
```

**Understand why a test fails before changing it.** When a test fails, investigate the root cause. If the production code is wrong, fix it. If the test itself is wrong (testing implementation details or asserting incorrect behavior after a legitimate refactor), fix the test with a clear explanation. Never weaken assertions, loosen expected values, or delete test cases just to achieve a green run.

---

## 4. Pre-commit and CI Gates

**Full details:** [tox skill](.agents/skills/tox/SKILL.md) | ADR-047 | [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)

**tox is the only way to run things** (ADR-047). Key environments:

| Task | Command |
|------|---------|
| Lint, format, type check | `tox -e lint` |
| Unit tests | `tox -e unit` |
| Proto regeneration | `tox -e grpc` |
| Build containers | `tox -e build` |
| Start/stop pod | `tox -e up` / `tox -e down` |

Never invoke `pytest`, `ruff`, `mypy`, or `prek` directly. The one exception is `apme daemon stop` for test isolation (see Section 3).

Setup: `uv tool install tox --with tox-uv`, then `uv tool install prek && prek install` for git hooks.

---

## 5. Git Workflow and PR Process

**Full details:** [CONTRIBUTING.md](CONTRIBUTING.md) | [submit-pr skill](.agents/skills/submit-pr/SKILL.md) | ADR-016

**Branch strategy:** `main` is the only long-lived branch. Never commit directly to it. For fork contributors (the common case):

```bash
git fetch upstream
git switch --create feat/<slug> upstream/main
```

For direct-push contributors, replace `upstream` with `origin`.

**Conventional Commits** format: `type(scope): description`. Types: `feat`, `fix`, `docs`, `refactor`, `test`, `build`, `ci`, `chore`. Scopes: `engine`, `native`, `opa`, `ansible`, `cli`, `daemon`, `remediation`, `proto`.

**PR requirements:** Summary, Changes, Security Checklist, Test Plan. Test plan should reference `tox -e lint` and `tox -e unit` (not direct tool invocations). Every review comment requires a reply and explicit thread resolution. Green CI is a prerequisite for merge.

---

## 6. Architectural Invariants

See [AGENTS.md — Architectural Invariants](AGENTS.md#architectural-invariants) for the full list of 14 non-negotiable invariants and the Design Thinking rules. If you think an invariant needs to change, write an ADR first.

---

## 7. Rule Development

**Full details:** [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | [docs/LINT_RULE_MAPPING.md](docs/LINT_RULE_MAPPING.md) | [docs/RULE_DOC_FORMAT.md](docs/RULE_DOC_FORMAT.md) | ADR-008

Rule ID prefixes: L = Lint, M = Modernize, R = Risk, P = Policy, SEC = Secrets, EXT- = Plugin. IDs are independent of the implementing validator. Choose between Native (complex Python logic), OPA (declarative structural checks), or Ansible (runtime version checks) based on what the rule needs.

Each new rule requires: implementation, colocated markdown docs with YAML examples, colocated tests, `rule_versions.json` update, lint rule mapping update, and catalog regeneration.

---

## 8. Release Process

**Full details:** [CLAUDE.md — Release Process](CLAUDE.md#release-process)

Bump `pyproject.toml` version, update `CHANGELOG.md`, update container image tags. Verify: `tox -e unit`, `tox -e lint`, security audit (`gitleaks`, `bandit`, `pip-audit`). Tag `vX.Y.Z`, build and push container images.

---

## 9. Industry Alignment

An industry gap analysis comparing APME practices against NIST SSDF v1.2, OpenSSF OSPS Baseline, SLSA, and GitHub hardening guides is maintained separately:

**Full analysis:** [.sdlc/research/industry-gap-analysis.md](.sdlc/research/industry-gap-analysis.md)

The analysis identifies current alignment (10 practices) and gaps (8 high-priority, 5 medium-priority) with specific recommendations. Each gap is tracked as a GitHub issue for individual resolution.

---

## Quick Reference

```
Start of session:
  1. git fetch upstream          — sync with upstream (use 'origin' if direct-push)
  2. /sdlc-status                — check state and blockers
  3. /dr-review                  — resolve any blocking DRs

Before every commit:
  1. Verify you are on a feature branch, NOT main
  2. apme daemon stop            — stop stale daemon
  3. tox -e lint                 — lint, format, type check, docstrings
  4. tox -e unit                 — run tests
  5. apme daemon stop            — clean up daemon

Before every PR:
  1. PR body has Summary, Changes, Security Checklist, Test Plan
  2. CI green
  3. Docs updated if applicable
```

---

*Last updated: 2026-04-02*
