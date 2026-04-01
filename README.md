# Ansible Forward (APME Engine)

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

> **WARNING: Proof-of-Concept / Rapid Prototype**
>
> This project is in an early, experimental state. APIs, CLI flags, gRPC
> contracts, configuration formats, and internal architecture are all
> **unstable and subject to breaking changes without notice**. There are no
> stability guarantees, no migration paths between versions, and no
> backward-compatibility commitments at this time.
>
> Do not depend on any interface or behavior remaining the same between
> commits. If you are evaluating this project, expect things to move fast
> and break often.

Ansible Policy & Modernization Engine — a multi-validator static analysis platform for Ansible content. It parses playbooks, roles, collections, and task files into a structured hierarchy, then fans validation out in parallel across four independent backends (OPA/Rego, native Python, Ansible-runtime, and Gitleaks) to produce a single, unified list of violations.

## What APME is

APME is a **static and semi-static analysis tool** for Ansible content. It reads your YAML, reasons about structure and module usage, and reports what it finds — without running tasks or executing against target hosts.

It answers questions like:

- Will this playbook **parse** on ansible-core 2.19?
- Are any modules I use **removed, deprecated, or redirected**?
- Do my module arguments **match the argspec** for the version I'm targeting?
- Does my code follow **organizational style and security policies**?
- Are there **hardcoded credentials** in my project?
- What **migration work** is required to move from one ansible-core version to another?

## What APME is not

APME is not a test framework, a deployment tool, or a runtime verification system.

It cannot tell you whether a playbook will **achieve its desired outcome** on your infrastructure. A playbook's intended outcome — packages installed, services configured, files in the right state — depends on target host state, inventory variables, network reachability, external APIs, and runtime facts that only exist during execution. No static analysis tool can evaluate those.

APME also cannot guarantee a playbook will **run successfully**, even if it reports zero violations. A clean APME scan means no *known* incompatibilities were detected — not that every runtime path will succeed.

## Where APME fits

APME provides a **compatibility and quality floor**. It catches the preventable mistakes — the removed module, the broken `include:`, the wrong argument name, the committed secret — before they reach staging or production.

| When discovered | Cost |
|----------------|------|
| APME scan in CI | Developer fixes it in their branch |
| Syntax check in staging | Deployment blocked, team context-switches |
| Production run fails | Outage, incident response, postmortem |

For organizations managing hundreds of roles and collections across ansible-core version upgrades, this shift-left is the difference between a planned migration and an emergency one.

**What still requires execution-time tools:**

| Concern | Tool |
|---------|------|
| Does the playbook produce the correct end state? | Molecule, integration tests |
| Is the playbook idempotent (safe to run twice)? | `--check` mode, Molecule converge+idempotence |
| Do templates render correctly with real variables? | Integration tests against test inventory |
| Does the playbook handle failure paths gracefully? | Molecule verify, side-effect testing |
| Does it work with my specific inventory and vault? | Staging environment dry-run |

APME and these tools are complementary. APME runs in seconds without infrastructure and catches structural and compatibility issues early. Execution-time tools validate behavior and correctness against real systems. Use both.

## Architecture at a glance

```
┌─────────┐      gRPC       ┌────────────┐      gRPC (parallel)      ┌────────────┐
│   CLI   │ ──────────────► │  Primary   │ ──────────────────────►   │   Native   │ :50055
│ (on-the │  ScanRequest    │ (orchestr) │   ValidateRequest         │  (Python)  │
│  -fly)  │  chunked fs     │            │ ┌─────────────────────►   ├────────────┤
└─────────┘                 │   Engine   │ │                         │    OPA     │ :50054
     ▲                      │  ┌──────┐  │ │  ┌──────────────────►   │  (Rego)    │
     │   ScanResponse       │  │parse │  │ │  │                      ├────────────┤
     │   violations         │  │annot.│  │ │  │  ┌───────────────►   │  Ansible   │ :50053
     └──────────────────────│  │hier. │  ├─┘  │  │                   │ (runtime)  │
                            │  └──────┘  ├────┘  │                   ├────────────┤
                            └────────────┘───────┘                   │  Gitleaks  │ :50056
                                 │                                   │ (secrets)  │
                            ┌────┴────┐                              └────────────┘
                            │ Galaxy  │ :8765
                            │ Proxy   │ (PEP 503)
                            └─────────┘
```

Six app containers, one pod. All inter-service communication is gRPC. The Galaxy Proxy serves Ansible collections as Python wheels (PEP 503). The CLI is run on-the-fly with the project directory mounted. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full design.

## Key features

- **Single parse, multiple validators** — the engine parses Ansible content once and produces a hierarchy payload + scandata; validators consume it independently.
- **Parallel fan-out** — Primary calls Native, OPA, Ansible, and Gitleaks validators concurrently via `asyncio.gather()`; total latency = max(validators), not sum.
- **Unified gRPC contract** — every validator implements the same `Validator` service (`validate.proto`); adding a new validator means implementing one RPC.
- **100+ rules** across four backends: OPA Rego (L003–L025, L061–L072, M006/M008/M009/M011, R118), native Python (L026–L105, M005/M010, R101–R501), Ansible runtime (L057–L059, M001–M004), Gitleaks (SEC:* — 800+ secret patterns).
- **Secret scanning** — Gitleaks binary wrapped in gRPC; scans all project files for hardcoded credentials, API keys, private keys. Vault-encrypted files and Jinja2 expressions are automatically filtered.
- **Multi ansible-core version support** — the Primary orchestrator builds session-scoped venvs per ansible-core version (UV-cached); argspec and deprecation checks run against the requested version. Venvs are shared read-only with validators via a `/sessions` volume.
- **Structured diagnostics** — every validator reports per-rule timing data via the gRPC contract; use `-v` for summaries or `-vv` for full per-rule breakdowns.
- **Galaxy Proxy** — converts Ansible Galaxy collection tarballs into pip-installable Python wheels (PEP 503/427); collections are `uv pip install`'d into session venvs, leveraging standard Python caching and dependency resolution.
- **YAML formatter** — normalize indentation, key ordering, Jinja spacing, and tab removal with comment preservation. Idempotent by design; runs as a pre-pass before semantic fixes.
- **Colocated tests** — every rule has a `*_test.py` (native), `*_test.rego` (OPA), or `.md` doc with violation/pass examples usable as integration tests.

## Quick start

### Local development (no containers)

```bash
# Install tox (one-time)
uv tool install tox --with tox-uv

# Run a check (user-facing); engine runs the internal scan pipeline
apme check /path/to/playbook-or-project

# JSON output
apme check --json .

# Diagnostics: summary + top 10 slowest rules
apme check -v .

# Diagnostics: full per-rule breakdown
apme check -vv .

# Format YAML files (show diff)
apme format /path/to/project

# Format and apply changes in place
apme format --apply /path/to/project

# CI check mode (exit 1 if changes needed)
apme format --check /path/to/project

# Full remediate pipeline: format → idempotency check → re-scan → modernize
apme remediate /path/to/playbook-or-project

# AI-assisted remediation (requires Abbenay daemon)
apme remediate --ai /path/to/playbook-or-project

# AI with auto-approve (no interactive review)
apme remediate --ai --auto-approve /path/to/playbook-or-project
```

### Container deployment (Podman)

The full pod runs 9 containers: engine services (Primary, Native, OPA, Ansible,
Gitleaks, Galaxy Proxy), Gateway (REST API + persistence), UI (React dashboard),
and Abbenay (AI provider). Pod management scripts (`build.sh`, `up.sh`,
`down.sh`, `wait-for-pod.sh`) run from the **repo root**. The CLI helper
`run-cli.sh` runs from the directory you want to scan.

```
Container        Port   Role
─────────────────────────────────────────────────────
primary          50051  Engine orchestrator (gRPC)
native           50055  Python rule validator (gRPC)
opa              50054  Rego rule validator (gRPC)
ansible          50053  Ansible runtime validator (gRPC)
gitleaks         50056  Secret scanner (gRPC)
galaxy-proxy      8765  Collection → wheel proxy (PEP 503)
gateway     8080/50060  REST API + WebSocket + persistence
ui                8081  React dashboard (nginx)
abbenay          50057  AI provider (gRPC)
```

#### Build and start

```bash
tox -e up                    # build all images and start the pod
tox -e up -- --no-cache      # rebuild from scratch
tox -e up-clean              # wipe state + rebuild + start (clean slate)
```

This builds a shared base image, nine service images, pulls the Abbenay AI
image, then starts all containers. Cache defaults to
`${XDG_CACHE_HOME:-$HOME/.cache}/apme` (override with `APME_CACHE_HOST_PATH`).

#### Access the UI

Once the pod is running, open **http://localhost:8081** in your browser.
The UI proxies API calls to the Gateway on port 8080. No authentication
is required for local development.

The dashboard provides project management, live scan/remediate operations
with real-time progress, interactive AI proposal review, dependency
tracking, and cross-project analytics. See
[.sdlc/research/ui-capabilities-assessment.md](.sdlc/research/ui-capabilities-assessment.md)
for a full capabilities inventory.

#### Run CLI scans (on-the-fly container)

The CLI container is **not** part of the pod — it joins the pod network
on each invocation with your current directory mounted at `/workspace`.

```bash
tox -e cli                              # default: check .
tox -e cli -- check --json .            # JSON output
tox -e cli -- remediate .               # Tier 1 deterministic fixes
tox -e cli -- remediate --ai .          # include AI proposals (Tier 2)
tox -e cli -- format --check .          # YAML format dry-run
tox -e cli -- health-check              # health check all services
```

#### AI setup (optional)

To enable AI-assisted remediation, create `containers/abbenay/.env` from
the example and add your API key:

```bash
cp containers/abbenay/.env.example containers/abbenay/.env
# Edit .env: set OPENROUTER_API_KEY=your-key
```

The `up.sh` script sources this file automatically. The Abbenay container
starts on port 50057 and Primary connects to it for Tier 2 AI proposals.

#### Stop the pod

```bash
tox -e down                              # stop and remove pod
tox -e wipe                              # also delete database + session cache
```

#### Health check

```bash
apme health-check                        # local daemon
tox -e cli -- health-check               # from pod
```

## AI escalation

APME can escalate Tier 2 violations (no deterministic transform) to an AI provider for proposed fixes. This requires the [Abbenay](https://github.com/redhat-developer/abbenay) daemon.

### Prerequisites

```bash
pip install apme-engine[ai]

# Consumer auth token (required for inline policy)
export APME_ABBENAY_TOKEN="your-token"
```

### Binary daemon

```bash
# Start Abbenay daemon (auto-discovers socket at $XDG_RUNTIME_DIR/abbenay/)
abbenay daemon start
# Or from a Sea binary:
./abbenay-daemon-linux-x64 start

# Set consumer auth token for inline policy (required)
export APME_ABBENAY_TOKEN="your-token"

# Remediate with AI
apme remediate --ai /path/to/playbook-or-project
```

### Container daemon

See the [Abbenay container documentation](https://github.com/redhat-developer/abbenay/blob/main/docs/CONTAINER.md) for full container setup instructions.

```bash
# Pull the pre-built multi-arch image (amd64 + arm64)
podman pull ghcr.io/redhat-developer/abbenay:latest

# Standalone: Abbenay defaults to gRPC on :50051.
# When running inside the APME pod, use -p 50057:50051 to avoid
# colliding with APME Primary (also :50051).
podman run -d --name abbenay \
  -v ./config.yaml:/home/abbenay/.config/abbenay/config.yaml:ro \
  -e OPENROUTER_API_KEY="$OPENROUTER_API_KEY" \
  -p 8787:8787 -p 50057:50051 \
  ghcr.io/redhat-developer/abbenay:latest

# Point APME at the Abbenay container via gRPC TCP
APME_ABBENAY_ADDR=localhost:50057 apme remediate --ai .
```

### CLI flags

| Flag | Description |
|------|-------------|
| `--ai` | Enable AI escalation (opt-in) |
| `--auto-approve` | Approve all AI proposals without prompting (CI mode) |
| `--max-passes N` | Max convergence passes (default: 5) |
| `--json` | Output structured data payloads as JSON |
| `--session ID` | Explicit session ID for venv reuse (default: auto-derived from project root) |

### Remediation flow

1. **Tier 1 (deterministic)**: convergence loop applies transforms until stable
2. **Tier 2 (AI)**: remaining violations are sent to the AI provider one at a time; each proposal is re-validated, cleaned with Tier 1 transforms, and retried with feedback if needed
3. **Interactive review**: accepted proposals are applied (or previewed with `check --diff`)
4. **Tier 3 (manual)**: violations that neither transforms nor AI can fix are reported for human review

## Scaling

Scale pods, not services within a pod. Each pod is a self-contained stack that can process check and remediate workloads end-to-end. For more throughput, run multiple pods behind a load balancer. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#scaling).

## Tests

```bash
# Install tox (one-time setup)
uv tool install tox --with tox-uv

# Unit tests with coverage
tox -e unit

# Integration tests (requires OPA binary)
tox -e integration

# All default environments (lint + unit + integration + ai + ui)
tox
```

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the full tox environment reference.

## Project layout

```
proto/apme/v1/          gRPC service definitions (.proto)
src/apme/v1/            generated Python gRPC stubs
src/apme_engine/
  ├── engine/           ARI-based scanner (parse, annotate, hierarchy)
  │   └── annotators/   per-module risk annotators
  ├── validators/
  │   ├── base.py       Validator protocol + ScanContext
  │   ├── native/       Python rules (L026–L105, M005/M010, R101–R501)
  │   ├── opa/          Rego bundle (L003–L025, L061–L072, M006/M008/M009/M011, R118)
  │   ├── ansible/      Ansible-runtime rules (L057–L059, M001–M004)
  │   └── gitleaks/     Gitleaks wrapper (SEC:* — secret detection)
  ├── daemon/           gRPC server implementations
  ├── venv_manager/     Session-scoped venv lifecycle (VenvSessionManager)
  ├── remediation/      Tier 1 transforms + AI escalation
  ├── formatter.py      YAML formatter (phase 1 remediation)
  ├── cli/              CLI entry point (check, format, remediate, health-check)
  └── runner.py         scan orchestration (engine-internal pipeline)
src/apme_gateway/       API gateway (FastAPI, REST/WebSocket, SQLite)
src/galaxy_proxy/       Galaxy → PEP 503 wheel proxy
frontend/               React operator UI (Vite + TypeScript)
containers/             Containerfiles + Podman pod config
docs/                   architecture, design, rule mapping
tests/                  unit, integration, rule doc coverage
```

## Documentation

| Document | Description |
|----------|-------------|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Container topology, gRPC contracts, data flow, scaling model |
| [DATA_FLOW.md](docs/DATA_FLOW.md) | Request lifecycle, engine pipeline, serialization formats |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Podman pod setup, configuration, troubleshooting |
| [DEVELOPMENT.md](docs/DEVELOPMENT.md) | Adding rules, proto generation, testing, code organization |
| [DESIGN_VALIDATORS.md](docs/DESIGN_VALIDATORS.md) | Validator abstraction rationale and design decisions |
| [LINT_RULE_MAPPING.md](docs/LINT_RULE_MAPPING.md) | Complete rule ID cross-reference (L/M/R/P) |
| [ANSIBLELINT_COVERAGE.md](docs/ANSIBLELINT_COVERAGE.md) | Coverage vs ansible-lint, gap analysis |
| [RULE_DOC_FORMAT.md](docs/RULE_DOC_FORMAT.md) | Rule `.md` format for docs + integration tests |
| [ANSIBLE_CORE_MIGRATION.md](docs/ANSIBLE_CORE_MIGRATION.md) | ansible-core 2.19/2.20 breaking changes and rule mapping |
| [PODMAN_OPA_ISSUES.md](docs/PODMAN_OPA_ISSUES.md) | Podman rootless troubleshooting |
| [DESIGN_REMEDIATION.md](docs/DESIGN_REMEDIATION.md) | Remediation engine: transform registry, AI escalation, convergence loop |
| [DESIGN_AI_ESCALATION.md](docs/DESIGN_AI_ESCALATION.md) | AI integration: Abbenay provider, hybrid validation loop, prompt engineering |
| [RESEARCH_REVIEW.md](docs/RESEARCH_REVIEW.md) | Analysis of early research concepts and roadmap pull-ins |
| [DESIGN_DASHBOARD.md](docs/DESIGN_DASHBOARD.md) | Dashboard & presentation layer: API gateway, REST/WebSocket, persistence, auth, frontend |
| [ADRs](.sdlc/adrs/) | Architecture Decision Records — key design decisions with context, alternatives, and rationale |

## Roadmap

### Phase 1 — YAML Formatter (done)

`format` subcommand with `--diff`/`--apply`/`--check` modes, idempotency guarantees, gRPC `Format` RPC.

### Phase 2 — Modernization Engine

- `remediate` subcommand: format → idempotency gate → re-scan → semantic transforms.
- **`is_finding_resolvable()` partition**: each rule declares a `fixable` attribute; the remediate pipeline splits findings into auto-fixable vs manual/AI.
- **Multi-pass convergence loop**: remediate applies transforms and re-runs the internal scan pipeline until stable or oscillation detected (max N passes).
- **`module_metadata.json`**: machine-readable module lifecycle data (introduced, deprecated, removed, parameter renames) generated from `ansible-doc` across core versions. M-series rules become data-driven lookups instead of per-rule hardcoded logic.

### Phase 2a — New Rules

- **Secret scanning** (done) — Gitleaks validator: 800+ patterns for credentials, API keys, private keys via dedicated container + gRPC wrapper. Vault and Jinja filtering built in.
- **EE compatibility rules** (R505–R507): undeclared collections, system path assumptions, undeclared Python deps. Requires static `ee_baseline.json` from `ee-supported-rhel9` inspection.
- **Version auto-detection**: infer source Ansible version from playbook signals (short-form module names → ≤2.9, `include:` → ≤2.7, `tower_*` → ≤2.13). Auto-scope M-rules without an explicit `--ansible-core-version` flag.

### Phase 3 — AI Integration (in progress)

- **Abbenay daemon** as the AI backend via gRPC: `pip install apme-engine[ai]`.
- **AIProvider Protocol** (`ADR-024`): pluggable abstraction for LLM providers; `AbbenayProvider` is the default.
- **Hybrid validation loop**: AI proposals are re-scanned through APME validators, cleaned up with Tier 1 transforms, and retried with feedback if issues persist (max 2 attempts).
- **Interactive review** (`--ai` flag): per-fix diff review (y/n/skip) like `git add -p`, or `--auto-approve` for automatic application.
- **Structured best practices**: curated Ansible guidelines injected into prompts for higher-quality fixes.
- **Preflight checks**: auto-discover Abbenay daemon socket, health check before AI calls.
- See [DESIGN_AI_ESCALATION.md](docs/DESIGN_AI_ESCALATION.md) for the full design.

### Phase 4 — Web UI (in progress)

Operator UI for **check** and **remediate** sessions, **Activity** (history), health monitoring, and findings management. API gateway (FastAPI), REST/WebSocket API (`/api/v1/activity`, etc.), persistence (SQLite), and React frontend. Operation streaming uses `FixSession` gRPC (ADR-039). See [DESIGN_DASHBOARD.md](docs/DESIGN_DASHBOARD.md) for the full design.

## License

Apache-2.0
