---
name: lean-ci
description: >
  Guide for writing and modifying GitHub Actions workflows in this repository.
  Use when creating CI/CD pipelines, adding workflow jobs, modifying build steps,
  or debugging CI failures. Enforces the project's lean CI philosophy.
argument-hint: "[workflow-name]"
user-invocable: true
metadata:
  author: APME Team
  version: 2.0.0
---

# Lean CI

This project follows a strict "CI as thin wrapper" philosophy. GitHub Actions
workflows must never contain substantive build logic. All logic lives in
locally-runnable tox environments; CI just calls them.

## Principles

1. **Every CI step must be reproducible locally.** A developer should be able to
   run the exact same command on their laptop. If a step only works inside
   GitHub Actions, it violates this rule.

2. **Workflows call tox environments, not inline shell.** Build and test logic
   belongs in `tox.ini` environments -- never in multi-line YAML `run:` blocks.
   CI runs `uvx --with tox-uv tox -e <env>`.

3. **No scattered version pinning.** Python version is in `pyproject.toml`
   (`requires-python`). Tool versions are managed in `.pre-commit-config.yaml`
   (ruff, mypy) and `pyproject.toml` (deps). Not in workflow YAML.

4. **Minimal setup actions.** `astral-sh/setup-uv` and `actions/checkout` only.
   No `actions/setup-python` (uv handles it). No other setup actions without
   explicit justification.

5. **Pin actions to commit SHAs.** Mutable tags (`@v4`) allow upstream changes
   to affect CI without review. Always pin to a full commit SHA with a comment
   noting the tag (ADR-015).

## tox as orchestration layer (ADR-047)

tox is the sole developer orchestration tool. Every CI step maps to a tox
environment that developers run locally.

| tox environment | What it does | CI workflow |
|-----------------|-------------|-------------|
| `tox -e lint` | Lint, format, type check (prek: ruff + mypy + pydoclint) | `prek.yml` |
| `tox -e unit` | Unit tests with coverage (`--cov-fail-under=36`) | `test.yml` |
| `tox -e integration` | Integration tests (requires OPA binary) | `test.yml` |
| `tox -e ai` | AI extra tests (abbenay) | `test.yml` |
| `tox -e ui` | Playwright UI tests | `test.yml` |
| `tox -e grpc` | Regenerate gRPC stubs | manual |
| `tox -e build` | Build container images | `container-images.yml` (GHCR) |
| `tox -e up` | Start the APME pod | manual |
| `tox -e down` | Stop the APME pod | manual |
| `tox -e pm` | Build + start + open browser | manual |

Install: `uv tool install tox --with tox-uv`

## Workflow structure

CI has five workflows in `.github/workflows/`:

- **prek.yml**: Runs `prek` (ruff lint, ruff format, mypy strict, pydoclint,
  uv-lock). Quality gate for code style and type safety.
- **test.yml**: Runs `tox -e unit`, `tox -e integration`, `tox -e ui`, and
  `tox -e ai` as separate jobs. Quality gate for correctness. Coverage threshold
  is enforced via `--cov-fail-under` in `tox.ini`.
- **container-images.yml**: Builds and pushes container images to GHCR on push
  to `main`.
- **deprecation-scrape.yml**: Monthly cron scraping ansible-core for deprecation
  gaps.
- **pr-feedback.yml**: Labels PRs with failing checks or merge conflicts.

`prek.yml` and `test.yml` trigger on `pull_request` targeting `main` and use
`concurrency` groups with `cancel-in-progress` to avoid stacking runs on rapid
pushes.

## Rules for modifications

When adding or modifying CI:

- **DO** add new build logic as a tox environment in `tox.ini`, then call it
  from the workflow with `uvx --with tox-uv tox -e <env>`.
- **DO** use SHA-pinned actions with a tag comment (e.g.,
  `actions/checkout@de0fac2e...  # v6`).
- **DO** set `FORCE_COLOR: 1` and `PY_COLORS: 1` as workflow-level env vars
  for readable CI logs.
- **DO** use `ubuntu-24.04` explicitly rather than `ubuntu-latest`.
- **DO NOT** put multi-line shell scripts in `run:` blocks. If it needs more
  than one command, it belongs in a tox environment or a script in `scripts/`.
  The git dirty check is the one exception -- it is a CI-only guard with no
  local equivalent.
- **DO NOT** add `actions/setup-python` or other setup actions. `setup-uv`
  handles the Python toolchain.
- **DO NOT** hardcode tool versions in YAML. Versions belong in
  `.pre-commit-config.yaml` or `pyproject.toml`.
- **DO NOT** add secrets or publishing steps without explicit approval.
