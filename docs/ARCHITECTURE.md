# Architecture

## Overview

APME is an eight-container microservice deployed as a single Podman pod. The Primary service runs the engine (parse + annotate), then fans validation out **in parallel** to four independent validator backends over a unified gRPC contract. The Gateway provides a REST API and gRPC Reporting service for the React UI. The CLI is ephemeral — run on-the-fly with the project directory mounted.

All inter-service communication is gRPC. The Gateway additionally exposes a REST API for the UI. There is no message queue, no service discovery. Containers in the same pod share `localhost`; addresses are fixed by convention.

All gRPC servers use **`grpc.aio`** (fully async). Blocking work (engine scan, subprocess calls, CPU-bound rules) is dispatched via `asyncio.get_event_loop().run_in_executor()`. Each request carries a **`request_id`** (correlation ID) from Primary through every validator for end-to-end tracing.

## Container topology

```
┌────────────────────────────── apme-pod ───────────────────────────────┐
│                                                                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐ │
│  │ Primary  │  │  Native  │  │   OPA    │  │ Ansible  │  │ Gitleaks │ │
│  │  :50051  │  │  :50055  │  │  :50054  │  │  :50053  │  │  :50056  │ │
│  │          │  │          │  │          │  │          │  │          │ │
│  │ engine + │  │ Python   │  │ OPA bin  │  │ ansible- │  │ gitleaks │ │
│  │ orchestr │  │ rules on │  │ + gRPC   │  │ core     │  │ + gRPC   │ │
│  │ session  │  │ scandata │  │ wrapper  │  │ venvs    │  │ wrapper  │ │
│  │  venvs   │  │          │  │          │  │ (ro)     │  │          │ │
│  └────┬─────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘ │
│       │                                                               │
│  ┌────┴─────────────────────────────────────┐                         │
│  │      Galaxy Proxy :8765 (PEP 503)        │                         │
│  └──────────────────────────────────────────┘                         │
│                                                                       │
│  ┌──────────────────┐  ┌──────────────────┐                           │
│  │ Gateway :8080    │  │ UI :8081 (nginx) │                           │
│  │ REST API +       │◄─┤ React SPA        │                           │
│  │ gRPC Reporting   │  │ /api/ → Gateway  │                           │
│  │ :50060 (SQLite)  │  │                  │                           │
│  └──────────────────┘  └──────────────────┘                           │
└───────────────────────────────────────────────────────────────────────┘

     ┌──────────┐
     │   CLI    │  podman run --rm --pod apme-pod
     │ (on-the  │  -v $(pwd):/workspace:ro,Z
     │  -fly)   │  apme-cli:latest apme check .
     └──────────┘
```

## Services

| Service | Image | Port | Role |
|---------|-------|------|------|
| **Primary** | `apme-primary` | 50051 | Runs the engine (parse → annotate → hierarchy); manages session-scoped venvs (`VenvSessionManager`); fans out `ValidateRequest` to all validators in parallel; merges, deduplicates, and returns violations. Pushes `FixCompletedEvent` to the Gateway via gRPC. |
| **Native** | `apme-native` | 50055 | Python rules operating on deserialized `scandata` (the full in-memory model). Rules L026–L060, M005/M010, P001–P004, R101–R501 |
| **OPA** | `apme-opa` | 50054 | OPA binary (REST on 8181 internally) + Python gRPC wrapper. Rego rules L003–L025, M006/M008/M009/M011, R118 on the hierarchy JSON |
| **Ansible** | `apme-ansible` | 50053 | Ansible-runtime checks using session-scoped venvs (shared read-only via `/sessions` volume). Rules L057–L059, M001–M004 |
| **Gitleaks** | `apme-gitleaks` | 50056 | Gitleaks binary + Python gRPC wrapper. Scans raw files for hardcoded secrets, API keys, private keys. Filters vault-encrypted content and Jinja2 expressions. Rules SEC:* (800+ patterns) |
| **Galaxy Proxy** | `apme-galaxy-proxy` | 8765 | PEP 503 simple repository API that converts Galaxy collection tarballs to pip-installable Python wheels. Caching is the proxy's concern — the engine has zero cache management code |
| **Gateway** | `apme-gateway` | 8080 / 50060 | Dual-protocol: FastAPI REST API (:8080) for the UI and a gRPC Reporting service (:50060) that receives `FixCompletedEvent` from Primary. Persists activity history to SQLite. Health endpoint probes all upstream services. |
| **UI** | `apme-ui` | 8081 | React SPA served by nginx. Proxies `/api/` to the Gateway at `127.0.0.1:8080`. Displays activity history, violations, sessions, and system health. |
| **CLI** | `apme-cli` | — | Ephemeral. **Check** and **remediate** are user-facing actions; the engine uses **`FixSession`** internally for both (ADR-039). The CLI streams project files as chunked **`ScanChunk`** messages on that RPC (check mode omits remediate options). Run with `--pod apme-pod` and CWD mounted |

## gRPC service contracts

Proto definitions live in `proto/apme/v1/`. Generated Python stubs in `src/apme/v1/`.

### Primary (`primary.proto`)

```protobuf
service Primary {
  rpc Scan(ScanRequest) returns (ScanResponse);
  rpc Format(FormatRequest) returns (FormatResponse);
  rpc FormatStream(stream ScanChunk) returns (FormatResponse);
  rpc Health(HealthRequest) returns (HealthResponse);
  rpc FixSession(stream SessionCommand) returns (stream SessionEvent);  // ADR-028, ADR-039
  // ... ListAIModels, etc.
}
```

**`ScanStream` was removed (ADR-039).** **Check** and **remediate** are user-facing actions; the engine uses **`FixSession`** internally for both (chunked **`ScanChunk`** uploads in `SessionCommand`). Unary **`Scan`** accepts a **`ScanRequest`** (optional **`ScanOptions`**, **`scan_id`**) and returns **`ScanResponse`** with merged violations, **`ScanDiagnostics`**, and **`ScanSummary`**. **`FixSession`** is bidirectional streaming for progress, AI proposal review, and session resume.

### Validator (`validate.proto`) — unified contract

```protobuf
service Validator {
  rpc Validate(ValidateRequest) returns (ValidateResponse);
  rpc Health(HealthRequest) returns (HealthResponse);
}
```

Every validator container implements this service. The `ValidateRequest` carries everything any validator might need:

| Field | Type | Used by |
|-------|------|---------|
| `project_root` | `string` | All |
| `files` | `repeated File` | Ansible (writes to temp dir), Gitleaks (writes to temp dir) |
| `hierarchy_payload` | `bytes` (JSON) | OPA, Ansible |
| `scandata` | `bytes` (jsonpickle) | Native |
| `ansible_core_version` | `string` | Ansible |
| `collection_specs` | `repeated string` | Ansible |
| `request_id` | `string` | All (correlation ID for logging/tracing) |

The `ValidateResponse` echoes back `request_id` for correlation and includes a `ValidatorDiagnostics` message with timing data, violation counts, and validator-specific metadata. Each validator ignores the data fields it doesn't need. This keeps the contract uniform — adding a new validator means implementing one RPC and choosing which fields to consume.

### Common types (`common.proto`)

- **`Violation`** — `rule_id`, `level`, `message`, `file`, `line` (int or range), `path`, `remediation_class` (AUTO_FIXABLE / AI_CANDIDATE / MANUAL_REVIEW), `remediation_resolution`
- **`File`** — `path` (relative), `content` (bytes)
- **`HealthRequest` / `HealthResponse`** — status string, downstream `ServiceHealth` list
- **`ScanSummary`** — `total`, `auto_fixable`, `ai_candidate`, `manual_review`, `by_resolution` map
- **`RuleTiming`** — per-rule timing: `rule_id`, `elapsed_ms`, `violations` count
- **`ValidatorDiagnostics`** — per-validator summary: name, request_id, total_ms, file/violation counts, rule timings, metadata map

## Parallel validator fan-out

Primary calls all configured validators concurrently using `asyncio.gather()` with async gRPC stubs:

```
              ┌─► Native   ─── violations ──┐
              │                              │
Primary ──────┼─► OPA      ─── violations ──┼──► merge + dedup + sort
  (async)     │                              │
              ├─► Ansible  ─── violations ──┤
              │                              │
              └─► Gitleaks ─── violations ──┘
```

Wall-clock time = `max(native, opa, ansible, gitleaks)` instead of `sum`. Each validator is discovered by environment variable (`NATIVE_GRPC_ADDRESS`, `OPA_GRPC_ADDRESS`, `ANSIBLE_GRPC_ADDRESS`, `GITLEAKS_GRPC_ADDRESS`). If a variable is unset, that validator is skipped.

## Concurrency model

All gRPC servers use `grpc.aio` (fully async). This means multiple scan requests can be handled concurrently without thread exhaustion.

| Service | Concurrency strategy | `maximum_concurrent_rpcs` |
|---------|---------------------|--------------------------|
| Primary | `asyncio.gather()` fan-out; engine scan via `run_in_executor()` | 16 |
| Native | CPU-bound rules via `run_in_executor()` | 32 |
| OPA | True async HTTP via `httpx.AsyncClient` | 32 |
| Ansible | Blocking venv build + subprocess via `run_in_executor()` | 8 |
| Gitleaks | Blocking subprocess via `run_in_executor()` | 16 |

Each service's `maximum_concurrent_rpcs` is configurable via environment variable (e.g., `APME_PRIMARY_MAX_RPCS`).

### Session-scoped venvs

The Primary orchestrator manages session-scoped venvs via `VenvSessionManager`. Within each session, venvs are keyed by `ansible_core_version` — like tox matrix entries. Collections discovered by FQCN auto-discovery (ADR-032) are installed incrementally via the Galaxy Proxy. Venvs are shared read-only with validators via a `/sessions` volume.

- **Single writer, many readers**: Primary owns venv creation/updates (rw); validators mount read-only
- **Additive, never destructive**: Collections are only added; a new core version creates a sibling venv
- **Idempotent installs**: `uv pip install` is a no-op for already-installed packages — warm sessions pay near-zero cost
- **Client-controlled identity**: `session_id` is always client-provided (VS Code workspace hash, CI job ID)
- **TTL-based reaping**: Individual core-version venvs can expire independently

## Session tracking (request_id)

Every scan request carries a `request_id` (derived from `ScanRequest.scan_id`) that propagates through the entire system:

```
CLI → Primary (scan_id) → ValidateRequest.request_id → each validator logs [req=xxx]
                                                      → ValidateResponse.request_id (echo)
```

All validator logs are prefixed with `[req=xxx]` for end-to-end correlation across concurrent requests.

## Serialization

| Data | Format | Wire type | Producer | Consumer |
|------|--------|-----------|----------|----------|
| Hierarchy payload | JSON (`json.dumps`) | `bytes` in protobuf | Engine (Primary) | OPA, Ansible |
| Scandata | jsonpickle (`jsonpickle.encode`) | `bytes` in protobuf | Engine (Primary) | Native |
| Violations | Protobuf `Violation` messages | gRPC | All validators | Primary |
| Project files | Protobuf `File` messages | gRPC | CLI | Primary, Ansible |

**jsonpickle** is used for scandata because the engine's in-memory model (`SingleScan`) contains complex Python objects (trees, contexts, specs, annotations) that standard JSON cannot represent. jsonpickle preserves types for round-trip deserialization.

## OPA container internals

The OPA container runs a multi-process architecture:

1. **OPA binary** starts as a REST server on `localhost:8181` with the Rego bundle mounted
2. **`entrypoint.sh`** waits for OPA to become healthy
3. **`apme-opa-validator`** (Python gRPC wrapper) starts on port 50054, receives `ValidateRequest`, extracts `hierarchy_payload`, POSTs it to the local OPA REST API, and converts the response to `ValidateResponse`

This keeps OPA's native REST interface intact while presenting a uniform gRPC contract to Primary.

## Gitleaks container internals

The Gitleaks container follows a similar multi-stage pattern:

1. **Gitleaks binary** is copied from the official `zricethezav/gitleaks` image into a Python 3.12 slim image
2. **`apme-gitleaks-validator`** (Python gRPC wrapper) starts on port 50056, receives `ValidateRequest`, writes `files` to a temp directory, runs `gitleaks detect --no-git --report-format json`, parses the JSON report, and converts findings to `ValidateResponse`

The wrapper adds Ansible-aware filtering:
- **Vault filtering**: files containing `$ANSIBLE_VAULT;` headers are excluded
- **Jinja filtering**: matches that are pure Jinja2 expressions (`{{ var }}`) are filtered out as false positives
- **Rule ID mapping**: Gitleaks rule IDs are prefixed with `SEC:` (e.g., `SEC:aws-access-key-id`) and can be mapped to stable APME rule IDs via `RULE_ID_MAP`

## Volumes

| Volume | Mount | Services | Access |
|--------|-------|----------|--------|
| **sessions** | `/sessions` | Primary (rw), Ansible (ro) | Session-scoped venvs with ansible-core + collections |
| **workspace** | `/workspace` | CLI (ro) | Project being scanned (mounted from host CWD) |

## Port map

| Port | Service | Protocol |
|------|---------|----------|
| 50051 | Primary | gRPC |
| 50053 | Ansible | gRPC |
| 50054 | OPA | gRPC (wrapper; OPA REST on 8181 internal) |
| 50055 | Native | gRPC |
| 50056 | Gitleaks | gRPC (wrapper; gitleaks binary for detection) |
| 50060 | Gateway | gRPC (Reporting service) |
| 8080 | Gateway | HTTP (REST API for UI) |
| 8081 | UI | HTTP (nginx; proxies /api/ to Gateway) |
| 8765 | Galaxy Proxy | HTTP (PEP 503 simple repository API) |

## Scaling

**Scale pods, not services within a pod.** Each pod is a self-contained stack (Primary + Native + OPA + Ansible + Gitleaks + Galaxy Proxy) that can process a scan request end-to-end.

```
                    ┌─────────────┐
  ScanRequest ────► │ Load        │
                    │ Balancer    │
                    │ (K8s Svc)   │
                    └──┬──┬──┬────┘
                       │  │  │
              ┌────────┘  │  └────────┐
              ▼           ▼           ▼
         ┌─────────┐ ┌─────────┐ ┌─────────┐
         │ Pod 1   │ │ Pod 2   │ │ Pod 3   │
         │ (full   │ │ (full   │ │ (full   │
         │  stack) │ │  stack) │ │  stack) │
         └─────────┘ └─────────┘ └─────────┘
```

Within a pod, containers share `localhost` — no config change needed. If a single validator is the bottleneck for one request, the fix is parallelism *inside* that validator (e.g., task-level concurrency), not more containers.

The **Galaxy Proxy** could be extracted to a shared service across pods to share a single wheel cache. For single-pod deployments this is unnecessary.

## Diagnostics instrumentation

Every validator and the engine collect structured timing data on every request. Diagnostics flow through the gRPC contract — no log parsing required.

### Proto messages

```protobuf
message RuleTiming {
  string rule_id = 1;
  double elapsed_ms = 2;
  int32  violations = 3;
}

message ValidatorDiagnostics {
  string validator_name = 1;
  string request_id = 2;
  double total_ms = 3;
  int32  files_received = 4;
  int32  violations_found = 5;
  repeated RuleTiming rule_timings = 6;
  map<string, string> metadata = 7;
}

message ScanDiagnostics {
  double engine_parse_ms = 1;
  double engine_annotate_ms = 2;
  double engine_total_ms = 3;
  int32  files_scanned = 4;
  int32  graph_nodes_built = 5;
  int32  total_violations = 6;
  repeated ValidatorDiagnostics validators = 7;
  double fan_out_ms = 8;
  double total_ms = 9;
}
```

### Per-validator instrumentation

| Validator | Timing granularity | Metadata |
|-----------|-------------------|----------|
| **Native** | Per-rule elapsed time from engine's `detect()` timing records | — |
| **OPA** | OPA HTTP query time; per-rule violation counts | `opa_query_ms`, `opa_response_size` |
| **Ansible** | Per-phase: L057 syntax, M001–M004 introspection, L058 argspec-doc, L059 argspec-mock | `ansible_core_version`, `venv_build_ms` |
| **Gitleaks** | Total subprocess time | `subprocess_ms`, `files_written` |

### Engine timing

The engine (`run_scan()`) reports per-phase timing:
- `parse_ms` — target load + PRM load + metadata load
- `tree_build_ms` — call-graph construction (includes ContentGraph build)
- `total_ms` — wall-clock for the full engine run

### Data flow

```
Validator → ValidateResponse.diagnostics (ValidatorDiagnostics)
                    ↓
Primary aggregates all ValidatorDiagnostics + engine timing
                    ↓
ScanResponse.diagnostics (ScanDiagnostics)
                    ↓
CLI displays with -v / -vv
```

### CLI verbosity

| Flag | Display |
|------|---------|
| (none) | Violations only |
| `-v` | Engine time, validator summaries (tree format), top 10 slowest rules |
| `-vv` | Full per-rule breakdown for every validator, metadata, engine phase timing |

With `--json`, the `diagnostics` key is included when `-v` or `-vv` is set.

## Health checks

The CLI `health-check` subcommand calls `Health` on all services and reports status:

```bash
apme health-check
```

The CLI discovers the Primary via `APME_PRIMARY_ADDRESS` env var, a running daemon, or auto-starts one locally.

Primary, Native, OPA, Ansible, and Gitleaks all implement the `Health` RPC. A service returning `status: "ok"` is healthy; any gRPC error marks it degraded.

## Decision records

See [ADR.md](ADR.md) for the full Architecture Decision Record covering all major design choices.
