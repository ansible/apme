---
name: tox
description: >
  Reference for running lint, test, build, and pod commands via tox.
  Agents MUST use tox for all quality gates — never invoke pytest, ruff,
  mypy, prek, or shell scripts directly. This skill is the canonical
  lookup table for which tox environment to use.
argument-hint: "[environment-name]"
user-invocable: true
metadata:
  author: APME Team
  version: 1.0.0
---

# tox — Sole Developer & Agent Orchestration Tool (ADR-047)

## Usage

```
/tox                  # Show full environment reference
/tox lint             # How to run lint
/tox unit             # How to run unit tests
/tox up               # How to start the pod
/tox build-clean      # How to do a clean rebuild
/tox <env>            # Lookup any tox environment
```

tox is the **only** way to run lint, test, build, and pod commands in this
project. Do not invoke `pytest`, `ruff`, `mypy`, `prek`, or shell scripts
directly. Every task maps to a `tox -e <env>` command.

## Hard Rules

1. **Never run `pytest` directly.** Use `tox -e unit`, `tox -e integration`,
   `tox -e ai`, or `tox -e ui`.
2. **Never run `ruff`, `mypy`, or `prek` directly.** Use `tox -e lint`.
3. **Never run `./scripts/gen_grpc.sh` directly.** Use `tox -e grpc`.
4. **Never run `./containers/podman/*.sh` directly.** Use `tox -e build`,
   `tox -e up`, `tox -e down`, or `tox -e cli`.
5. **Pass extra arguments after `--`.** Example: `tox -e unit -- -k test_sbom`.
6. **In CI, use `uvx --with tox-uv tox -e <env>`** instead of installing tox.

## Environment Reference

### Quality gates

| Environment | What it runs | When to use |
|-------------|-------------|-------------|
| `tox -e lint` | `prek run --all-files` (ruff, mypy, pydoclint, uv-lock) | Before every commit. Verification step for all tasks. |

### Test suites

| Environment | What it runs | When to use |
|-------------|-------------|-------------|
| `tox -e unit` | `pytest` with `--cov-fail-under=36` | After any code change. |
| `tox -e unit -- -k <pattern>` | Single test or test pattern | Debugging a specific test. |
| `tox -e unit -- --no-cov` | Tests without coverage overhead | Quick iteration. |
| `tox -e integration` | `pytest tests/integration/` (needs OPA binary) | After engine or validator changes. |
| `tox -e ai` | `pytest` with AI extras (abbenay) | After AI/remediation changes. |
| `tox -e ui` | `pytest -m ui` (Playwright, needs running pod) | After Gateway or UI changes. |

### Code generation

| Environment | What it runs | When to use |
|-------------|-------------|-------------|
| `tox -e grpc` | `scripts/gen_grpc.sh` | After modifying any `.proto` file. |

### Pod lifecycle

| Environment | What it runs | When to use |
|-------------|-------------|-------------|
| `tox -e up` | Build images + start the pod | After any code/config change. The common case. |
| `tox -e up -- --no-cache` | Full rebuild + start | When cached layers are stale. |
| `tox -e build` | `containers/podman/build.sh` | Build images only (no start). |
| `tox -e down` | `containers/podman/down.sh` | Stop the APME pod. |
| `tox -e wipe` | Stop + wipe DB and sessions | Preserve images, wipe state. |
| `tox -e build-clean` | Wipe + rebuild `--no-cache` | Full clean rebuild (no start). |
| `tox -e up-clean` | Wipe + rebuild `--no-cache` + start | Nuclear option — clean slate. |
| `tox -e cli -- <args>` | `containers/podman/run-cli.sh` | Run CLI commands in the pod. |
| `tox -e pm` | Build + start + health-check + open browser | Demo the product. |

### Default set

Running `tox` with no `-e` flag executes: `lint`, `unit`, `integration`, `ai`,
`ui`. This is the full quality gate.

## Common Agent Workflows

### "I changed Python code, what do I run?"

```bash
tox -e lint              # check style + types
tox -e unit              # run tests
```

### "I changed a proto file"

```bash
tox -e grpc              # regenerate stubs
tox -e lint              # check generated code compiles
tox -e unit              # verify nothing broke
```

### "I need to run one specific test"

```bash
tox -e unit -- -k test_scan_detects_fqcn
tox -e unit -- tests/test_validators.py::test_native_rule_discovery
```

### "I need to verify the full pod works"

```bash
tox -e up                        # builds + starts
tox -e cli -- health-check
tox -e cli -- check /workspace
tox -e down
```

### "List all available environments"

```bash
tox l
```

## Installation

```bash
uv tool install tox --with tox-uv
```

## Configuration

All tox configuration lives in `tox.ini` at the repo root. Environment
definitions, extras, pass-through env vars, and commands are all there.
Do not scatter test/lint invocations across Makefiles, scripts, or
workflow YAML.
