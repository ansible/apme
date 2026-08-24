# Validator abstraction and engine ownership

## Status: implemented

This document captures the design rationale for the validator abstraction. All sections below describe the current implementation unless marked as "future."

---

## Pipeline

The engine ingests Ansible content and produces a structured model. Validators consume that model independently.

```
Ansible content (files)
    ↓
[Engine: parse → trees → variables → annotate → hierarchy]
    ↓
ScanContext { hierarchy_payload (JSON), scandata (SingleScan) }
    ↓
┌─────────────────────────────────────────────────┐
│              Parallel fan-out                    │
│                                                  │
│  ┌─► OPA              (hierarchy_payload → Rego)       │
│  ├─► Native           (scandata → Python rules)        │
│  ├─► Ansible          (files + hierarchy → runtime)    │
│  ├─► Gitleaks         (files → secrets detection)      │
│  ├─► Collection Health (venv → collection lint)        │
│  └─► Dep Audit        (venv → pip-audit CVEs)          │
│                                                  │
└─────────────────────────────────────────────────┘
    ↓
Merged violations (deduplicated, sorted)
```

The engine is the **single source of truth** for "what's in this repo/playbook." Validators only see what the engine produces. Adding or removing a validator does not change how content is parsed.

---

## Validator protocol

```python
# src/apme_engine/validators/base.py

class Validator(Protocol):
    def run(self, context: ScanContext) -> list[dict[str, Any]]:
        ...

class ScanContext:
    hierarchy_payload: dict      # always present (JSON-serializable)
    scandata: Any = None         # full SingleScan (for native validator)
    root_dir: str = ""           # filesystem path (for ansible validator)
```

Every validator returns the same violation shape:

```python
{
    "rule_id": str,    # e.g. "L024", "native:L026", "M002"
    "level": str,      # "error", "warning", "info"
    "message": str,
    "file": str,       # relative path
    "line": int,       # or [start, end]
    "path": str,       # hierarchy path
    # Optional rule-specific metadata (forwarded via proto metadata map):
    # "resolved_fqcn", "original_module", "with_key", "redirect_chain", "removal_msg"
}
```

---

## Implemented validators

### OPA (Rego)

- **Input**: `context.hierarchy_payload` (JSON)
- **Execution**: Rego bundle evaluated by OPA (`data.apme.rules.violations`)
- **Rules**: L003–L025, M006/M008/M009/M011, R118
- **Container**: OPA binary + Python gRPC wrapper (`apme-opa`)
- **Why Rego**: Declarative policy language well-suited for structural checks on JSON; rules are data-driven via `bundle/data.json` (deprecated modules list, package modules, etc.)

### Native (Python)

- **Input**: `ContentGraph` (deserialized from `scandata`)
- **Execution**: `GraphRule` subclasses with `match()` / `process()` methods, evaluated by `graph_scanner.scan()`
- **Rules**: L026–L105 (lint), M005–M030 (modernize), R101–R501 (risk)
- **Container**: `apme-native`
- **Why Python**: Full access to the ContentGraph DAG — node attributes, edges, and structural queries. Rules that need to walk the hierarchy, inspect module options, or apply complex heuristics that would be awkward in Rego.

### Ansible (runtime)

- **Input**: `context.root_dir` (files on disk) + `context.hierarchy_payload`
- **Execution**: Uses ansible-core's plugin loader, `ansible-playbook --syntax-check`, argspec extraction
- **Rules**: L057–L059 (syntax/argspec), M001–M004 (FQCN resolution, deprecation, redirects, removed modules)
- **Container**: `apme-ansible` with UV cache pre-warmed for ansible-core 2.18/2.19/2.20; session-scoped venvs managed by the Engine orchestrator via `VenvSessionManager`
- **Why separate container**: Requires actual ansible-core installation; multi-version support needs isolated venvs; sessions volume mounted read-only

### Gitleaks (secrets)

- **Input**: `request.files` (raw file content)
- **Execution**: Writes files to temp dir, runs `gitleaks detect --no-git --report-format json`, parses JSON report
- **Rules**: SEC:* (800+ patterns for credentials, API keys, private keys, tokens)
- **Container**: `apme-gitleaks` (gitleaks binary + Python gRPC wrapper)
- **Why separate container**: Requires Go binary; wraps external tool output into the unified violation format. Adds Ansible-aware filtering: vault-encrypted files and Jinja2 expressions are automatically excluded.

### Collection Health (installed collection scanning)

- **Input**: `venv_path` (session-scoped virtualenv with installed collections)
- **Execution**: Runs the APME engine against each collection under `site-packages/ansible_collections/`, producing findings attributed to the dependency rather than the project
- **Rules**: Lint and modernization findings from collections — helps assess dependency health
- **Container**: `apme-collection-health` (mounts `/sessions` read-only)
- **Why separate container**: Collection scanning is heavyweight and optional; isolating it prevents slowdowns in the core scan pipeline. Skippable via `--skip-dep-scan` or `--skip-collection-scan`.

### Dep Audit (Python CVE scanning)

- **Input**: `venv_path` (session-scoped virtualenv with all installed Python packages)
- **Execution**: Runs `pip-audit` against the venv to detect known CVEs in installed Python packages (including transitive dependencies from Ansible collections)
- **Rules**: One violation per CVE with package name, installed version, fixed version, and advisory ID
- **Container**: `apme-dep-audit` (mounts `/sessions` read-only)
- **Why separate container**: CVE scanning is network-dependent and optional; isolation keeps the core pipeline fast. Skippable via `--skip-dep-scan` or `--skip-python-audit`.

---

## Engine ownership decision

**Chosen: engine integrated in-tree** (`src/apme_engine/engine/`).

The engine was originally derived from ARI (Ansible Risk Insights). It is now fully integrated — not vendored, not a subprocess, not a dependency. The engine code lives alongside the rest of the application and is tested, modified, and shipped as one unit.

Rationale:

- Full control over the hierarchy payload shape, ContentGraph structure, and parser logic
- Single parse, single model — validators reuse the same `ContentGraph` and `hierarchy_payload`
- No version drift between engine and validators
- ContentGraph is the sole data model feeding both OPA rules (via hierarchy JSON) and native rules (via GraphRule evaluation)

The engine exposes one public function:

```python
# src/apme_engine/runner.py
def run_scan(target_path, project_root, include_scandata=True) -> ScanContext:
```

Everything downstream (validators, daemon, CLI) calls `run_scan()` and works with `ScanContext`.

---

## Parallel execution

Engine calls all configured validators concurrently using `asyncio.gather()` with async gRPC stubs (`grpc.aio`). Each validator is a gRPC call to an independent container. The `ValidateRequest` is immutable and shared across all calls.

Total latency = `max(all validators)` instead of `sum`.

Core services — Engine, Native, OPA, Ansible, and Galaxy Proxy — are always
required. Native, OPA, and Ansible are discovered by environment variable
(`NATIVE_GRPC_ADDRESS`, `OPA_GRPC_ADDRESS`, `ANSIBLE_GRPC_ADDRESS`); Galaxy Proxy
via `APME_GALAXY_PROXY_URL`. Missing core addresses fail Engine readiness rather
than silently reducing results. Optional validators (`GITLEAKS_GRPC_ADDRESS`,
`COLLECTION_HEALTH_GRPC_ADDRESS`, `DEP_AUDIT_GRPC_ADDRESS`) are skipped when
unset and start only with `include_optional=True` (or in the full pod).

---

## Unified gRPC contract

All validators implement the same `Validator` service from `validate.proto`:

```protobuf
service Validator {
  rpc Validate(ValidateRequest) returns (ValidateResponse);
  rpc Health(HealthRequest) returns (HealthResponse);
}
```

The `ValidateRequest` is a superset — it carries fields for all validators. Each validator consumes only what it needs and ignores the rest. This means adding a new validator requires:

1. Implement `ValidatorServicer` (`Validate` and `Health`)
2. Build a container image
3. Add its address environment variable and wire it through `_DEFAULT_PORTS` or `_OPTIONAL_SERVICES`, `env_map`, and `_run_daemon()`
4. Add the service to the pod spec

No proto changes or changes to other validators are required.

---

## Rule ID independence

Rule IDs (L, M, R, P) describe **what** is checked, not **who** checks it. The user sees a rule ID; whether OPA or a Python rule implements it is irrelevant. Multiple validators can fire for the same concept (e.g., OPA L003 is a structural lint check; Ansible M001 is semantic FQCN resolution) — they have different rule IDs because they're different checks.

Deduplication happens at the Engine level by `(rule_id, file, line)`. If two validators produce the same rule/file/line, only one is reported.

---

## Diagnostics contract

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

Engine aggregates all `ValidatorDiagnostics` plus engine phase timing into `ScanDiagnostics` on the `FixCompletedEvent` reporting path (`reporting.proto`). `SessionEvent` / `SessionResult` do not carry a diagnostics field. The CLI surfaces pipeline timing via `-v` / `-vv` from session `ProgressUpdate` logs (summary vs full per-rule breakdown).

---

## Rule catalog and overrides (ADR-041)

Rule enable/disable, severity overrides, and enforced-mode are managed at the **Engine/Gateway level**, not inside individual validators. Validators remain read-only — they always run all their rules and return all violations. Policy enforcement is orchestration:

1. **Catalog registration**: At startup, the Engine collects `RuleDefinition` metadata from all built-in validators and pushes the full catalog to the Gateway via `RegisterRules`. The Gateway reconciles a `rules` table and stores admin-configured overrides in `rule_overrides`.

2. **Override delivery**: Each scan carries `repeated RuleConfig rule_configs` in `ScanOptions`. For Gateway-initiated scans, the Gateway loads resolved configs (default + override) and injects them. For CLI scans, configs are parsed from `.apme/rules.yml`.

3. **Engine enforcement**: After validator fan-out, the Engine applies `rule_configs` — filtering violations for disabled rules, overriding severity labels, and attaching `enforced` metadata (bypassing `# apme:ignore`). When `rule_configs_complete` is set (Gateway/enterprise path), the Engine performs a **bidirectional audit** — hard-failing the scan if the config references unknown rule IDs *or* omits rules the engine knows. This catches catalog drift between the Gateway and engine. For CLI-originated scans (partial overrides from `.apme/rules.yml`), unknown IDs produce a warning only.

The `Validator` gRPC contract (`validate.proto`) is unchanged — validators do not need to know about rule overrides or the catalog.

---

## Future considerations

- **Streaming results**: The current contract is unary (one request, one response). For very large projects, server-side streaming (`stream ValidateResponse`) could reduce memory pressure.
- **Third-party plugins (ADR-042)**: Organization-specific rules run as separate plugin containers with their own `Describe` RPC for catalog integration.

## Decision records

See [ADR.md](ADR.md) for the Architecture Decision Record covering all major design choices.
