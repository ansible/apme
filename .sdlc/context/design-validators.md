# Validator Design

This document outlines a modular architecture for an Ansible content inspection system, where a centralised engine serves as the single source of truth by parsing data into a structured model. Multiple specialised validators, including OPA, Python, Ansible runtime, and Gitleaks, operate independently as discrete containers to perform structural, heuristic, functional, and security checks. These components are linked by a unified gRPC contract, allowing them to run in parallel execution via asynchronous calls to ensure total latency is determined by the slowest individual process rather than their sum. This design prioritises extensibility and consistency, ensuring that adding new validation tools requires no changes to the core logic while maintaining a standardised output format for all detected violations.

---

## Validator Abstraction and Engine Ownership

**Status**: implemented

This document captures the design rationale for the validator abstraction. All sections below describe the current implementation unless marked as "future."

---

## Pipeline

The engine ingests Ansible content and produces a structured model. Validators consume that model independently.

```
Ansible content (files)
    ↓
[Engine: load_definitions → build_content_graph → apply_rules → hierarchy]
    ↓
ScanContext { hierarchy_payload (JSON), scandata (SingleScan with .content_graph) }
    ↓
┌─────────────────────────────────────────────────────────┐
│              Parallel fan-out                            │
│                                                          │
│  ┌─► OPA             (hierarchy_payload → Rego)         │
│  ├─► Native          (content_graph → GraphRules)       │
│  ├─► Ansible         (files + hierarchy → runtime)      │
│  ├─► Gitleaks        (content_graph → stdin pipe)       │
│  ├─► Collection Hlth (venv → collection scan)           │
│  └─► Dep Audit       (venv → pip-audit subprocess)      │
│                                                          │
└─────────────────────────────────────────────────────────┘
    ↓
Merged violations (deduplicated, sorted)
```

The engine is the **single source of truth** for "what's in this repo/playbook." Validators only see what the engine produces. Adding or removing a validator does not change how content is parsed.

---

## Validator Protocol

```python
# src/apme_engine/validators/base.py

@runtime_checkable
class Validator(Protocol):
    def run(self, context: ScanContext) -> list[ViolationDict]:
        ...

@dataclass
class ScanContext:
    hierarchy_payload: YAMLDict          # always present (JSON-serializable)
    scandata: object = None              # legacy path (replaced by ContentGraph)
    root_dir: str = ""                   # filesystem path
    engine_diagnostics: EngineDiagnostics = field(default_factory=EngineDiagnostics)
```

Note: The in-process `Validator` protocol is used by `OpaValidator` and `AnsibleValidator` internally. The production gRPC path uses `ValidatorServicer` adapters that deserialize the `ValidateRequest`, create a `ScanContext`, call the validator's `run()`, and convert the result to a `ValidateResponse`. The Native validator bypasses `ScanContext` — it receives `content_graph_data` directly via gRPC and operates on `ContentGraph`.

Every validator returns the same violation shape:

```json
{
    "rule_id": "string",    // e.g. "L024", "native:L026", "M002"
    "level": "string",      // "error", "warning", "info"
    "message": "string",
    "file": "string",       // relative path
    "line": "int",          // or [start, end]
    "path": "string"        // hierarchy path
}
```

---

## Implemented Validators

### OPA (Rego)

| Aspect | Details |
|--------|---------|
| **Input** | `hierarchy_payload` (JSON) |
| **Execution** | `opa eval` subprocess with Rego bundle (`data.apme.rules.violations`). In the container the binary is local; in daemon mode it runs via Podman or local binary. |
| **Rules** | ~50 Rego files: L002–L025, L061–L072, M006–M028, R118 |
| **Container** | OPA binary + Python gRPC wrapper (`apme-opa`) |

**Why Rego**: Declarative policy language well-suited for structural checks on JSON; rules are data-driven via `bundle/data.json` (deprecated modules list, package modules, etc.)

### Native (Python)

| Aspect | Details |
|--------|---------|
| **Input** | `content_graph_data` (serialized `ContentGraph` JSON dict) |
| **Execution** | `GraphRule` subclasses with `match()` / `process()` methods, invoked by `graph_scanner.scan()` |
| **Rules** | ~90 rules: L026–L110 (lint), M005/M010/M014+ (modernize), R101–R501 (risk), A001–A002 (AAP), P001–P004 (legacy) |
| **Container** | `apme-native` |

**Why Python**: Full access to the ContentGraph model (nodes, edges, properties, variable tracking). Rules that need to walk call graphs, inspect variable resolution, or apply complex heuristics that would be awkward in Rego.

### Ansible (Runtime)

| Aspect | Details |
|--------|---------|
| **Input** | `context.root_dir` (files on disk) + `context.hierarchy_payload` |
| **Execution** | Uses ansible-core's plugin loader, `ansible-playbook --syntax-check`, argspec extraction |
| **Rules** | L057–L059 (syntax/argspec), M001–M004 (FQCN resolution, deprecation, redirects, removed modules) |
| **Container** | `apme-ansible` with UV cache pre-warmed for ansible-core 2.18/2.19/2.20; session-scoped venvs managed by the Primary orchestrator via `VenvSessionManager` |

**Why separate container**: Requires actual ansible-core installation; multi-version support needs isolated venvs; sessions volume mounted read-only

### Gitleaks (Secrets)

| Aspect | Details |
|--------|---------|
| **Input** | `content_graph_data` (node content extracted from ContentGraph) + uncovered raw files |
| **Execution** | Pipes concatenated node content to `gitleaks detect --pipe` via stdin; maps findings back to node IDs via delimiter lines |
| **Rules** | SEC:* (800+ patterns for credentials, API keys, private keys, tokens) |
| **Container** | `apme-gitleaks` (gitleaks binary + Python gRPC wrapper) |

**Why separate container**: Requires Go binary; wraps external tool output into the unified violation format. Adds Ansible-aware filtering: vault-encrypted files and Jinja2 expressions are automatically excluded.

### Collection Health (optional)

| Aspect | Details |
|--------|---------|
| **Input** | `venv_path` (session venv with installed collections) |
| **Execution** | Scans installed collections in venv with a curated subset of Native GraphRules. Findings cached by `(fqcn, version)`. |
| **Rules** | M005, M010, R101, R103–R115, R117, R401 (18 curated rules) |
| **Container** | `apme-collection-health` |

**Why separate**: Decouples collection quality from project scan; results are cached and reused across scans with same collections.

### Dep Audit (optional)

| Aspect | Details |
|--------|---------|
| **Input** | `venv_path` (session venv with Python packages) |
| **Execution** | Runs `pip-audit -f json --strict --path <site-packages>` against OSV.dev |
| **Rules** | R200 (Python CVE findings) |
| **Container** | `apme-dep-audit` |

**Why separate**: Isolates dependency auditing from scan logic; uses pip-audit binary.

---

## Engine Ownership Decision

**Chosen**: engine integrated in-tree (`src/apme_engine/engine/`).

The engine was originally derived from ARI (Ansible Risk Insights). It is now fully integrated — not vendored, not a subprocess, not a dependency. The engine code lives alongside the rest of the application and is tested, modified, and shipped as one unit.

**Rationale**:

- Full control over the hierarchy payload shape, ContentGraph structure, and parser logic
- Single parse, single model — validators reuse the same `ContentGraph` and `hierarchy_payload`
- No version drift between engine and validators
- Annotators (risk annotations) are engine concerns that feed into both OPA rules (via hierarchy JSON) and native rules (via ContentGraph)

The engine exposes one public function:

```python
# src/apme_engine/runner.py
def run_scan(target_path, project_root, include_scandata=True, dependency_dir="") -> ScanContext:
```

Everything downstream (validators, daemon, CLI) calls `run_scan()` and works with `ScanContext`.

---

## Parallel Execution

Primary calls all configured validators concurrently using `asyncio.gather()` with async gRPC stubs (`grpc.aio`). Each validator is a gRPC call to an independent container. The `ValidateRequest` is immutable and shared across all calls.

**Total latency = max(validators)** instead of sum.

Each validator is discovered by environment variable (`NATIVE_GRPC_ADDRESS`, `OPA_GRPC_ADDRESS`, `ANSIBLE_GRPC_ADDRESS`, `GITLEAKS_GRPC_ADDRESS`, `COLLECTION_HEALTH_GRPC_ADDRESS`, `DEP_AUDIT_GRPC_ADDRESS`). If a variable is unset, that validator is skipped — no error, just fewer results. This makes it possible to run a subset of validators during development or testing.

---

## Unified gRPC Contract

All validators implement the same `Validator` service from `validate.proto`:

```protobuf
service Validator {
  rpc Validate(ValidateRequest) returns (ValidateResponse);
  rpc Health(HealthRequest) returns (HealthResponse);
}
```

The `ValidateRequest` is a superset — it carries fields for all validators. Each validator consumes only what it needs and ignores the rest. This means adding a new validator requires:

1. Implement `ValidatorServicer` (one `Validate` method)
2. Build a container image
3. Add an environment variable to Primary
4. Add the service to the pod spec

**No proto changes, no Primary code changes, no other validators affected.**

---

## Rule ID Independence

Rule IDs (L, M, R, P) describe **what** is checked, not **who** checks it. The user sees `L002` (FQCN check); whether OPA or a Python rule implements it is irrelevant. Multiple validators can fire for the same concept (e.g., OPA L002 is syntactic FQCN; Ansible M001 is semantic FQCN resolution) — they have different rule IDs because they're different checks.

Deduplication happens at the Primary level by `(rule_id, file, line)`. If two validators produce the same rule/file/line, only one is reported.

---

## Diagnostics Contract

Every validator returns structured timing data in `ValidateResponse.diagnostics`:

```python
ValidatorDiagnostics(
    validator_name="native",       # identifies the validator
    request_id="scan-uuid",        # echoed for correlation
    total_ms=42.0,                 # wall-clock time
    files_received=10,             # input file count
    violations_found=5,            # output violation count
    rule_timings=[                 # per-rule granularity
        RuleTiming(rule_id="L026", elapsed_ms=3.5, violations=2),
    ],
    metadata={"key": "value"},     # validator-specific data
)
```

Primary aggregates all `ValidatorDiagnostics` plus engine phase timing into `ScanDiagnostics` on the `SessionEvent` stream. `apme check` (and related clients) show diagnostics with `-v` (summary + top 10 slowest rules) or `-vv` (full per-rule breakdown).

---

## Future Considerations

| Area | Description |
|------|-------------|
| **Additional validators** | A yamllint adapter, a custom Go plugin validator, or an AI-assisted reviewer could be added as new containers implementing the same `Validator` service. |
| **Streaming results** | The current contract is unary (one request, one response). For very large projects, server-side streaming (`stream ValidateResponse`) could reduce memory pressure. |
| **Validator-specific configuration** | Rules can be enabled/disabled per-validator via configuration (not yet implemented at the gRPC level — currently done at the rule level within each validator). |

---

## Related Documents

- [ADR-001: gRPC Communication](/.sdlc/adrs/ADR-001-grpc-communication.md) — Why gRPC for inter-service communication
- [ADR-002: OPA/Rego Policy](/.sdlc/adrs/ADR-002-opa-rego-policy.md) — Hybrid Rego + Python rules
- [ADR-003: Vendored ARI Engine](/.sdlc/adrs/ADR-003-vendor-ari-engine.md) — Engine ownership decision
- [ADR-007: Async gRPC Servers](/.sdlc/adrs/ADR-007-async-grpc-servers.md) — Why grpc.aio
- [architecture.md](architecture.md) — Container topology and service contracts
- [lint-rule-mapping.md](lint-rule-mapping.md) — Rule ID cross-mapping
