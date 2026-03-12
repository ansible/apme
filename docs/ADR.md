# Architecture Decision Record

This document captures the key architectural decisions made during the design and implementation of APME (Ansible Policy & Modernization Engine). Each entry records the context, options considered, decision, and rationale.

---

## ADR-001: gRPC for inter-service communication

**Status:** Accepted  
**Date:** 2026-02  

### Context

APME is a multi-service system. The Primary orchestrator fans out to multiple validator backends, each in its own container. We needed a protocol for this inter-service communication.

### Options considered

| Option | Pros | Cons |
|--------|------|------|
| gRPC (protobuf) | Binary encoding, streaming, bidirectional, typed contracts, code generation | Requires proto compilation, less browser-friendly |
| REST/JSON | Universal, browser-friendly, no compilation | Verbose, no streaming, no type safety, manual validation |
| JSON-RPC | Lightweight, language-agnostic | No streaming, no code generation, limited tooling |

### Decision

Use **gRPC** for all inter-service communication (Primary ↔ Validators, CLI ↔ Primary). HTTP/REST is used only for the OPA REST API internally (OPA's native interface) and reserved for future presentation layers (web UI).

### Rationale

- Typed `.proto` contracts generate client and server stubs — adding a validator means implementing one RPC
- Binary encoding is efficient for the hierarchy payload (large nested JSON structures)
- Bidirectional streaming is available if needed for future large-project scanning
- The penalty (proto compilation step) is minor and automated via `scripts/gen_grpc.sh`

> *"I find grpc fast and bidirectional when needed"* — user decision

---

## ADR-002: OPA/Rego for declarative policy rules

**Status:** Accepted  
**Date:** 2026-02  

### Context

Ansible-lint implements ~100 rules in Python. We needed a strategy for rule implementation that balances expressiveness, maintainability, and extensibility.

### Options considered

| Option | Pros | Cons |
|--------|------|------|
| All rules in Python | Full engine access, familiar language | Monolithic, hard to contribute declaratively |
| All rules in Rego | Declarative, portable, data-driven | Cannot access full engine model, limited for complex checks |
| Hybrid: Rego + Python | Best of both worlds | Two rule languages to maintain |

### Decision

**Hybrid approach.** Rules that operate on the JSON hierarchy payload (structural checks, naming conventions, best-practice patterns) are written in Rego and evaluated by OPA. Rules that require the full in-memory engine model (variable resolution, call-graph traversal, risk annotations) are written in Python as native rules.

### Rationale

- Rego is purpose-built for structural policy checks on JSON — concise and auditable
- OPA's `data.json` mechanism makes rules data-driven (e.g., deprecated module lists, package module sets)
- Rules needing `scandata` (the full `SingleScan` object with trees, contexts, variable tracking) cannot be expressed in Rego — they stay native
- Each OPA rule lives in its own `.rego` file with a colocated `_test.rego`, matching the native rule pattern

---

## ADR-003: Vendor the ARI engine, do not use as dependency

**Status:** Accepted  
**Date:** 2026-02  

### Context

The engine that parses Ansible content (playbooks, roles, collections, task files) and builds the in-memory model originates from ARI (Ansible Risk Insights). We needed to decide how to consume it.

### Options considered

| Option | Pros | Cons |
|--------|------|------|
| pip dependency | Clean separation, upstream updates | No control over parser behavior, version drift, API instability |
| Vendored (copied in, read-only) | Full control, single repo | Must maintain fork |
| Subprocess | No code coupling | Double parsing, IPC overhead, fragile |
| Full integration | Native, tested as one unit | Maintenance burden |

### Decision

**Full integration.** The ARI engine code was brought into `src/apme_engine/engine/` and is maintained as part of the project. It is not vendored read-only, not a pip dependency, and not invoked via subprocess.

### Rationale

- The engine is the critical data path — its output (hierarchy payload, scandata) feeds every validator
- We need to modify the parser, annotators, and hierarchy builder to suit our payload shape
- Single parse, single model: all validators reuse the same engine output with no version drift
- The original ARI rules become one validator ("native") among many, not the engine itself

> *"We need to vendor the code, bring it into this project."* — user decision

---

## ADR-004: Podman pod as deployment unit

**Status:** Accepted  
**Date:** 2026-02  

### Context

APME runs multiple services (Primary, Native, OPA, Ansible, Gitleaks, Cache Maintainer, CLI). We needed a deployment model.

### Options considered

| Option | Pros | Cons |
|--------|------|------|
| Docker Compose | Widely adopted, good local dev | Requires Docker daemon, not rootless-friendly |
| Podman pod | Rootless, Kubernetes-compatible YAML, no daemon | Less tooling maturity |
| Kubernetes directly | Production-grade orchestration | Heavy for local dev |
| Single monolithic process | Simple deployment | No isolation, no independent scaling |

### Decision

Use a **Podman pod**. All backend services share `localhost` within the pod. The CLI runs on-the-fly outside the pod with a CWD volume mount.

### Rationale

- Podman is rootless and daemon-less — better security posture for developer workstations
- The pod spec is Kubernetes-compatible YAML, easing future migration
- Shared `localhost` within the pod means fixed port assignments, no service discovery
- The CLI is ephemeral (run and exit), not a long-running service

> *"Use podman and a pod not docker. The CLI container should be 'on the fly' since it will need the CWD volume mount."* — user decision

---

## ADR-005: Reject etcd/service discovery for single-pod deployment

**Status:** Accepted  
**Date:** 2026-02  

### Context

An "Introspective Pod" design was proposed with etcd for service discovery, registration heartbeats, and client-side load balancing within the pod.

### Options considered

| Option | Pros | Cons |
|--------|------|------|
| etcd sidecar | Dynamic discovery, load metrics | Heavy (Raft consensus), unnecessary for fixed service set |
| Fixed-port env vars | Zero dependencies, simple, deterministic | Must update pod spec to add services |

### Decision

**Fixed-port environment variables.** Each validator has a known port (50051–50056) and is discovered via env vars (`NATIVE_GRPC_ADDRESS`, `OPA_GRPC_ADDRESS`, etc.). No etcd, no registration, no heartbeats.

### Rationale

- Within a single pod, the service set is known at deploy time from the pod YAML
- etcd adds operational complexity (Raft cluster, persistence, health monitoring) for a problem that doesn't exist — there's no dynamic service topology
- If a validator is not configured (env var unset), Primary simply skips it — graceful degradation with zero infrastructure

---

## ADR-006: Ephemeral per-request venvs for Ansible validator

**Status:** Accepted  
**Date:** 2026-03  

### Context

The Ansible validator requires an `ansible-core` installation to run syntax checks, argspec validation, and module introspection. Multiple ansible-core versions (2.18, 2.19, 2.20) must be supported concurrently.

### Options considered

| Option | Pros | Cons |
|--------|------|------|
| Pre-built venv pool with semaphores | Amortized build cost | Shared state, stale venvs, locking complexity |
| Ephemeral per-request venvs | Perfect isolation, automatic cleanup | ~1-2s creation cost per request |
| Pre-built at container build time (static) | Zero runtime cost | Cannot support dynamic version selection |

### Decision

**Ephemeral per-request venvs.** Each `Validate()` call creates a temporary venv using UV (from warm cache), runs all rules, then destroys the venv in a `finally` block.

### Rationale

- UV's persistent wheel cache makes venv creation fast (~1-2 seconds from warm cache)
- Zero shared state between concurrent requests — no locking, no stale venvs
- Automatic cleanup: the venv is destroyed after each request regardless of success or failure
- The Ansible Dockerfile pre-warms the UV cache at build time (`prebuild-venvs.sh`)
- Concurrency is bounded by `maximum_concurrent_rpcs=8` on the Ansible gRPC server

> *"I think we should build a venv per request and dispose of it when done per session per core version."* — user decision

---

## ADR-007: Fully async gRPC servers (grpc.aio)

**Status:** Accepted  
**Date:** 2026-03  

### Context

The original gRPC servers used synchronous `grpc.server()` with `ThreadPoolExecutor`. Under concurrent load, thread exhaustion and blocking I/O in validators (subprocess calls, HTTP requests to OPA) were identified as bottlenecks.

### Options considered

| Option | Pros | Cons |
|--------|------|------|
| Synchronous gRPC + ThreadPoolExecutor | Simple, familiar | Thread exhaustion under load, blocking I/O wastes threads |
| grpc.aio (fully async) | Non-blocking I/O, `asyncio.gather()` for fan-out | Requires async-aware libraries |

### Decision

**grpc.aio for all five gRPC servers.** CPU-bound work runs via `run_in_executor()`. I/O-bound work uses native async libraries (`httpx.AsyncClient` for OPA, `asyncio.create_subprocess_exec` for Gitleaks).

### Rationale

- The implementation pattern is consistent across validators — the async overhead is minimal
- Primary benefits most: `asyncio.gather()` for parallel validator fan-out replaces `ThreadPoolExecutor.map()`
- OPA validator: `requests.post()` → `httpx.AsyncClient.post()` — truly non-blocking HTTP
- Each server sets `maximum_concurrent_rpcs` for backpressure control
- `request_id` propagation is naturally supported through async call chains

> *"How much more complex? Our implementation should be pretty simple and similar across validators."* — user decision

---

## ADR-008: Rule ID conventions (L/M/R/P)

**Status:** Accepted  
**Date:** 2026-02  

### Context

Rules needed stable, human-readable IDs. The original ansible-lint used kebab-case names (`no-changed-when`, `fqcn`). We needed a convention for our multi-validator system.

### Options considered

| Option | Pros | Cons |
|--------|------|------|
| kebab-case (ansible-lint style) | Descriptive | Verbose, not sortable, no category prefix |
| `Lxxx` / `Rxxx` / `Mxxx` / `Pxxx` | Sortable, categorized, concise | Less self-documenting |

### Decision

Use **prefixed numeric IDs**:

| Prefix | Category | Examples |
|--------|----------|----------|
| **L** | Lint (style, correctness, best practice) | L002–L059 |
| **M** | Modernize (ansible-core migration) | M001–M004 |
| **R** | Risk/security (annotation-based) | R101–R501, R118 |
| **P** | Policy (legacy, requires ansible runtime) | P001–P004 |

### Rationale

- Rule IDs are independent of the validator that implements them — the user sees `L002`, not "the OPA rule that checks FQCN"
- Numeric IDs are sortable and stable across refactors
- The prefix immediately communicates the rule's category
- A cross-mapping document (`LINT_RULE_MAPPING.md`) tracks the correspondence to original ansible-lint rule names

> *"I think lint rules should have an Lxxx ID."* — user decision

---

## ADR-009: Separate remediation engine with transform registry

**Status:** Accepted  
**Date:** 2026-03  

### Context

We needed a strategy for automatically fixing detected violations. Should fixes be embedded in rules, or should there be a separate engine?

### Options considered

| Option | Pros | Cons |
|--------|------|------|
| Rules fix violations inline | Self-contained | Rules become read-write, mixing concerns |
| Separate remediation engine | Clean separation (detect vs fix) | Additional component to maintain |
| AI-only fixes | Handles everything | Unreliable, expensive, slow |

### Decision

**Separate remediation engine** with a three-tier finding classification:

1. **Tier 1 — Deterministic transforms**: registered functions in a `TransformRegistry`, keyed by rule ID. Always correct, fully automated.
2. **Tier 2 — AI-proposable**: findings where an LLM can suggest a fix with high confidence. Human reviews before applying.
3. **Tier 3 — Manual review**: findings that require human judgment (architectural concerns, security risk acceptance).

### Rationale

- Validators are read-only by design — they detect but never modify files
- The remediation engine operates on the YAML AST (ruamel.yaml round-trip) and produces diffs
- A convergence loop (scan → fix → rescan → repeat until stable) ensures correctness
- The formatter is a blind pre-pass, not part of the remediation engine
- Separating remediation into its own service enables future AI escalation without touching validators

> *"I think B is best since as a separate service, it could invoke AI if needed right?"* — user decision

---

## ADR-010: Gitleaks as a gRPC validator

**Status:** Accepted  
**Date:** 2026-03  

### Context

Secret detection was needed. Several tools were evaluated: Gitleaks, detect-secrets, GitHub Secret Scanning, secrets-patterns-db.

### Options considered

| Option | Pros | Cons |
|--------|------|------|
| Native Python regex rules (R502–R504) | In-process, no binary dependency | Limited patterns, maintenance burden |
| Gitleaks | Single Go binary, 800+ patterns, JSON output, maintained | External binary, container needed |
| detect-secrets | Python-native, entropy detectors | Slower, less pattern coverage |

### Decision

Use **Gitleaks** as a dedicated validator in its own container, exposed via gRPC. The previously planned native SEC rules (R502–R504) were superseded.

### Rationale

- 800+ maintained patterns covering AWS keys, private keys, passwords, tokens, and provider-specific formats
- `--no-git` mode scans raw files — compatible with the chunked filesystem pattern
- JSON report output is easy to parse and convert to `Violation` messages
- Ansible-aware filtering (vault-encrypted files, Jinja2 expressions) is added in the gRPC wrapper
- The `ValidateRequest` already carries raw file content, so Gitleaks receives exactly what it needs

> *"Yes, gitleaks in a container as a validator via grpc."* — user decision

---

## ADR-011: YAML formatter as Phase 1 pre-pass

**Status:** Accepted  
**Date:** 2026-03  

### Context

Remediation (semantic fixes) requires clean YAML as input. If formatting and semantic fixes are interleaved, diffs become noisy and convergence is harder to guarantee.

### Options considered

| Option | Pros | Cons |
|--------|------|------|
| Format + fix in one pass | Single pipeline | Noisy diffs, hard to verify formatter stability |
| Format as Phase 1, then fix | Clean separation, idempotency gate | Two passes over files |
| Route formatter through remediation engine | Unified pipeline | Requires artificial "formatting violations", scan-before-format |

### Decision

**YAML formatter runs as Phase 1**, completely independent of the scan and remediation engine. The pipeline is:

```
format → idempotency gate (re-format, verify zero diffs) → scan → remediate → rescan → converge
```

### Rationale

- The formatter (ruamel.yaml round-trip) normalizes indentation, key ordering, Jinja spacing, and tab removal
- Idempotency is verified by running the formatter twice — if the second pass produces any diffs, the formatter has a bug and the pipeline aborts
- Subsequent semantic diffs are clean because formatting noise has been eliminated
- The formatter does not consume violations — it operates on raw YAML

---

## ADR-012: Scale pods, not services within a pod

**Status:** Accepted  
**Date:** 2026-02  

### Context

When throughput needs to increase, where do we scale?

### Options considered

| Option | Pros | Cons |
|--------|------|------|
| Scale services within a pod (multiple Ansible validators + load balancer) | Fine-grained scaling | Requires service discovery (etcd), complex routing |
| Scale pods horizontally | Simple, self-contained | Each pod has a full copy of every service |

### Decision

**Scale pods, not services within a pod.** Each pod is a self-contained stack (Primary + Native + OPA + Ansible + Gitleaks + Cache Maintainer). To increase throughput, run more pods behind a load balancer.

### Rationale

- The pod is the natural unit for Kubernetes/Podman scaling
- No intra-pod service discovery or routing needed
- Each request is handled entirely within one pod — no cross-pod RPC
- The Cache Maintainer is the one exception that could be extracted to a shared service if multiple pods need a single cache volume

---

## ADR-013: Structured diagnostics in the gRPC contract

**Status:** Accepted  
**Date:** 2026-03  

### Context

During development and production operation, understanding where time is spent during a scan is critical. Ad-hoc stderr logging was insufficient for programmatic consumption.

### Options considered

| Option | Pros | Cons |
|--------|------|------|
| Ad-hoc stderr logging | Simple, immediate | Not programmatic, not visible to CLI or UI |
| Structured diagnostics in proto | Machine-readable, flows through gRPC, CLI and UI can display | Proto changes, always collected |
| Optional sidecar (Prometheus, OpenTelemetry) | Standard observability | Infrastructure overhead, not embedded in response |

### Decision

Add **structured diagnostics messages** to the proto contract:

- `RuleTiming` — per-rule elapsed time and violation count
- `ValidatorDiagnostics` — per-validator summary (total time, file count, violation count, rule timings, metadata)
- `ScanDiagnostics` — engine phases + all validator diagnostics aggregated

Diagnostics are **always collected** by every validator and the engine. Display in the CLI is gated by verbosity:

| Flag | Display |
|------|---------|
| (none) | Violations only |
| `-v` | Validator summaries + top 10 slowest rules |
| `-vv` | Full per-rule breakdown, metadata, engine phase timing |

### Rationale

- Diagnostics data is carried in the gRPC response — no log parsing needed
- Future UIs, CI integrations, and API consumers can access timing data directly from `ScanResponse.diagnostics`
- Always-collect/tiered-display means zero overhead for users who don't need it, full detail for those who do
- Per-rule granularity enables identifying slow rules, regression detection, and performance optimization

> *"Always collect, but we can show the user the top ten with -v and maybe all with -vv."* — user decision

---

## ADR-014: Ruff linter and prek pre-commit hooks

**Status:** Accepted  
**Date:** 2026-03  

### Context

The project had no linter, code formatter, or pre-commit hooks. Code style inconsistencies and latent issues (unused imports, bare raises, missing context managers, ambiguous variable names) accumulated across the codebase. There was no automated gate to prevent these from entering the repository.

### Options considered

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

### Decision

Use **ruff** for linting and formatting, managed via **prek** pre-commit hooks. Configuration lives in `pyproject.toml` under `[tool.ruff]`. The `.pre-commit-config.yaml` uses `astral-sh/ruff-pre-commit` with `ruff` (lint + auto-fix) and `ruff-format` hooks.

### Rationale

- ruff is a single tool that replaces flake8, isort, pyupgrade, and pycodestyle — one config, one dependency
- ruff runs in milliseconds on the full codebase, making it practical as a pre-commit hook
- prek is a faster, dependency-free drop-in for pre-commit — no Python runtime required to run hooks
- `.pre-commit-config.yaml` is the standard format understood by both prek and pre-commit
- All existing violations were remediated at adoption time, so the codebase starts clean

---

## ADR-015: GitHub Actions CI with prek

**Status:** Accepted  
**Date:** 2026-03  

### Context

ADR-014 introduced ruff and prek for local pre-commit hooks, but there was no CI enforcement. Contributors who skip `prek install` or commit with `--no-verify` could merge code that fails lint or format checks.

### Options considered

| Option | Pros | Cons |
|--------|------|------|
| j178/prek-action | Official prek action, installs prek + runs hooks in one step, reuses `.pre-commit-config.yaml` | Newer action |
| astral-sh/ruff-action | Official ruff action, well-maintained | Doesn't reuse `.pre-commit-config.yaml`, would duplicate hook config |
| Manual `pip install ruff && ruff check` | No third-party action dependency | Manual setup, duplicates config, no hook caching |

### Decision

Use **j178/prek-action** in a GitHub Actions workflow triggered on `pull_request` against `main`. The action installs prek and runs `prek run --all-files`, reusing the existing `.pre-commit-config.yaml` — no duplicate configuration.

### Rationale

- Single source of truth: CI and local dev both use the same `.pre-commit-config.yaml`
- The action handles prek installation and caching automatically
- PRs that fail ruff lint or format checks are blocked from merging
- Minimal workflow config: checkout + prek-action (two steps)

---

## ADR-016: Single-branch `main` strategy

**Status:** Accepted  
**Date:** 2026-03  

### Context

The repository used a `master` default branch. The industry has broadly adopted `main` as the default branch name. Additionally, we needed to decide whether to introduce long-lived feature branches, release branches, or other branching strategies.

### Options considered

| Option | Pros | Cons |
|--------|------|------|
| Single branch (`main`) | Simple, all work merges to one place, easy to reason about | No release isolation |
| Gitflow (`main` + `develop` + release branches) | Release isolation, hotfix support | Complex, overhead for a project without versioned releases yet |
| Trunk-based with short-lived branches | Industry best practice for CI/CD | Requires mature CI — we're getting there |

### Decision

Rename the default branch from `master` to `main`. Use a **single-branch strategy** with `main` as the sole long-lived branch. All work is done on short-lived feature branches forked from `main` and merged back via pull request. No `develop`, `release/*`, or `staging` branches.

Multi-branch strategies (release branches, maintenance branches) will be introduced only when a concrete need arises that cannot be addressed with tags or the single-branch model.

### Rationale

- The project does not yet have versioned releases — there is nothing to isolate
- A single branch eliminates merge conflicts between long-lived branches and reduces cognitive overhead
- Short-lived feature branches + PR review + CI (prek) provide sufficient quality gating
- Tags can mark release points when versioned releases begin
- This decision is explicitly revisable: when multi-branch is required, we adopt it then

---

## Changelog

| ADR | Date | Summary |
|-----|------|---------|
| 001 | 2026-02 | gRPC for inter-service communication |
| 002 | 2026-02 | OPA/Rego for declarative policy rules |
| 003 | 2026-02 | Vendor ARI engine, full integration |
| 004 | 2026-02 | Podman pod as deployment unit |
| 005 | 2026-02 | Reject etcd/service discovery |
| 006 | 2026-03 | Ephemeral per-request venvs |
| 007 | 2026-03 | Fully async gRPC servers (grpc.aio) |
| 008 | 2026-02 | Rule ID conventions (L/M/R/P) |
| 009 | 2026-03 | Separate remediation engine |
| 010 | 2026-03 | Gitleaks as gRPC validator |
| 011 | 2026-03 | YAML formatter as Phase 1 pre-pass |
| 012 | 2026-02 | Scale pods, not services |
| 013 | 2026-03 | Structured diagnostics in gRPC contract |
| 014 | 2026-03 | Ruff linter and prek pre-commit hooks |
| 015 | 2026-03 | GitHub Actions CI with prek |
| 016 | 2026-03 | Single-branch `main` strategy |
