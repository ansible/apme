# Development Guide

## Documentation Map

| Document | Scope |
|----------|-------|
| **This file** (`docs/DEVELOPMENT.md`) | Canonical reference for local setup, tox environments, tooling, and pod lifecycle |
| [`CONTRIBUTING.md`](/CONTRIBUTING.md) | PR process, commit conventions, contributor onboarding |
| [`SECURITY.md`](/SECURITY.md) | Security policy and practices |
| [`containers/podman/README.md`](/containers/podman/README.md) | Pod troubleshooting |
| [`README.md`](/README.md) | Product overview and quick start |

## Setup

### Install developer tools

```bash
# tox — sole developer orchestration tool (ADR-047)
uv tool install tox --with tox-uv

# prek — git hooks (runs automatically on commit)
uv tool install prek
prek install

# Install the project (makes `apme` CLI available locally)
uv sync --extra dev --extra gateway
```

### Verify setup

```bash
# List all available tox environments
tox l

# Run lint + typecheck
tox -e lint

# Run unit tests
tox -e unit
```

## tox — Developer Orchestration

tox is the single entry point for all developer tasks. Every CI check has a corresponding tox environment you can run locally.

### Environment reference

| Environment | What it runs | Category |
|-------------|-------------|----------|
| `tox -e lint` | `prek run --all-files` (ruff, mypy, pydoclint, uv-lock) | Quality gate |
| `tox -e unit` | `pytest` with coverage (`--cov-fail-under=36`) | Test |
| `tox -e integration` | `pytest tests/integration/` (requires OPA binary) | Test |
| `tox -e ai` | `pytest` with AI extras (abbenay) | Test |
| `tox -e ui` | `pytest -m ui` (Playwright, requires running gateway + UI) | Test |
| `tox -e grpc` | `scripts/gen_grpc.sh` | Code generation |
| `tox -e build` | `containers/podman/build.sh` | Pod lifecycle |
| `tox -e up` | `build.sh` + `up.sh` | Pod lifecycle |
| `tox -e down` | `containers/podman/down.sh` | Pod lifecycle |
| `tox -e wipe` | `down --wipe` (stop + delete DB/sessions) | Pod lifecycle |
| `tox -e build-clean` | `wipe` + `build --no-cache` | Pod lifecycle |
| `tox -e up-clean` | `wipe` + `build --no-cache` + `up` | Pod lifecycle |
| `tox -e cli` | `containers/podman/run-cli.sh` | Pod lifecycle |
| `tox -e pm` | Build + start + health-check + open browser | Product demo |

### Default environments

Running `tox` with no `-e` flag runs the default list: `lint`, `unit`, `integration`, `ai`, `ui`.

### Passing extra arguments

Use `--` to pass arguments through to the underlying command:

```bash
tox -e unit -- -k test_sbom             # run a single test
tox -e unit -- --no-cov                 # skip coverage
tox -e build -- --no-cache          # rebuild from scratch
tox -e wipe                          # stop + wipe DB/sessions
tox -e cli -- check .               # run CLI check in pod
tox -e cli -- health-check          # run health check in pod
```

## Pre-commit hooks (prek)

prek runs automatically on `git commit`. `tox -e lint` runs the same checks manually.

### What runs

| Hook | What it does |
|------|--------------|
| `ruff` | Lint check (rules: E, F, W, I, UP, B, SIM, D) with `--fix`; D = pydocstyle (Google convention) |
| `ruff-format` | Code formatting |
| `mypy` | Strict type check on `src/`, `tests/`, `scripts/` |
| `pydoclint` | Docstring consistency (Google style, Args/Returns/Raises, no type hints in docstrings) on `src/`, `tests/`, `scripts/` |

Configuration: `[tool.ruff]` and `[tool.ruff.lint.pydocstyle]` (convention = google) in `pyproject.toml`; `[tool.pydoclint]` for style and options. Generated gRPC stubs (`src/apme/v1/*_pb2*.py`) are excluded from ruff.

### CI

Prek runs automatically on pull requests targeting `main` via GitHub Actions (`.github/workflows/prek.yml`). Tests run via tox in `.github/workflows/test.yml`.

### Running ruff directly

```bash
ruff check src/ tests/          # lint
ruff check --fix src/ tests/    # lint + auto-fix
ruff format src/ tests/         # format
ruff format --check src/ tests/ # format check (CI mode)
```

## Code organization

```
src/apme_engine/
├── cli/                    CLI package (thin gRPC presentation layer)
│   ├── __init__.py         main() entry point, subcommand dispatch
│   ├── __main__.py         python -m apme_engine.cli shim
│   ├── parser.py           build_parser() — all argparse definitions
│   ├── check.py            Check subcommand (FixSession in check mode, ADR-039)
│   ├── format_cmd.py       Format subcommand (FormatStream RPC)
│   ├── remediate.py        Remediate subcommand (FixSession bidi stream, ADR-028)
│   ├── health.py           Health-check subcommand
│   ├── daemon_cmd.py       daemon start/stop/status
│   ├── discovery.py        resolve_primary() — gRPC channel setup
│   ├── output.py           Human-readable / structured CLI output
│   ├── ansi.py             Zero-dependency ANSI styling (NO_COLOR/FORCE_COLOR)
│   ├── _convert.py         Internal proto ↔ dict conversion
│   └── _models.py          Internal DTOs
├── runner.py               run_scan() → ScanContext
├── formatter.py            YAML formatter (format_content)
├── opa_client.py           OPA eval (Podman or local binary)
│
├── engine/                 ARI-based scanner
│   ├── scanner.py          ARIScanner.evaluate() pipeline
│   ├── parser.py           YAML/Ansible content parser
│   ├── models.py           SingleScan, TaskCall, RiskAnnotation, etc.
│   ├── findings.py         Finding/violation structures
│   ├── content_graph.py    ContentGraph DAG model (ADR-044)
│   ├── graph_scanner.py    GraphRule evaluation engine
│   └── graph_opa_payload.py  OPA hierarchy from ContentGraph
│
├── validators/
│   ├── base.py             Validator protocol + ScanContext
│   ├── native/             GraphRule-based Python rules
│   │   ├── __init__.py     Rule discovery, rules_dir helper
│   │   ├── rules/          one GraphRule per rule + .md docs
│   │   │   ├── *_graph.py  GraphRule implementations
│   │   │   ├── *.md        Rule documentation with examples
│   │   │   └── rule_versions.json
│   │   └── README.md
│   ├── opa/
│   │   ├── __init__.py     OPA validator
│   │   └── bundle/         Rego rules + tests + data
│   │       ├── _helpers.rego
│   │       ├── L003.rego ... L025.rego, M006/M008/M009/M011, R118
│   │       ├── *_test.rego (colocated)
│   │       ├── data.json
│   │       └── README.md
│   ├── ansible/
│   │   ├── __init__.py     AnsibleValidator
│   │   ├── _venv.py        venv resolution
│   │   └── rules/          L057–L059, M001–M004 + .md docs
│   └── gitleaks/
│       ├── __init__.py
│       └── scanner.py      gitleaks binary wrapper, vault/Jinja filtering
│
├── remediation/            Remediation engine (Tier 1 transforms + Tier 2 AI)
│   ├── engine.py           RemediationEngine (convergence loop)
│   ├── partition.py        is_finding_resolvable(), classify_violation()
│   ├── registry.py         TransformRegistry
│   ├── ai_provider.py      AIProvider Protocol, AIProposal dataclass
│   ├── abbenay_provider.py AbbenayProvider (default AI impl via abbenay_grpc)
│   ├── enrich.py           Enrich violations/context for remediation
│   ├── structured.py       Structured remediation payloads
│   ├── unit_segmenter.py   Split content into task snippets for AI
│   └── transforms/         Per-rule deterministic fix functions
│       ├── __init__.py     auto-registers all transforms
│       ├── _helpers.py     Shared transform helpers
│       └── L007_*, L021_*, L046_*, M001_*, M006_*, M008_*, M009_*, ...
│
├── data/
│   └── ansible_best_practices.yml  structured best practices for AI prompts
│
├── daemon/                 async gRPC servers (all use grpc.aio)
│   ├── primary_server.py   Primary orchestrator (engine + fan-out + remediation)
│   ├── primary_main.py     entry point: apme-primary (asyncio.run)
│   ├── native_validator_server.py   (async, CPU work in run_in_executor)
│   ├── native_validator_main.py
│   ├── opa_validator_server.py      (async, OPA via subprocess in executor)
│   ├── opa_validator_main.py
│   ├── ansible_validator_server.py  (async, session venvs from /sessions)
│   ├── ansible_validator_main.py
│   ├── gitleaks_validator_server.py (async, subprocess in executor)
│   ├── gitleaks_validator_main.py
│   ├── launcher.py         Local multi-service daemon (start/stop/status)
│   ├── session.py          FixSession state management (SessionStore)
│   ├── chunked_fs.py       Chunked file streaming + .apmeignore filtering
│   ├── health_check.py     Health check utilities
│   └── violation_convert.py  dict ↔ proto Violation conversion
│
└── venv_manager/           Session venv management
    └── session.py           VenvSessionManager lifecycle (galaxy proxy installs)
```

## Adding a new rule

### Native (Python) GraphRule

Native rules use the `GraphRule` base class, which operates on the `ContentGraph` DAG.

1. Create `src/apme_engine/validators/native/rules/L0XX_rule_name_graph.py`:

```python
"""GraphRule L0XX: Short description of the rule."""

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult


@dataclass
class RuleNameGraphRule(GraphRule):
    """Detect some condition in the content graph.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L0XX"
    description: str = "Short description"
    enabled: bool = True
    name: str = "RuleName"
    version: str = "v0.0.1"
    severity: str = Severity.VERY_LOW
    tags: tuple[str, ...] = (Tag.DEPENDENCY,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Return True if this rule applies to the given node.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node should be evaluated by process().
        """
        node = graph.get_node(node_id)
        return node is not None and node.node_type == NodeType.TASK

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Evaluate the rule against the node.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with verdict=True when violated, else verdict=False.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        if some_condition(node):
            return GraphRuleResult(
                verdict=True,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        return GraphRuleResult(
            verdict=False,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
```

2. Create colocated test in `tests/` or as a colocated `*_test.py`.

3. Create rule doc `src/apme_engine/validators/native/rules/L0XX_rule_name.md` (see [RULE_DOC_FORMAT.md](RULE_DOC_FORMAT.md)).

4. Add the rule ID to `rule_versions.json`.

5. Update `docs/LINT_RULE_MAPPING.md` with the new entry.

### OPA (Rego) rule

1. Create `src/apme_engine/validators/opa/bundle/L0XX_rule_name.rego`:

```rego
package apme.rules

import data.apme.helpers

L0XX_violations[v] {
    node := input.hierarchy[_].nodes[_]
    node.type == "taskcall"
    # rule logic
    v := helpers.violation("L0XX", "warning", "Description", node)
}

violations[v] {
    L0XX_violations[v]
}
```

2. Create colocated test `src/apme_engine/validators/opa/bundle/L0XX_rule_name_test.rego`:

```rego
package apme.rules

test_L0XX_violation {
    result := violations with input as {"hierarchy": [{"nodes": [...]}]}
    count({v | v := result[_]; v.rule_id == "L0XX"}) > 0
}

test_L0XX_pass {
    result := violations with input as {"hierarchy": [{"nodes": [...]}]}
    count({v | v := result[_]; v.rule_id == "L0XX"}) == 0
}
```

3. Create rule doc `src/apme_engine/validators/opa/bundle/L0XX.md`.

### Ansible rule

Ansible rules live in `src/apme_engine/validators/ansible/rules/` and typically require the Ansible runtime (subprocess calls to `ansible-playbook`, `ansible-doc`, or Python imports from ansible-core). Create a `.md` doc for each rule.

## Proto / gRPC changes

Proto definitions: `proto/apme/v1/*.proto`

After modifying a `.proto` file, regenerate stubs:

```bash
tox -e grpc
```

This generates `*_pb2.py` and `*_pb2_grpc.py` in `src/apme/v1/`. Generated files are checked in.

To add a new service:

1. Create `proto/apme/v1/newservice.proto`
2. Add it to the `PROTOS` array in `scripts/gen_grpc.sh`
3. Run `tox -e grpc`
4. Implement the servicer in `src/apme_engine/daemon/`
5. Add an entry point in `pyproject.toml`

## Testing

### Running tests via tox

```bash
tox -e unit                          # unit tests with coverage
tox -e unit -- -k test_validators    # specific test
tox -e unit -- --no-cov              # skip coverage
tox -e integration                   # integration tests (requires OPA binary)
tox -e ai                            # AI extra tests
tox -e ui                            # Playwright UI tests
tox                                  # run all default environments
```

### Test structure

```
tests/
├── test_opa_client.py             OPA client + Rego eval tests
├── test_scanner_hierarchy.py      Engine hierarchy tests
├── test_formatter.py              YAML formatter tests (transforms, idempotency)
├── test_validators.py             Validator tests
├── test_validator_servicers.py    async gRPC servicer tests (pytest-asyncio)
├── test_session_venv_e2e.py           Session venv + galaxy proxy e2e tests
├── test_rule_doc_coverage.py      Asserts every rule has a .md doc
├── rule_doc_parser.py             Parses rule .md frontmatter
├── rule_doc_integration_test.py   Runs .md examples through engine
├── conftest.py                    Shared fixtures
└── integration/
    ├── test_e2e.sh                End-to-end container test
    └── test_playbook.yml          Sample playbook for e2e

src/apme_engine/validators/native/rules/
    *_test.py                      Colocated native rule tests
```

### OPA Rego tests

Rego tests run via the OPA binary (Podman or local):

```bash
podman run --rm \
  -v "$(pwd)/src/apme_engine/validators/opa/bundle:/bundle:ro,z" \
  --userns=keep-id -u root \
  docker.io/openpolicyagent/opa:latest test /bundle -v
```

### Coverage target

Coverage is configured at 50% (`fail_under = 50` in `pyproject.toml`). CI and tox run with `--cov-fail-under=36` as a lower floor; the pyproject.toml target is the ratchet goal. Ratchet up as tests are added. Rule files under `validators/*/rules/` are excluded from coverage measurement (they have colocated tests instead).

## Pod lifecycle

### Quick start

```bash
tox -e up       # build images and start the pod
tox -e cli      # default: apme check .
```

### Full reference

```bash
tox -e up                        # build images and start the pod
tox -e up -- --no-cache          # rebuild from scratch and start
tox -e build                     # build images only (no start)
tox -e build-clean               # wipe DB/sessions + rebuild --no-cache
tox -e up-clean                  # wipe + rebuild + start (clean slate)
tox -e down                      # stop the pod
tox -e wipe                          # stop + wipe DB and session cache
tox -e cli -- check .            # run check in pod
tox -e cli -- health-check       # health check all services
tox -e pm                            # build, start, wait, open browser
```

The underlying scripts in `containers/podman/` remain directly callable for debugging and low-level troubleshooting, but tox is the expected interface for routine work. See `containers/podman/README.md` for troubleshooting details.

## YAML formatter

The `format` subcommand normalizes YAML files to a consistent style before semantic analysis. This is Phase 1 of the remediation pipeline.

### Transforms applied

1. **Tab removal** — converts tabs to 2-space indentation
2. **Key reordering** — `name` first, then module/action, then conditional/loop/meta keys
3. **Jinja spacing** — normalizes `{{foo}}` to `{{ foo }}`
4. **Indentation** — ruamel.yaml round-trip enforces 2-space map indent and 4-space sequence indent (with dash offset 2) for nested sequences, matching ansible-lint style; root-level sequences remain at column 0

### Usage

```bash
# Show diffs without changing files
apme format /path/to/project

# Apply formatting in place
apme format --apply /path/to/project

# CI mode: exit 1 if any file needs formatting
apme format --check /path/to/project

# Exclude patterns
apme format --apply --exclude "vendor/*" "tests/fixtures/*" .
```

### Remediate pipeline

The `remediate` subcommand chains format → idempotency check → re-check → modernize:

```bash
apme remediate /path/to/project        # apply Tier 1 fixes
apme remediate --ai /path/to/project   # include AI proposals (Tier 2)
apme check --diff /path/to/project     # preview changes without applying
```

This runs the formatter, verifies idempotency (a second format pass produces zero diffs), re-checks the project, then applies Tier 1 deterministic transforms from the transform registry in a convergence loop (check → remediate → re-check until stable). Uses the `FixSession` bidirectional streaming RPC (ADR-028, ADR-039).

### gRPC Format RPC

The Primary service exposes `Format` (unary) and `FormatStream` (streaming) RPCs with `FileDiff` messages. The CLI uses `FormatStream` to stream files to the Primary and receive diffs back.

## Concurrency model

All gRPC servers use `grpc.aio` (fully async). When writing new servicers:

- Servicer methods must be `async def`
- CPU-bound work (rule evaluation, engine scan) goes in `await loop.run_in_executor(None, fn)`
- I/O-bound work uses async libraries
- Each server sets `maximum_concurrent_rpcs` to control backpressure

Every validator receives `request.request_id` and should include it in log output (`[req=xxx]`) for end-to-end tracing across concurrent requests. Echo it back in `ValidateResponse.request_id`.

The OPA validator invokes `opa eval` via subprocess (not REST) — see AGENTS.md invariant #9.

The Ansible validator uses session-scoped venvs provided by the Primary (read-only via `/sessions` volume). Warm sessions pay near-zero cost; cold sessions are built once by the Primary's `VenvSessionManager`.

## Diagnostics

Every validator collects per-rule timing data and returns it in `ValidateResponse.diagnostics`. The Primary aggregates engine timing + all validator diagnostics into `ScanDiagnostics` on the `ScanResponse`.

### CLI verbosity flags

```bash
# Summary: engine time, validator summaries, top 10 slowest rules
apme check -v .

# Full breakdown: per-rule timing for every validator, metadata, engine phases
apme check -vv .

# JSON output includes diagnostics when -v or -vv is set
apme check -v --json .
```

### Color output

Check results use ANSI styling (summary box, severity badges, tree view). Color is auto-detected via TTY and respects the [no-color.org](https://no-color.org) standard:

```bash
# Disable color via environment variable (any value, including empty string)
NO_COLOR=1 apme check .

# Force color in non-TTY contexts (CI pipelines)
FORCE_COLOR=1 apme check .

# Disable color via CLI flag
apme check --no-ansi .
```

### Adding diagnostics to a new validator

When implementing a new `ValidatorServicer`:

1. Time each rule or phase using `time.monotonic()`
2. Build `common_pb2.RuleTiming` entries for each rule
3. Build a `common_pb2.ValidatorDiagnostics` with `validator_name`, `total_ms`, `files_received`, `violations_found`, `rule_timings`, and any validator-specific `metadata`
4. Set `diagnostics=diag` on the `ValidateResponse`

The Primary automatically collects diagnostics from all validators and includes them in `ScanDiagnostics`.

## Deprecation pipeline

The project includes automated tooling to discover ansible-core deprecation notices and identify gaps in APME rule coverage.

### Scripts

| Script | Purpose |
|--------|---------|
| `scripts/scrape_ansible_deprecations.py` | Clones ansible-core devel, scans for `display.deprecated()`, `# deprecated:`, and `_tags.Deprecated()` patterns, compares against existing APME rules, and outputs a gap report |

### Running locally

```bash
# Full scrape + gap analysis (outputs JSON to stdout)
python scripts/scrape_ansible_deprecations.py

# Filter to content-author deprecations >= 2.21
python scripts/scrape_ansible_deprecations.py --min-version 2.21 --audience content

# Save reports to files
python scripts/scrape_ansible_deprecations.py --output-json gaps.json --output-md gaps.md

# Use an existing ansible-core checkout (skip clone)
python scripts/scrape_ansible_deprecations.py --skip-clone --cache-dir /path/to/ansible
```

### How it works

1. **Scrape** — clones/updates ansible-core devel and extracts all deprecation notices
2. **Inventory** — scans existing APME rules (OPA, native, ansible validators) by rule_id, description, and keywords
3. **Compare** — matches each deprecation against the rule inventory to find gaps
4. **Report** — for each uncovered deprecation, generates a detailed rule spec including detection hints, YAML keys to check, recommended validator type, and source context

### CI workflow

The `.github/workflows/deprecation-scrape.yml` workflow runs monthly (or on manual dispatch), scrapes for new deprecations, and creates a GitHub issue if any gaps are found. Maintainers can then use the detailed rule specs in the issue to implement new rules.

## Rule ID conventions

| Prefix | Category | Examples |
|--------|----------|----------|
| **L** | Lint (style, correctness, best practice) | L002–L059 |
| **M** | Modernize (ansible-core metadata) | M001–M030 |
| **R** | Risk/security (annotation-based) | R101–R501, R118 |
| **P** | Policy (legacy, superseded by L058/L059) | P001–P004 |

Rule IDs are independent of the validator that implements them. The user sees rule IDs; the underlying validator is an implementation detail.

See [LINT_RULE_MAPPING.md](LINT_RULE_MAPPING.md) for the complete cross-reference.

## Entry points

Defined in `pyproject.toml`:

| Command | Module | Purpose |
|---------|--------|---------|
| `apme` | `apme_engine.cli:main` | CLI (check, format, remediate, health-check) |
| `apme-primary` | `apme_engine.daemon.primary_main:main` | Primary daemon |
| `apme-native-validator` | `apme_engine.daemon.native_validator_main:main` | Native validator daemon |
| `apme-opa-validator` | `apme_engine.daemon.opa_validator_main:main` | OPA validator daemon |
| `apme-ansible-validator` | `apme_engine.daemon.ansible_validator_main:main` | Ansible validator daemon |
| `apme-gitleaks-validator` | `apme_engine.daemon.gitleaks_validator_main:main` | Gitleaks validator daemon |
