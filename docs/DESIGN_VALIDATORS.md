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
│  ┌─► OPA        (hierarchy_payload → Rego)      │
│  ├─► Native     (scandata → Python rules)       │
│  └─► Ansible    (files + hierarchy → runtime)    │
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
    "rule_id": str,    # e.g. "L024", "native:L029", "M002"
    "level": str,      # "error", "warning", "info"
    "message": str,
    "file": str,       # relative path
    "line": int,       # or [start, end]
    "path": str,       # hierarchy path
}
```

---

## Implemented validators

### OPA (Rego)

- **Input**: `context.hierarchy_payload` (JSON)
- **Execution**: Rego bundle evaluated by OPA (`data.apme.rules.violations`)
- **Rules**: L002–L025, R118
- **Container**: OPA binary + Python gRPC wrapper (`apme-opa`)
- **Why Rego**: Declarative policy language well-suited for structural checks on JSON; rules are data-driven via `bundle/data.json` (deprecated modules list, package modules, etc.)

### Native (Python)

- **Input**: `context.scandata` (deserialized `SingleScan`)
- **Execution**: Python `Rule` subclasses with `match()` / `process()` methods, invoked in-process by `NativeValidator`
- **Rules**: L026–L056 (lint), R101–R501 (risk), P001–P004 (legacy)
- **Container**: `apme-native`
- **Why Python**: Full access to the in-memory model (trees, contexts, specs, annotations, variable tracking). Rules that need to walk call graphs, inspect variable resolution, or apply complex heuristics that would be awkward in Rego.

### Ansible (runtime)

- **Input**: `context.root_dir` (files on disk) + `context.hierarchy_payload`
- **Execution**: Uses ansible-core's plugin loader, `ansible-playbook --syntax-check`, argspec extraction
- **Rules**: L057–L059 (syntax/argspec), M001–M004 (FQCN resolution, deprecation, redirects, removed modules)
- **Container**: `apme-ansible` with pre-built venvs for ansible-core 2.18/2.19/2.20
- **Why separate container**: Requires actual ansible-core installation; multi-version support needs isolated venvs; collection cache mounted read-only

---

## Engine ownership decision

**Chosen: engine integrated in-tree** (`src/apme_engine/engine/`).

The engine was originally derived from ARI (Ansible Risk Insights). It is now fully integrated — not vendored, not a subprocess, not a dependency. The engine code lives alongside the rest of the application and is tested, modified, and shipped as one unit.

Rationale:

- Full control over the hierarchy payload shape, annotator behavior, and parser logic
- Single parse, single model — validators reuse the same `SingleScan` and `hierarchy_payload`
- No version drift between engine and validators
- Annotators (risk annotations) are engine concerns that feed into both OPA rules (via hierarchy JSON) and native rules (via scandata)

The engine exposes one public function:

```python
# src/apme_engine/runner.py
def run_scan(target_path, project_root, include_scandata=True) -> ScanContext:
```

Everything downstream (validators, daemon, CLI) calls `run_scan()` and works with `ScanContext`.

---

## Parallel execution

Primary calls all three validators concurrently using `concurrent.futures.ThreadPoolExecutor`. Each validator is a gRPC call to an independent container. The `ValidateRequest` is immutable and shared across all calls.

Total latency = `max(native, opa, ansible)` instead of `sum`.

Each validator is discovered by environment variable. If a variable is unset, that validator is skipped — no error, just fewer results. This makes it possible to run a subset of validators during development or testing.

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

1. Implement `ValidatorServicer` (one `Validate` method)
2. Build a container image
3. Add an environment variable to Primary
4. Add the service to the pod spec

No proto changes, no Primary code changes, no other validators affected.

---

## Rule ID independence

Rule IDs (L, M, R, P) describe **what** is checked, not **who** checks it. The user sees `L002` (FQCN check); whether OPA or a Python rule implements it is irrelevant. Multiple validators can fire for the same concept (e.g., OPA L002 is syntactic FQCN; Ansible M001 is semantic FQCN resolution) — they have different rule IDs because they're different checks.

Deduplication happens at the Primary level by `(rule_id, file, line)`. If two validators produce the same rule/file/line, only one is reported.

---

## Future considerations

- **Additional validators**: A yamllint adapter, a custom Go plugin validator, or an AI-assisted reviewer could be added as new containers implementing the same `Validator` service.
- **Streaming results**: The current contract is unary (one request, one response). For very large projects, server-side streaming (`stream ValidateResponse`) could reduce memory pressure.
- **Validator-specific configuration**: Rules can be enabled/disabled per-validator via configuration (not yet implemented at the gRPC level — currently done at the rule level within each validator).
