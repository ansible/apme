# Architecture

**Analysis Date:** 2026-03-25

## Pattern Overview

**Overall:** Microservices with async gRPC communication and single-pod deployment.

**Key Characteristics:**
- **Client-Server model**: CLI/UI clients communicate with Primary service via gRPC
- **Parallel validation**: Primary orchestrates validators asynchronously using `asyncio.gather()`
- **Read-only validators**: All validators are detection-only; remediation is handled separately
- **Session-scoped resources**: Virtual environments per session/core-version combo managed centrally by Primary
- **Unified gRPC contract**: All validators implement same `Validator` service interface

## Layers

**CLI Layer:**
- Purpose: Pure gRPC presentation; file I/O and output rendering
- Location: `src/apme_engine/cli/`
- Contains: Command handlers (scan, fix, format, health-check), ANSI rendering, argument parsing
- Depends on: Primary service (gRPC), file system
- Used by: End users, CI/CD systems

**Primary Service (Orchestrator):**
- Purpose: Runs the ARI engine (parse → annotate → hierarchy); manages venvs; fans out to validators; merges results
- Location: `src/apme_engine/daemon/primary_server.py`, `src/apme_engine/daemon/primary_main.py`
- Contains: gRPC service implementation, session management, venv orchestration, validator coordination
- Depends on: Engine, venv manager, all validator services (via gRPC stubs)
- Used by: CLI, future UI services

**Engine (ARI Core):**
- Purpose: Parse Ansible content, build call graphs, annotate metadata, produce in-memory model
- Location: `src/apme_engine/engine/`
- Contains: Scanner, parser, context, models, analyzer, tree builder, findings aggregator
- Depends on: ansible-core, Python YAML/JSON libraries
- Used by: Primary (runs scan), validators (consume scandata)

**Validator Services:**
- Purpose: Apply domain-specific rules; each implements `Validator` gRPC service
- Locations:
  - Native: `src/apme_engine/validators/native/`, daemon at `src/apme_engine/daemon/native_validator_server.py`
  - OPA: `src/apme_engine/validators/opa/`, daemon at `src/apme_engine/daemon/opa_validator_server.py`
  - Ansible: `src/apme_engine/validators/ansible/`, daemon at `src/apme_engine/daemon/ansible_validator_server.py`
  - Gitleaks: `src/apme_engine/validators/gitleaks/`, daemon at `src/apme_engine/daemon/gitleaks_validator_server.py`
- Depends on: ValidateRequest protobuf, rule definitions, external tools (OPA binary, gitleaks binary, ansible-runtime)
- Used by: Primary orchestrator (gRPC)

**Remediation Engine:**
- Purpose: Generate fix proposals; NOT called by Primary during validation (ADR-009)
- Location: `src/apme_engine/remediation/`
- Contains: AI-driven suggestion engine, partition/segmentation logic, structured transformers
- Depends on: AI providers (Abbenay, Claude), violation models
- Used by: CLI fix command, future remediation UI flows

**Venv Manager:**
- Purpose: Create and manage session-scoped Python virtual environments with ansible-core + collections
- Location: `src/apme_engine/venv_manager/`
- Contains: Session store, venv lifecycle, incremental collection installation
- Depends on: galaxy_proxy, uv (package manager)
- Used by: Primary orchestrator, Ansible/OPA validators

**Galaxy Proxy:**
- Purpose: Convert Ansible Galaxy collection tarballs to pip-installable wheels (PEP 503)
- Location: `src/galaxy_proxy/`
- Contains: Client for Galaxy API, wheel converter, caching layer
- Depends on: Galaxy index, external wheel packages
- Used by: Venv manager

**Data Layer:**
- Purpose: Configuration and reference data
- Location: `src/apme_engine/data/`
- Contains: Ansible best practices YAML, reference data files
- Used by: Engine, validators

## Data Flow

**Scan Request Flow:**

1. CLI reads project files from disk
2. CLI chunks files into `ScanRequest` protobuf message
3. CLI calls Primary service `Scan` RPC (gRPC)
4. Primary receives `ScanRequest`, extracts options (ansible-core version, collections)
5. Primary calls `run_scan()` (engine) in executor (async-safe)
6. Engine (ARIScanner) parses all files, builds hierarchy, returns `ScanContext`
7. Primary serializes hierarchy as JSON and scandata as bytes to avoid deserialization overhead
8. Primary calls each validator concurrently via `asyncio.gather()`:
   - Native: receives `ValidateRequest` with scandata bytes
   - OPA: receives `ValidateRequest` with hierarchy_payload JSON
   - Ansible: receives `ValidateRequest` with hierarchy_payload + files, uses session venv
   - Gitleaks: receives `ValidateRequest` with files, writes to temp dir, runs binary
9. Validators return `ValidateResponse` with violations list and diagnostics
10. Primary merges violations, sorts, deduplicates, collects diagnostics
11. Primary returns `ScanResponse` with violations + `ScanDiagnostics`
12. CLI receives response, formats output (tree/table/JSON), prints violations
13. CLI exits with code 0 (no violations) or non-zero (violations found)

**Fix Request Flow:**

1. CLI sends `FixOptions` to Primary (violation subset, AI model preference)
2. Primary calls remediation engine with violations
3. Remediation engine partitions violations, generates proposals via AI
4. Primary streams proposals back to CLI as `ProposalsReady` events
5. CLI shows proposals, collects user approvals
6. CLI sends approved fixes back to Primary as `ApprovalAck`
7. Primary applies patches, returns `FixResponse` with patched files
8. CLI writes patched files back to disk

**Format Request Flow:**

1. CLI sends `FormatRequest` with playbook files
2. Primary calls formatter (in-process or delegated)
3. Returns formatted YAML bytes
4. CLI writes formatted files back to disk

**State Management:**

- **Session state**: Stored in `SessionStore` (in-memory); tracks active sessions, venvs, locks
- **Venv lifecycle**: Managed by `VenvSessionManager`; session venv path is `/sessions/{session_id}/ansible-{core_version}`
- **Request tracking**: Every request carries `request_id` for correlation across logs
- **Concurrent request handling**: Primary uses `asyncio` to handle multiple scans concurrently (up to `APME_PRIMARY_MAX_RPCS`)

## Key Abstractions

**ScanContext:**
- Purpose: Encapsulates everything a validator needs (hierarchy, scandata, files, diagnostics)
- Examples: `src/apme_engine/validators/base.py` (defines protocol)
- Pattern: Passed to all validators via `Validator.run(context)`

**Validator Protocol:**
- Purpose: Uniform interface so Primary doesn't know validator internals
- Examples: `src/apme_engine/validators/base.py` (runtime_checkable protocol)
- Pattern: Each validator backend implements `run(context: ScanContext) -> list[ViolationDict]`

**SingleScan (scandata):**
- Purpose: In-memory AST/model produced by engine; consumed by Native validator
- Examples: Produced in `src/apme_engine/engine/scanner.py`
- Pattern: Serialized via standard serialization for gRPC transmission; deserialized by Native validator

**ValidateRequest / ValidateResponse:**
- Purpose: Unified gRPC contract for all validators
- Examples: Defined in `proto/apme/v1/validate.proto`
- Pattern: Request carries all possible validator inputs; validator ignores what it doesn't need

**Rule / RuleID Conventions:**
- Purpose: Standardized rule naming per ADR-008
- Pattern: `L` (Lint), `M` (Modernize), `R` (Risk), `P` (Policy), `SEC` (Secrets)
- Examples: `L003` (OPA rule), `M001` (Ansible rule), `SEC:aws-access-key-id` (Gitleaks)

**ARIScanner:**
- Purpose: Core scan engine; entry point is `ARIScanner.evaluate()`
- Examples: `src/apme_engine/engine/scanner.py`
- Pattern: Stateful scanner that maintains parse cache, context, dependencies across scan phases

## Entry Points

**Primary Service:**
- Location: `src/apme_engine/daemon/primary_main.py`
- Triggers: Container startup; listens on `APME_PRIMARY_LISTEN` (default `0.0.0.0:50051`)
- Responsibilities: Start async gRPC server, handle `Scan`/`Format`/`Health` RPCs, coordinate validators

**Native Validator:**
- Location: `src/apme_engine/daemon/native_validator_main.py`
- Triggers: Container startup; listens on `APME_NATIVE_VALIDATOR_LISTEN` (default `0.0.0.0:50055`)
- Responsibilities: Start async gRPC server, run native Python rules on scandata

**OPA Validator:**
- Location: `src/apme_engine/daemon/opa_validator_main.py`
- Triggers: Container startup; wraps OPA REST server (8181) with gRPC interface on 50054
- Responsibilities: Accept ValidateRequest, extract hierarchy JSON, POST to OPA, convert results

**Ansible Validator:**
- Location: `src/apme_engine/daemon/ansible_validator_main.py`
- Triggers: Container startup; listens on `APME_ANSIBLE_VALIDATOR_LISTEN` (default `0.0.0.0:50053`)
- Responsibilities: Run ansible-runtime rules from session venvs on project files

**Gitleaks Validator:**
- Location: `src/apme_engine/daemon/gitleaks_validator_main.py`
- Triggers: Container startup; listens on `APME_GITLEAKS_VALIDATOR_LISTEN` (default `0.0.0.0:50056`)
- Responsibilities: Write files to temp, run gitleaks binary, convert JSON findings to violations

**CLI:**
- Location: `src/apme_engine/cli/__init__.py` (entry point is `main()`)
- Triggers: `apme-scan scan`, `apme-scan fix`, etc.
- Responsibilities: Parse CLI args, read project files, call Primary service, render output

## Error Handling

**Strategy:** Exceptions bubble up with context; gRPC errors are caught at service boundary.

**Patterns:**

- **Validator timeout**: Primary sets gRPC deadline per validator; timeout → gRPC error → marked as failed validator in diagnostics
- **Malformed input**: Parser catches YAML errors, logs, skips file or returns empty findings depending on configuration
- **Resource exhaustion**: Primary rejects new sessions if venv storage limit exceeded; returns `ResourceExhaustedError`
- **Missing venv**: Primary auto-creates venv on first session use; if creation fails, returns error to CLI
- **Validator unavailable**: Primary skips validator if gRPC connection fails; returns incomplete violations + warning in response

## Cross-Cutting Concerns

**Logging:**
- Framework: Python `logging` module with custom handlers
- Approach: Bridge handler routes ansible/ARI logs through apme logger
- Correlation: All logs include `[req={request_id}]` prefix for distributed tracing
- Implementation: `src/apme_engine/log_bridge.py`

**Validation:**
- Input: All CLI/gRPC inputs validated against protobuf schemas
- Rules: Rule IDs validated against convention per ADR-008
- Files: Symlink/path traversal checks in `safe_glob.py`

**Authentication:**
- Not implemented in APME itself
- Assumed to be handled by service mesh (e.g., K8s NetworkPolicy) or reverse proxy

**Authorization:**
- All validators run with same privilege level (non-root in container)
- Remediation engine enforces approval workflow for user confirmation

---

*Architecture analysis: 2026-03-25*
