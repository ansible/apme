# Data flow

This document traces a **check** run from the CLI to violation output, covering every transformation and serialization boundary. The engine still runs an internal scan pipeline (`run_scan`, `scan_id`, etc.); **check** is the user-facing name for that operation.

## Request lifecycle

```
User runs:  apme check /path/to/project
            │
            ▼
┌───────────────────────────────────────────────────────┐
│  CLI (apme_engine/cli/)                                │
│                                                       │
│  1. Discover project root (walk up for .git,          │
│     galaxy.yml, requirements.yml, ansible.cfg,        │
│     pyproject.toml) → derive session_id (SHA-256)     │
│  2. Walk project directory                            │
│  3. Filter: TEXT_EXTENSIONS, skip SKIP_DIRS,          │
│     skip SKIP_FILENAMES (.travis.yml), apply          │
│     .apmeignore patterns, exclude >2 MiB/binary       │
│  4. Build a stream of ScanChunk messages (chunked FS):       │
│     - scan_id (uuid) on first chunk                             │
│     - session_id (from project root or --session)               │
│     - project_root (basename)                                   │
│     - files[] = File(path=relative, content=bytes) per chunk    │
│     - ScanOptions on first chunk (ansible_core_version,         │
│       collection_specs, galaxy_servers — ADR-045,               │
│       rule_configs — ADR-041)                                   │
│                                                                 │
│  gRPC: Primary.FixSession(stream SessionCommand) — ADR-039      │
│        Each SessionCommand carries upload=ScanChunk until       │
│        last chunk; check mode (no FixOptions / remediate).      │
│        ScanStream RPC removed; FixSession is the CLI stream.     │
└───────────────────────────────────────────────────────┘       │
                                                                ▼
┌──────────────────────────────────────────────────────────────────┐
│  Primary (daemon/primary_server.py)                              │
│                                                                  │
│  4. _write_chunked_fs(): write request.files to temp dir         │
│                                                                  │
│  5. run_scan(temp_dir, project_root):                            │
│     ┌────────────────────────────────────────────────────┐       │
│     │  Engine (engine/scanner.py — ARIScanner.evaluate)  │       │
│     │                                                    │       │
│     │  a. load_definitions_root()                        │       │
│     │     Parser.run() → playbooks, roles, taskfiles,    │       │
│     │     tasks, modules, mappings                       │       │
│     │                                                    │       │
│     │  b. build_content_graph()                           │       │
│     │     GraphBuilder → ContentGraph (ADR-044, sole     │       │
│     │     execution path for native rules & OPA)         │       │
│     │                                                    │       │
│     │  c. build_hierarchy_payload()                      │       │
│     │     Serialize trees → JSON hierarchy:              │       │
│     │     { scan_id, hierarchy: [{root_key, root_type,   │       │
│     │       root_path, nodes: [{type, key, file, line,   │       │
│     │       module, options, module_options,              │       │
│     │       annotations}]}],                             │       │
│     │       collection_set: ["ns.coll", ...],            │       │
│     │       metadata }                                   │       │
│     │                                                    │       │
│     │  Returns: ScanContext                              │       │
│     │    .hierarchy_payload = dict (JSON-serializable)   │       │
│     │    .scandata = SingleScan (full in-memory model)   │       │
│     └────────────────────────────────────────────────────┘       │
│                                                                  │
│  6. Build ValidateRequest:                                       │
│     - hierarchy_payload = json.dumps(ctx.hierarchy_payload,      │
│                                      default=str)                │
│     - scandata = jsonpickle.encode(ctx.scandata)                 │
│     - files, ansible_core_version, collection_specs              │
│                                                                  │
│  7. Parallel fan-out (asyncio.gather):                           │
│     ┌─────────────────────────────────────────────────────┐      │
│     │                                                     │      │
│     │  ┌─► Native :50055                                  │      │
│     │  │   - Deserialize ContentGraph from scandata       │      │
│     │  │   - GraphRule evaluation via graph_scanner.scan() │      │
│     │  │   → violations[] + ValidatorDiagnostics          │      │
│     │  │                                                  │      │
│     │  ├─► OPA :50054                                     │      │
│     │  │   - json.loads(hierarchy_payload)                 │      │
│     │  │   - opa eval via subprocess (ADR-009)            │      │
│     │  │   - Rego eval: data.apme.rules.violations        │      │
│     │  │   → violations[] + ValidatorDiagnostics          │      │
│     │  │                                                  │      │
│     │  ├─► Ansible :50053                                 │      │
│     │  │   - Write files to temp dir                      │      │
│     │  │   - Use session venv from /sessions (read-only)  │      │
│     │  │   - Run AnsibleValidator (syntax, argspec,       │      │
│     │  │     FQCN, deprecation, redirect, removed)        │      │
│     │  │   → violations[] + ValidatorDiagnostics          │      │
│     │  │                                                  │      │
│     │  └─► Gitleaks :50056                                │      │
│     │      - Write files to temp dir                      │      │
│     │      - Run gitleaks detect --no-git                 │      │
│     │      - Filter vault + Jinja false positives         │      │
│     │      → violations[] + ValidatorDiagnostics          │      │
│     │                                                     │      │
│     └─────────────────────────────────────────────────────┘      │
│                                                                  │
│  8. Validate rule_configs (ADR-041):                             │
│     - If rule_configs_complete (Gateway): bidirectional audit    │
│       — hard fail on unknown IDs *and* missing IDs              │
│     - If partial (CLI): warn on unknown IDs, continue           │
│  9. Merge all violations                                         │
│ 10. Apply rule_configs: filter disabled rules, override severity,│
│      mark enforced (ADR-041)                                     │
│ 11. Deduplicate by (rule_id, file, line)                         │
│ 12. Sort by (file, line)                                         │
│ 13. Convert to proto Violation messages                          │
│ 14. Aggregate diagnostics:                                       │
│     - Engine timing (parse, annotate, tree build)                │
│     - Each validator's ValidatorDiagnostics                      │
│     - Fan-out wall-clock, total wall-clock                       │
│                                                                  │
│  Stream SessionEvent (progress, …); result event carries         │
│  violations + diagnostics (same merge/dedup as unary Scan).      │
└──────────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌───────────────────────────────────────────────┐
│  CLI                                          │
│                                               │
│ 13. Print violations (table or --json)        │
│     rule_id | level | file:line | message     │
│                                               │
│ 14. If -v: show validator summaries +         │
│     top 10 slowest rules                      │
│     If -vv: full per-rule breakdown,          │
│     metadata, engine phase timing             │
└───────────────────────────────────────────────┘
```

## Engine pipeline detail

The engine (`ARIScanner.evaluate()`) runs five stages in sequence. All stages operate on the same in-memory model; there is no intermediate serialization between stages.

### Stage 1: Load definitions

`Parser.run()` dispatches by load type (`PROJECT`, `COLLECTION`, `ROLE`, `PLAYBOOK`, `TASKFILE`). Produces:

- `root_definitions` — playbooks, roles, taskfiles, tasks, modules found in the scan target
- `ext_definitions` — external dependencies (collections, roles from cache)
- `mappings` — index of module → FQCN, role → path, etc.

### Stage 2: Build ContentGraph

`GraphBuilder` constructs a `ContentGraph` (ADR-044) — a DAG-backed model where each Ansible content unit (playbook, play, role, task, module, collection) exists as a deduplicated node with typed edges:

```
Playbook → Play → Task (CONTAINS)
                 → Role (DEPENDENCY / INCLUDE / IMPORT)
                 → TaskFile (IMPORT / INCLUDE)
```

Resolution bookkeeping (`extra_requirements`, `resolve_failures`) is tracked during construction. Variable provenance is resolved via `VariableProvenanceResolver` on the graph.

### Stage 3: Annotate

Per-module `RiskAnnotator` subclasses inspect each `TaskCall` and attach `RiskAnnotation` objects:

| Annotator | Risk types |
|-----------|------------|
| `ShellAnnotator` | `CMD_EXEC` |
| `CommandAnnotator` | `CMD_EXEC` |
| `GetUrlAnnotator` | `INBOUND_TRANSFER` |
| `UriAnnotator` | `INBOUND_TRANSFER`, `OUTBOUND_TRANSFER` |
| `CopyAnnotator` | `FILE_CHANGE` |
| `FileAnnotator` | `FILE_CHANGE` |
| `UnarchiveAnnotator` | `FILE_CHANGE`, `INBOUND_TRANSFER` |
| `LineinfileAnnotator` | `FILE_CHANGE` |
| `GitAnnotator` | `INBOUND_TRANSFER` |
| `PackageAnnotator` | `PACKAGE_INSTALL` |

Annotations are attached to the `TaskCall` and serialized into the hierarchy payload's `annotations` array, making them available to OPA rules (e.g., R118 checks for `inbound_transfer`).

### Stage 5: Build hierarchy payload

Serializes the tree into a flat JSON structure consumable by OPA and other payload-based validators:

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
  "collection_set": ["ansible.posix", "community.general"],
  "metadata": { "type": "project", "name": "myproject" }
}
```

## Serialization boundaries

### CLI → Primary (gRPC)

Files are sent as protobuf `File` messages (path + content bytes) inside streamed **`ScanChunk`** payloads on **`FixSession`** (check and remediate). This is the "chunked filesystem" pattern — the CLI reads all text files from the project and sends them over the wire so the Primary doesn't need filesystem access. **`ScanStream`** was removed (ADR-039); **`FixSession`** is the single streaming RPC for those flows.

### Primary → Validators (gRPC)

Two serialization formats in one `ValidateRequest`:

1. **`hierarchy_payload`** — `json.dumps()` → bytes. The complete hierarchy as JSON. Used by OPA (Rego operates on JSON) and Ansible (for reference).

2. **`scandata`** — `jsonpickle.encode()` → bytes. The full `SingleScan` object including trees, contexts, specs, and annotations. Used by Native (needs the in-memory Python object model). jsonpickle preserves Python types for round-trip `decode()`.

### Validators → Primary (gRPC)

Each validator returns `ValidateResponse` containing:
- Protobuf `Violation` messages
- `ValidatorDiagnostics` with per-rule timing, violation counts, and validator-specific metadata

Primary converts violations to dicts, merges, deduplicates, and converts back to proto. It also aggregates all `ValidatorDiagnostics` with engine phase timing into a `ScanDiagnostics` message on the `ScanResponse`.

### Diagnostics flow

```
Engine → EngineDiagnostics (parse_ms, annotate_ms, tree_build_ms, total_ms)
                              ↓
Native  → ValidatorDiagnostics (per-rule timing from detect() records)
OPA     → ValidatorDiagnostics (opa_query_ms, per-rule violation counts)
Ansible → ValidatorDiagnostics (per-phase: syntax, introspect, argspec; venv_build_ms)
Gitleaks→ ValidatorDiagnostics (subprocess_ms, files_written)
                              ↓
Primary aggregates → ScanDiagnostics
                              ↓
ScanResponse.diagnostics → CLI (-v / -vv) or JSON consumer
```

## Violation shape

Every violation, regardless of source validator, has the same structure:

```
rule_id   : string   e.g. "L024", "native:L026", "M002"
level     : string   "error", "warning", "info"
message   : string   human-readable description
file      : string   relative path to file
line      : int      line number (or LineRange {start, end})
path      : string   hierarchy path (e.g. "playbook > play > task")
metadata  : map      rule-specific key/value pairs (e.g. resolved_fqcn,
                      original_module, with_key, redirect_chain, removal_msg)
```

The `metadata` map carries fields that transforms need but don't fit the common schema. For example, M001 violations include `resolved_fqcn` (the target FQCN from ansible-core introspection) and `original_module` (the literal YAML key). These are serialized through the gRPC `Violation.metadata` map field and round-tripped by `violation_convert.py`.

The `rule_id` prefix convention:
- No prefix → OPA rule
- `native:` → native Python rule
- No prefix → Ansible/Modernize rule (M001–M004, L057–L059)

## Event reporting (Primary → Gateway → UI)

After every **check** or **remediate** run, the Primary pushes a `FixCompletedEvent` to the Gateway's gRPC Reporting service (if `APME_REPORTING_ENDPOINT` is configured). The Gateway persists the event to SQLite and the UI reads it via the REST API.

```
Primary (check/remediate completes)
    │
    │  await emit_fix_completed(FixCompletedEvent)
    │    ↓
    │  GrpcReportingSink.on_fix_completed()
    │    ↓
    │  gRPC → Gateway :50060 ReportFixCompleted
    │
    ▼
Gateway (grpc_reporting/servicer.py)
    │  Upsert session row
    │  Insert activity row + violations + logs + ContentGraph → SQLite
    │
    ▼
UI (React SPA on :8081)
    │  GET /api/v1/activity (nginx proxies to Gateway :8080)
    │  GET /api/v1/projects/{id}/graph → ContentGraph visualization
    │  Renders activity history, violations, session trends, graph
```

Event emission uses ``await`` so delivery completes before the operation returns to the client. When the Reporting endpoint is known-down, a fast-fail timeout (1 s) prevents blocking the check/remediate path.

## Rule catalog registration (Primary → Gateway, ADR-041)

At startup, the authority Primary collects rule definitions from all built-in
validators (native via `load_graph_rules()`, OPA/Ansible via `.md` frontmatter,
Gitleaks as a `SEC:*` placeholder) and pushes the full catalog to the Gateway
via `RegisterRules`. The Gateway reconciles the `rules` table — adding new rules,
removing absent ones, and updating changed metadata.

```
Primary startup
    │
    │  collect_all_rules() → [RuleDefinition, ...]
    │  RegisterRulesRequest(pod_id, is_authority=True, rules)
    │    ↓
    │  GrpcReportingSink.register_rules()
    │    ↓
    │  gRPC → Gateway :50060 RegisterRules
    │
    ▼
Gateway (grpc_reporting/servicer.py)
    │  Reconcile: add new, delete absent, update changed → rules table
    │  RegisterRulesResponse(accepted, rules_added, rules_removed, ...)
```

## Rule override injection (Gateway → Engine, ADR-041)

When the Gateway initiates a scan (UI or API), it loads rule overrides from the
`rule_overrides` table, resolves effective configuration (default + override),
and injects `RuleConfig` protos into `ScanOptions.rule_configs` on the first
`ScanChunk`. The Primary validates rule IDs against its known set (hard fail on
unknown) and applies the configs after validator fan-out.

For CLI-initiated scans, `rule_configs` are parsed from `.apme/rules.yml` in
the project root.

## Galaxy server injection (Gateway → Engine, ADR-045)

The Gateway stores global Galaxy server definitions (name, URL, token, auth URL)
in its SQLite database.  When initiating any project operation (check or
remediate) via the WebSocket endpoints or playground session, the Gateway loads
all configured servers and injects them into the gRPC request:

- `ScanOptions.galaxy_servers` on the first `ScanChunk`
- `FixOptions.galaxy_servers` on the first chunk (remediate mode)

The Primary writes these into a session-scoped temporary `ansible.cfg` so that
`ansible-galaxy collection download` can authenticate against private Galaxy /
Automation Hub instances without any per-project configuration.

For CLI-initiated scans, `galaxy_servers` are parsed from the user's local
`ansible.cfg` instead (PR 2).

Token values are never exposed in the REST API — the response schema reports
only `has_token: bool`.  Application-layer encryption of stored tokens is a
documented follow-up requirement.

## Local daemon mode

When running without the Podman pod, the CLI connects to a local daemon via `ensure_daemon()`:

1. If `APME_PRIMARY_ADDRESS` is set, the CLI connects to that address directly
2. If a daemon is already running (`~/.apme-data/daemon.json`), the CLI reuses it
3. Otherwise, the CLI auto-starts a background daemon (`apme daemon start`)

The local daemon runs the Primary, Native, OPA, and Ansible validators as localhost gRPC servers, and the Galaxy Proxy as a localhost HTTP (uvicorn) server, all within a single background process. These five services are all required — Galaxy Proxy is the sole collection installation path for session venvs. The CLI always talks to the Primary service over gRPC; it never runs the engine in-process or communicates with Galaxy Proxy directly.

Gitleaks is the only optional service (requires the `gitleaks` binary). Pass `include_optional=True` to `start_daemon()` to enable it.
