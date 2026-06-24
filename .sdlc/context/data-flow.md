# Data Flow

This document traces a **check** request from CLI to violation output, covering every transformation and serialization boundary. (The engine still **scans** files internally; the user action is **check**, not “run a scan.”)

**ADR-039:** `ScanStream` and unary `Scan` were removed. The thin CLI and gateway use **`FixSession`** for both **check** and **remediate**. The sequence below shows the engine pipeline and validator fan-out once Primary has the project files via the `FixSession` chunked upload.

## Request Lifecycle

```
User runs:  apme check /path/to/project
            │
            ▼
┌───────────────────────────────────────────────────────┐
│  CLI (apme_engine/cli/)                               │
│                                                       │
│  1. Walk project directory                            │
│  2. Filter: TEXT_EXTENSIONS, skip SKIP_DIRS,          │
│     exclude >2 MiB and binary files                   │
│  3. Open FixSession (bidirectional gRPC stream):      │
│     - Send SessionCommand with ScanChunk messages     │
│     - Each chunk: File(path=relative, content=bytes)  │
│     - options (ansible_core_version, collection_specs)│
│     - No fix_options = check mode (dry-run)           │
│                                                       │
│  gRPC: Primary.FixSession(stream SessionCommand) ─────────┐
└───────────────────────────────────────────────────────┘    │
                                                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  Primary (daemon/primary_server.py)                              │
│                                                                  │
│  4. _write_chunked_fs(): write uploaded files to temp dir        │
│                                                                  │
│  5. run_scan(temp_dir, project_root) in executor:                │
│     ┌────────────────────────────────────────────────────┐       │
│     │  Engine (engine/scanner.py — AnsibleProjectLoader) │       │
│     │                                                    │       │
│     │  a. load_definitions_root()                        │       │
│     │     Parser dispatches by type (project, collection,│       │
│     │     role, playbook, taskfile) → raw definitions:   │       │
│     │     playbooks, roles, taskfiles, tasks, modules    │       │
│     │                                                    │       │
│     │  b. build_content_graph()                          │       │
│     │     GraphBuilder transforms definitions into a     │       │
│     │     ContentGraph (nodes + edges + properties).     │       │
│     │     Node types: PLAY, ROLE, TASK, HANDLER, BLOCK,  │       │
│     │     FILE, etc. Edges encode call relationships.    │       │
│     │                                                    │       │
│     │  c. apply_rules() — internal validation/enrichment │       │
│     │     Produces hierarchy_payload from the graph:     │       │
│     │     { hierarchy: [{root_key, root_type, root_path, │       │
│     │       nodes: [{type, key, file, line, module,      │       │
│     │       options, module_options, annotations}]}],    │       │
│     │     metadata }                                     │       │
│     │                                                    │       │
│     │  Returns: ScanContext                              │       │
│     │    .hierarchy_payload = dict (JSON-serializable)   │       │
│     │    .scandata = SingleScan (with .content_graph)    │       │
│     └────────────────────────────────────────────────────┘       │
│                                                                  │
│  6. Build ValidateRequest for each validator:                    │
│     - hierarchy_payload = json.dumps(ctx.hierarchy_payload)      │
│     - content_graph_data = ContentGraph.to_dict(slim=True)       │
│     - files, venv_path, session_id, request_id                   │
│                                                                  │
│  7. Parallel fan-out (asyncio.gather):                           │
│     ┌─────────────────────────────────────────────────────┐      │
│     │                                                     │      │
│     │  ┌─► Native :50055                                  │      │
│     │  │   - Deserialize ContentGraph from                 │      │
│     │  │     content_graph_data (JSON dict)                │      │
│     │  │   - Run ~90 GraphRule subclasses on graph nodes   │      │
│     │  │   → violations[] + ValidatorDiagnostics          │      │
│     │  │                                                  │      │
│     │  ├─► OPA :50054                                     │      │
│     │  │   - json.loads(hierarchy_payload)                 │      │
│     │  │   - subprocess: opa eval -I -d /bundle           │      │
│     │  │     (local binary; NOT the REST server)           │      │
│     │  │   → violations[] + ValidatorDiagnostics          │      │
│     │  │                                                  │      │
│     │  ├─► Ansible :50053                                 │      │
│     │  │   - Write files to temp dir                      │      │
│     │  │   - Use session venv from Primary (read-only)     │      │
│     │  │   - Run AnsibleValidator (syntax, argspec,       │      │
│     │  │     FQCN, deprecation, redirect, removed)        │      │
│     │  │   → violations[] + ValidatorDiagnostics          │      │
│     │  │                                                  │      │
│     │  ├─► Gitleaks :50056                                │      │
│     │  │   - Pipe node content to gitleaks --pipe stdin    │      │
│     │  │   - Filter vault + Jinja false positives         │      │
│     │  │   → violations[] + ValidatorDiagnostics          │      │
│     │  │                                                  │      │
│     │  ├─► Collection Health :50058 (optional)            │      │
│     │  │   - Scan installed collections in session venv   │      │
│     │  │   → findings[] + ValidatorDiagnostics            │      │
│     │  │                                                  │      │
│     │  └─► Dep Audit :50059 (optional)                    │      │
│     │      - pip-audit against session venv packages      │      │
│     │      → findings[] + ValidatorDiagnostics            │      │
│     │                                                     │      │
│     └─────────────────────────────────────────────────────┘      │
│                                                                  │
│  8. Merge all violations                                         │
│  9. Deduplicate by (rule_id, file, line)                         │
│ 10. Sort by (file, line)                                         │
│ 11. Convert to proto Violation messages                          │
│ 12. Aggregate diagnostics:                                       │
│     - Engine timing (parse, graph_construction, total)           │
│     - Each validator's ValidatorDiagnostics                      │
│     - Fan-out wall-clock, total wall-clock                       │
│                                                                  │
│  Stream back SessionEvent(violations, diagnostics, patches)      │
└──────────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌───────────────────────────────────────────────────────┐
│  CLI                                                  │
│                                                       │
│ 13. Print violations (table or --json)                │
│     rule_id | level | file:line | message             │
│                                                       │
│ 14. If -v: show validator summaries +                 │
│     top 10 slowest rules                              │
│     If -vv: full per-rule breakdown,                  │
│     metadata, engine phase timing                     │
└───────────────────────────────────────────────────────┘
```

---

## Engine Pipeline Detail

The engine (`AnsibleProjectLoader.load()`) runs multiple stages in sequence. All stages operate on the same in-memory model; there is no intermediate serialization between stages.

### Stage 1: Load Definitions (`target_load`)

`Parser` dispatches by load type (PROJECT, COLLECTION, ROLE, PLAYBOOK, TASKFILE). Produces:

| Output | Description |
|--------|-------------|
| `root_definitions` | playbooks, roles, taskfiles, tasks, modules found in the scan target |
| `ext_definitions` | external dependencies (collections from `dependency_dir`) |

### Stage 2: Build ContentGraph (`graph_construction`)

`GraphBuilder` transforms raw definitions into a `ContentGraph` — a directed graph where:

- **Nodes** represent structural elements (PLAY, ROLE, TASK, HANDLER, BLOCK, FILE, etc.)
- **Edges** encode call relationships (play → role, role → taskfile → task)
- **Properties** on each node: `file`, `line`, `module`, `options`, `module_options`, `annotations`

The graph preserves execution order, nesting, and variable scope. Node identity is stable across rescans (ADR-044).

### Stage 3: Apply Rules / Build Hierarchy (`apply_rules`)

Runs internal enrichment and builds the hierarchy payload from the graph:

- Resolves module FQCNs, variable provenance, and risk annotations
- Serializes the graph into a flat JSON hierarchy for OPA/Ansible validators
- Annotations (e.g., `cmd_exec`, `inbound_transfer`, `file_change`) are attached to task nodes and serialized into the hierarchy payload's `annotations` array

### Hierarchy Payload Shape

Serializes into a flat JSON structure consumable by OPA and other payload-based validators:

```json
{
  "scan_id": "uuid",
  "hierarchy": [
    {
      "root_key": "playbook:/path/to/pb.yml",
      "root_type": "playbook",
      "root_path": "/path/to/pb.yml",
      "nodes": [
        {
          "type": "taskcall",
          "key": "task:...",
          "file": "pb.yml",
          "line": 5,
          "module": "ansible.builtin.shell",
          "options": { "name": "Run something", "become": true },
          "module_options": { "_raw_params": "echo hello" },
          "annotations": [
            { "risk_type": "cmd_exec", "detail": { "cmd": "echo hello" } }
          ]
        }
      ]
    }
  ],
  "metadata": { "type": "project", "name": "myproject" }
}
```

---

## Serialization Boundaries

### CLI → Primary (gRPC)

Files are sent as protobuf `File` messages (`path` + `content` bytes). This is the **"chunked filesystem" pattern** — the CLI reads all text files from the project and sends them over the wire so the Primary doesn't need filesystem access.

### Primary → Validators (gRPC)

Multiple serialization formats in one `ValidateRequest`:

| Field | Format | Used By | Description |
|-------|--------|---------|-------------|
| `hierarchy_payload` | `json.dumps()` → bytes | OPA, Ansible | Complete hierarchy as JSON. Rego and Ansible runtime operate on JSON. |
| `content_graph_data` | `ContentGraph.to_dict(slim=True)` → JSON bytes | Native, Gitleaks | Serialized ContentGraph (nodes, edges, properties). Native runs GraphRules on it; Gitleaks extracts node content for pipe-mode scanning. |
| `files` | protobuf `File` messages | Ansible | Raw file content for writing to temp dirs (syntax check). |
| `venv_path` | string | Ansible, Collection Health, Dep Audit | Path to session-scoped venv (read-only for validators). |

### Validators → Primary (gRPC)

Each validator returns `ValidateResponse` containing:
- Protobuf `Violation` messages
- `ValidatorDiagnostics` with per-rule timing, violation counts, and validator-specific metadata

Primary converts violations to dicts, merges, deduplicates, and converts back to proto. It also aggregates all `ValidatorDiagnostics` with engine phase timing into a `ScanDiagnostics` message streamed via the `SessionEvent`.

---

## Diagnostics Flow

```
Engine → EngineDiagnostics (parse_ms, annotate_ms, tree_build_ms, total_ms)
                              ↓
Native  → ValidatorDiagnostics (per-rule timing from GraphRule match/process)
OPA     → ValidatorDiagnostics (subprocess_ms, per-rule violation counts)
Ansible → ValidatorDiagnostics (per-phase: syntax, introspect, argspec; venv_build_ms)
Gitleaks→ ValidatorDiagnostics (subprocess_ms)
Coll Health → ValidatorDiagnostics (per-collection timing, collections_scanned)
Dep Audit → ValidatorDiagnostics (subprocess_ms, packages_audited)
                              ↓
Primary aggregates → ScanDiagnostics
                              ↓
SessionEvent.diagnostics → CLI (-v / -vv) or JSON consumer
```

---

## Violation Shape

Every violation, regardless of source validator, has the same structure:

| Field | Type | Example |
|-------|------|---------|
| `rule_id` | string | `"L024"`, `"native:L026"`, `"M002"` |
| `level` | string | `"error"`, `"warning"`, `"info"` |
| `message` | string | human-readable description |
| `file` | string | relative path to file |
| `line` | int | line number (or `LineRange {start, end}`) |
| `path` | string | hierarchy path (e.g., `"playbook > play > task"`) |

### Rule ID Prefix Convention

| Prefix | Source |
|--------|--------|
| (no prefix) | OPA rule |
| `native:` | Native Python rule |
| (no prefix) | Ansible/Modernize rule (M001–M004, L057–L059) |

---

## OPA Execution Modes

OPA policy evaluation always uses `opa eval` as a **subprocess** — never an HTTP query.
The mechanism varies by deployment:

| Deployment | OPA binary location | Mechanism |
|------------|-------------------|-----------|
| **Podman pod** (container) | `/usr/local/bin/opa` (copied from OPA image at build time) | Direct subprocess: `opa eval -I -d /bundle <entrypoint>`. Fast — no container startup per eval. |
| **CLI daemon** (host, default) | Inside an ephemeral Podman container | `podman run --rm ... opa eval` per evaluation. Slower — container startup overhead. |
| **CLI daemon** (host, `OPA_USE_PODMAN=0`) | Local `opa` binary on `$PATH` | Direct subprocess. Same speed as container path. |

**Note:** The OPA container's `entrypoint.sh` starts an OPA REST server on :8181,
but the gRPC wrapper does **not** query it. The REST server exists only for the
entrypoint's readiness-wait loop; the actual evaluation always uses `opa eval`
subprocess. This is a known vestige.

A timeout-based circuit breaker (default: 3 consecutive 60s timeouts) disables
OPA evaluation for the remainder of the process when evaluations consistently hang.

---

## Daemon Mode

The CLI daemon (`apme daemon start`) runs Primary + validators as localhost gRPC
servers in a single process without containers:

1. Engine + Primary run directly
2. Native, OPA, Ansible validators run as in-process gRPC servers
3. Galaxy Proxy runs as a local uvicorn HTTP server
4. OPA invoked via Podman container or local binary (see table above)
5. Results merged via the same gRPC fan-out as the pod

The daemon supports all required validators (Native, OPA, Ansible, Galaxy Proxy).
Optional validators (Gitleaks, Collection Health, Dep Audit) start when
`include_optional=True`.

---

## Summary

```
┌─────────────┐     ┌─────────────┐     ┌─────────────────────────────────┐
│    CLI      │────►│   Primary   │────►│           Validators            │
│             │     │   Engine    │     │  Native | OPA | Ansible |       │
│ FixSession  │     │             │     │  Gitleaks | CollHealth | Dep    │
│ (files)     │     │ hierarchy + │     │                                 │
└─────────────┘     │ graph +     │     │ ValidateRequest                 │
                    │ venv_path   │     │ (hierarchy | content_graph |    │
                    └──────┬──────┘     │  files | venv_path)             │
                           │            └──────────────────┬──────────────┘
                           │                               │
                           │◄──────────────────────────────┘
                           │         violations[]
                           │         + diagnostics
                           ▼
                    ┌─────────────┐
                    │   Primary   │
                    │   Merge +   │
                    │   Dedup     │
                    └──────┬──────┘
                           │
                           ▼
                    ┌─────────────┐
                    │    CLI      │
                    │   Display   │
                    │ violations  │
                    └─────────────┘
```

## Related Documents

- [Architecture series](/docs/architecture/) — Container topology and service contracts
- [ADR-001](/adrs/ADR-001-grpc-communication.md) — gRPC communication
- [ADR-003](/adrs/ADR-003-vendor-ari-engine.md) — Vendored ARI engine
- [ADR-013](/adrs/ADR-013-structured-diagnostics.md) — Diagnostics instrumentation
