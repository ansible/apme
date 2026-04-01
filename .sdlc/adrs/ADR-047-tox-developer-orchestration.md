# ADR-047: tox as Sole Developer Orchestration Tool

## Status

Implemented

## Date

2026-03-30

## Context

Developer tasks are spread across multiple tools with no single entry point:

- **prek** for lint/format/typecheck (`prek run --all-files`)
- **uv run pytest** with four different incantation patterns (unit, integration, AI, UI) each requiring different extras, markers, and coverage flags
- **Shell scripts** in `containers/podman/` for pod lifecycle (build, up, down, CLI)
- **scripts/gen_grpc.sh** for protobuf code generation

A new contributor must discover 5+ tools and 10+ commands, documented inconsistently across `DEVELOPMENT.md`, `CONTRIBUTING.md`, `README.md`, and `containers/podman/README.md`. The coverage threshold is split between `pyproject.toml` (`fail_under = 50`) and the CI workflow (`--cov-fail-under=36`). Despite `requires-python = ">=3.10"`, there is no multi-Python testing.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| tox + tox-uv | Ansible ecosystem standard, named environments, multi-Python, `allowlist_externals` wraps non-Python tasks, tox-uv integrates with `uv.lock` | Additional tool to install |
| nox | Python-native config (noxfile.py), flexible | Less common in Ansible ecosystem, no uv plugin as mature as tox-uv |
| just / Makefile | Language-agnostic, no Python dependency | No virtualenv management, no multi-Python, manual dependency handling |
| hatch | Modern Python project manager, environments built-in | Opinionated about project layout, would replace uv as package manager |
| Status quo | No new tools | Discovery problem persists, no multi-Python, CI/local drift |

## Decision

**Adopt tox with the tox-uv plugin as the sole developer orchestration tool.** Install via `uv tool install tox --with tox-uv`. Every developer-facing task — lint, test, build containers, start the pod, generate protos — is a `tox -e <env>` command.

### Environment layout

| Environment | What it runs | Category |
|-------------|-------------|----------|
| `lint` | `prek run --all-files` | Quality gate |
| `unit` | `pytest` with coverage | Test |
| `integration` | `pytest tests/integration/` | Test |
| `ai` | `pytest` with AI extras | Test |
| `ui` | `pytest -m ui` (Playwright) | Test |
| `grpc` | `scripts/gen_grpc.sh` | Code generation |
| `build` | `containers/podman/build.sh` | Pod lifecycle |
| `up` | `build.sh` + `up.sh` | Pod lifecycle |
| `down` | `containers/podman/down.sh` | Pod lifecycle |
| `cli` | `containers/podman/run-cli.sh` | Pod lifecycle |
| `pm` | Build + start + open browser | Product demo |

### Relationship to existing tools

- **prek (ADR-014)**: Remains the git hook runner. `tox -e lint` delegates to `prek run --all-files`. The single source of truth for hook configuration stays in `.pre-commit-config.yaml`.
- **Shell scripts**: Pod lifecycle scripts stay as-is under `containers/podman/`. tox environments are thin wrappers via `allowlist_externals = bash`. The scripts remain directly callable, but tox is the documented primary path.
- **CI (ADR-015)**: Test workflows call `uvx --with tox-uv tox -e <env>` instead of bespoke `uv sync` + `uv run pytest` sequences. The `prek.yml` lint workflow continues to use `j178/prek-action` directly for its built-in caching; `tox -e lint` is the local equivalent. Every CI check has a corresponding tox environment.

## Rationale

- **Single entry point**: `tox -e <tab>` or `tox l` shows everything a developer can do. No tool discovery problem.
- **Local/CI parity**: CI runs `uvx --with tox-uv tox -e unit` — the same command a developer runs locally. This is the lean-CI principle (ADR-015) applied to all tasks, not just lint.
- **tox-uv integration**: tox-uv uses the existing `uv.lock` for dependency resolution and `uv` for virtualenv creation. No separate dependency specification.
- **Multi-Python**: `tox -e py310-unit,py311-unit,py312-unit` works out of the box. `requires-python = ">=3.10"` can be validated in CI when ready.
- **Ansible ecosystem alignment**: tox is the standard test runner for ansible-core, ansible collections, and most Ansible tooling projects. Contributors from the Ansible ecosystem will recognize it immediately.
- **Non-Python tasks**: `allowlist_externals` cleanly wraps shell scripts and binaries without contorting them into Python. Pod lifecycle and proto generation stay in their natural form.

## Consequences

### Positive

- One tool to learn, one command pattern for all tasks
- CI workflows simplified (remove `uv sync` + bespoke pytest lines)
- Multi-Python testing enabled without additional infrastructure
- Coverage threshold defined once in `tox.ini`, not split between config files
- `tox l` serves as living documentation of available developer tasks

### Negative

- Additional tool to install (`uv tool install tox --with tox-uv`)
- Thin wrapper overhead for pod/grpc environments (mitigated: skip_install = true, near-zero latency)
- prek and tox coexist — two tools, but with clear separation (hooks vs orchestration)

## Related Decisions

- ADR-014: Ruff linter and prek pre-commit hooks (prek stays for hooks)
- ADR-015: GitHub Actions CI with prek (CI parity principle extended to tests)
- ADR-024: Thin CLI with local daemon mode (local dev without Docker)
