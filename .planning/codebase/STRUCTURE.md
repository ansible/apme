# Codebase Structure

**Analysis Date:** 2026-03-25

## Directory Layout

```
apme/
├── .sdlc/                      # Spec, Design, Learning context (ADRs, workflow docs)
├── .planning/                  # Planning artifacts (codebase docs, phase plans)
├── .github/                    # GitHub Actions CI/CD
├── containers/                 # Containerfiles and build scripts for each service
├── docs/                       # User-facing documentation
├── examples/                   # Example playbooks and projects
├── frontend/                   # React/TypeScript web UI
│   ├── src/
│   │   ├── pages/             # React page components
│   │   ├── components/        # Reusable UI components
│   │   ├── hooks/             # Custom React hooks
│   │   ├── services/          # API client and formatters
│   │   ├── types/             # TypeScript type definitions
│   │   ├── data/              # Mock/reference data
│   │   └── test/              # Test utilities
│   ├── vendor/                # Vendored ansible-ui-framework components
│   └── vite.config.ts         # Vite build configuration
├── proto/                      # Protocol buffer definitions
│   └── apme/v1/
│       ├── common.proto       # Shared types (Violation, File, Health, Diagnostics)
│       ├── primary.proto      # Primary service contract
│       ├── validate.proto     # Validator service contract
│       ├── reporting.proto    # Event reporting for audit/monitoring
│       └── ansible.proto      # Ansible-specific types
├── scripts/                    # Utility scripts (build, test, proto generation)
├── src/                        # All Python backend code
│   ├── apme/                   # Generated gRPC Python stubs (from proto/)
│   │   └── v1/                # v1 service stubs
│   ├── apme_engine/            # Core APME engine and services
│   │   ├── cli/               # CLI commands (scan, fix, format, health-check)
│   │   ├── daemon/            # Daemon services (Primary, validators, main entry points)
│   │   ├── engine/            # ARI scanner engine (parser, models, rules, tree builder)
│   │   ├── validators/        # Validator implementations (native, opa, ansible, gitleaks)
│   │   ├── remediation/       # AI-driven fix generation (partition, transform, engine)
│   │   ├── venv_manager/      # Session-scoped venv lifecycle management
│   │   ├── data/              # Reference data (Ansible best practices)
│   │   ├── formatter.py       # YAML formatting logic
│   │   ├── opa_client.py      # OPA HTTP client wrapper
│   │   ├── log_bridge.py      # Python logging bridge for ARI logs
│   │   └── runner.py          # High-level scan orchestration
│   ├── apme_gateway/          # Future: REST API gateway (not currently active)
│   │   ├── api/               # REST endpoints
│   │   ├── scan/              # Scan management
│   │   ├── db/                # Database models
│   │   └── grpc_reporting/    # Event reporting
│   └── galaxy_proxy/          # PEP 503 wheel conversion proxy
│       ├── proxy/             # HTTP server and caching
│       ├── converter.py       # Galaxy tarball → wheel conversion
│       ├── galaxy_client.py   # Galaxy API client
│       └── naming.py          # Wheel naming conventions
├── tests/                      # Integration and unit tests
│   ├── integration/           # End-to-end tests
│   ├── fixtures/              # Test data (terrible-playbook example)
│   └── *_test.py              # Individual test files
├── prototypes/                # Experimental code (not production)
├── CLAUDE.md                  # Project constitution (authoritative for AI agents)
└── pyproject.toml             # Python package manifest
```

## Directory Purposes

**`.sdlc/`:**
- Purpose: Specification-driven development context
- Contains: Architecture decision records (ADRs), design documents, workflow guidelines
- Key files: `adrs/README.md`, `context/architecture.md`, `context/deployment.md`

**`.planning/`:**
- Purpose: Planning and analysis artifacts
- Contains: Codebase mapping documents, phase plans, progress tracking
- Key files: `codebase/ARCHITECTURE.md`, `codebase/STRUCTURE.md`, phase implementations

**`containers/`:**
- Purpose: Container build definitions
- Contains: Containerfiles for Primary, Native, OPA, Ansible, Gitleaks, Galaxy Proxy
- Pattern: One Containerfile per service image

**`src/apme_engine/cli/`:**
- Purpose: CLI presentation layer
- Contains: Command handlers, output formatters, argument parser
- Key files: `__init__.py` (main entry point), `parser.py` (argparse setup), `scan.py`, `fix.py`, `format_cmd.py`

**`src/apme_engine/daemon/`:**
- Purpose: gRPC service implementations and entry points
- Contains: Primary server, all validator servers, session management
- Key files:
  - `primary_main.py` / `primary_server.py` — Primary orchestrator
  - `native_validator_main.py` / `native_validator_server.py` — Native rules runner
  - `opa_validator_main.py` / `opa_validator_server.py` — OPA wrapper
  - `ansible_validator_main.py` / `ansible_validator_server.py` — Ansible runtime wrapper
  - `gitleaks_validator_main.py` / `gitleaks_validator_server.py` — Gitleaks wrapper
  - `session.py` — Session state and lifecycle
  - `event_emitter.py` — Audit event streaming

**`src/apme_engine/engine/`:**
- Purpose: Core ARI scan engine (parse → analyze → annotate → build hierarchy)
- Contains: File parsing, context building, model loading, tree construction, findings aggregation
- Key files:
  - `scanner.py` — Entry point: `ARIScanner.evaluate()`
  - `parser.py` — YAML parser and file discovery
  - `models.py` — In-memory AST classes (plays, tasks, roles, etc.)
  - `context.py` — Execution context with variable scopes
  - `tree.py` — Call graph builder
  - `analyzer.py` — Semantic analysis
  - `scanner_config.py` — Configuration object
  - Large data files: `ansible_builtin_modules.json`, `ansible_variables.txt`

**`src/apme_engine/validators/`:**
- Purpose: Domain-specific rule implementations
- Subdirectories:
  - `native/` — Python rules on in-memory model
  - `opa/` — Rego rules on hierarchy JSON (OPA binary wrapper)
  - `ansible/` — Ansible-runtime rules (module/plugin validation)
  - `gitleaks/` — Secret detection (gitleaks binary wrapper)
  - `base.py` — Validator protocol and ScanContext

**`src/apme_engine/validators/native/rules/`:**
- Purpose: Native rule implementations (L026–L060, M005/M010, P001–P004, R101–R501)
- Contains: 200+ individual rule files
- Pattern: One rule per file; file name matches rule ID (e.g., `L026_invalid_import.py`)

**`src/apme_engine/remediation/`:**
- Purpose: AI-driven fix generation
- Key files:
  - `engine.py` — Main remediation orchestrator
  - `partition.py` — Violation grouping strategy
  - `enrich.py` — Context enrichment for AI
  - `ai_provider.py` — Abstract AI backend interface
  - `abbenay_provider.py` — Abbenay AI integration
  - `structured.py` — Structured prompt templates
  - `transforms/` — YAML transformation rules

**`src/apme_engine/venv_manager/`:**
- Purpose: Session-scoped virtual environment lifecycle
- Key files:
  - `session.py` — Session tracking and venv paths
  - Manages venv creation, collection installation, caching

**`src/galaxy_proxy/`:**
- Purpose: Galaxy collection → pip wheel conversion
- Key files:
  - `cli.py` — CLI for testing wheel conversion
  - `galaxy_client.py` — Galaxy API client
  - `converter.py` — Tarball → wheel transformation
  - `naming.py` — PEP 503 wheel naming
  - `proxy/` — HTTP server and caching

**`src/apme/v1/`:**
- Purpose: Generated Python gRPC stubs
- Generated from: `proto/apme/v1/*.proto`
- Regeneration: Run `scripts/gen_grpc.sh` after any `.proto` changes

**`tests/`:**
- Purpose: Integration and unit tests
- Pattern: Test files in repo root prefixed with test name (e.g., `test_scanner.py`)
- Fixtures: `tests/fixtures/terrible-playbook/` — Example Ansible project with intentional issues

**`proto/apme/v1/`:**
- Purpose: Service contracts in Protocol Buffer format
- Key files:
  - `common.proto` — Shared types (Violation, File, HealthRequest/Response, ValidatorDiagnostics)
  - `primary.proto` — Primary service (Scan, Format, Health)
  - `validate.proto` — Validator service (Validate, Health)
  - `reporting.proto` — Event types (ScanCompleted, FixCompleted)

## Key File Locations

**Entry Points:**
- CLI: `src/apme_engine/cli/__init__.py` → `main()`
- Primary daemon: `src/apme_engine/daemon/primary_main.py` → `main()`
- Native daemon: `src/apme_engine/daemon/native_validator_main.py` → `main()`
- OPA daemon: `src/apme_engine/daemon/opa_validator_main.py` → `main()`
- Ansible daemon: `src/apme_engine/daemon/ansible_validator_main.py` → `main()`
- Gitleaks daemon: `src/apme_engine/daemon/gitleaks_validator_main.py` → `main()`

**Configuration:**
- Project manifest: `pyproject.toml` (versions, dependencies, package config)
- gRPC definitions: `proto/apme/v1/*.proto`
- Environment variables: Documented in `.sdlc/context/deployment.md`

**Core Logic:**
- Engine scan loop: `src/apme_engine/engine/scanner.py` — `ARIScanner.evaluate()`
- Primary orchestration: `src/apme_engine/daemon/primary_server.py` — `PrimaryServicer.Scan()`
- Validator protocols: `src/apme_engine/validators/base.py`
- CLI argument parsing: `src/apme_engine/cli/parser.py`

**Testing:**
- Test fixtures: `tests/fixtures/terrible-playbook/` (example Ansible project)
- Integration tests: `tests/integration/` (end-to-end validation)

## Naming Conventions

**Files:**
- Python modules: snake_case (e.g., `primary_server.py`, `native_validator.py`)
- Test files: `test_{module_name}.py` or `{module_name}_test.py`
- Rule files: Rule ID as filename (e.g., `L026_invalid_import.py`, `M001_use_fqcn.py`)
- Proto files: snake_case with service suffix (e.g., `primary.proto`, `validate.proto`)

**Directories:**
- Service directories: lowercase (e.g., `apme_engine`, `galaxy_proxy`, `apme_gateway`)
- Logical grouping: Feature-based (e.g., `validators/`, `remediation/`, `venv_manager/`)
- Daemon/service entry points: Grouped in `daemon/` directory

**Functions/Classes:**
- Entry points: `main()` for CLI and daemon entry points
- gRPC services: Class name matches proto service (e.g., `PrimaryServicer`, `ValidatorServicer`)
- Scanner: `ARIScanner` class
- Validators: Inherit from or implement `Validator` protocol

**Environment Variables:**
- Service config: `APME_{SERVICE}_{CONFIG}` (e.g., `APME_PRIMARY_LISTEN`, `APME_NATIVE_VALIDATOR_LISTEN`)
- Concurrency: `APME_{SERVICE}_MAX_RPCS` (e.g., `APME_PRIMARY_MAX_RPCS`)

## Where to Add New Code

**New Validator:**
1. Create `src/apme_engine/validators/{validator_name}/` directory
2. Implement validator class inheriting from `Validator` protocol (defined in `src/apme_engine/validators/base.py`)
3. Create daemon entry point in `src/apme_engine/daemon/{validator_name}_validator_main.py` and `{validator_name}_validator_server.py`
4. Add gRPC service wrapper if needed (similar to OPA/Gitleaks pattern)
5. Update environment variable discovery in `src/apme_engine/daemon/primary_server.py` to register new validator
6. Regenerate proto stubs if new message types needed: `scripts/gen_grpc.sh`

**New CLI Command:**
1. Create handler in `src/apme_engine/cli/{command_name}.py`
2. Add to command dispatch in `src/apme_engine/cli/__init__.py` → `main()`
3. Register in argument parser: `src/apme_engine/cli/parser.py`

**New Rule (Native Validator):**
1. Create file `src/apme_engine/validators/native/rules/{RULE_ID}_{description}.py`
2. Implement rule class/function
3. Register in `src/apme_engine/validators/native/__init__.py`
4. Add to rules discovery (if not auto-discovered)

**Shared Utilities:**
- Helper functions: `src/apme_engine/engine/utils.py` (for engine-level) or `src/apme_engine/{module}/utils.py`
- Constants: Define in module root `__init__.py` or separate `constants.py`

**Data Files:**
- Reference data: `src/apme_engine/data/`
- Rule definitions: Not stored as files; embedded in rule classes

## Special Directories

**`src/apme/v1/` (Generated):**
- Purpose: Auto-generated Python gRPC stubs from proto files
- Generated by: `scripts/gen_grpc.sh`
- Committed: Yes (for CI/CD without protoc requirement)
- Do NOT manually edit — regenerate after `.proto` changes

**`tests/fixtures/terrible-playbook/` (Test Data):**
- Purpose: Example Ansible project with known issues for testing
- Contains: Roles, playbooks, group vars demonstrating violations
- Generated: No
- Committed: Yes

**`proto/apme/v1/` (Source Definitions):**
- Purpose: Service contracts and shared types
- Format: Protocol Buffers 3
- Regen workflow: Edit `.proto` → run `scripts/gen_grpc.sh` → commit both `.proto` and generated stubs

**`frontend/vendor/` (Vendored Framework):**
- Purpose: Ansible UI framework components (vendored, not npm dependency)
- Generated: No (manually maintained fork)
- Committed: Yes

---

*Structure analysis: 2026-03-25*
