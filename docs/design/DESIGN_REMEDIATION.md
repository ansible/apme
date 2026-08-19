# Remediation Engine Design

## Overview

The remediation engine is a separate service that consumes scan violations and produces file patches. It is **not** the formatter. The formatter is a blind pre-pass that normalizes YAML style; the remediation engine is a violation-driven transform pipeline that fixes detected issues.

```
┌──────────┐     ┌─────────────┐     ┌──────────┐     ┌──────────────────┐     ┌──────────┐
│ Formatter│ ──► │ Idempotency │ ──► │  Scan    │ ──► │  Remediation    │ ──► │ Re-check │
│ (Phase 1)│     │    Gate     │     │ (engine  │     │    Engine       │     │          │
│          │     │             │     │  + all   │     │                 │     │          │
│ blind    │     │ format again│     │ validtrs)│     │ partition →     │     │ verify   │
│ pre-pass │     │ assert zero │     │          │     │ transform / AI  │     │ fixes    │
│          │     │ diffs       │     │          │     │                 │     │          │
└──────────┘     └─────────────┘     └──────────┘     └──────────────────┘     └──────────┘
                                                              │                      │
                                                              │    ┌─────────────────┘
                                                              │    │ count decreased?
                                                              │    │ repeat (max N)
                                                              ▼    ▼
                                                        ┌──────────────┐
                                                        │   Report     │
                                                        │  (converged  │
                                                        │   or bail)   │
                                                        └──────────────┘
```

## Why the Formatter Is Not Part of the Remediation Engine

| | Formatter (Phase 1) | Remediation Engine (Phase 2+) |
|---|---|---|
| **Input** | Raw YAML text | Violations from a scan |
| **Trigger** | Always runs (blind pre-pass) | Only runs when violations exist |
| **Logic** | Fixed transforms: tabs, indentation, key order, Jinja spacing | Rule-specific transforms + AI escalation |
| **Needs a scan?** | No | Yes |
| **Goal** | Canonical formatting so downstream diffs are clean | Fix detected issues |

Routing the formatter through the remediation engine would require:
1. Running a scan before formatting (to produce violations for the engine to consume)
2. Inventing artificial "formatting violation" rules that don't exist today
3. Creating a circular dependency: format needs scan, scan assumes formatted input

The formatter is a **pre-condition** for the remediation engine. `apme format` works without any scan infrastructure — fast, standalone, no containers needed.

## Fix Pipeline

The `apme remediate` command orchestrates the full pipeline:

```
Phase 1: Format
  └─► format all YAML files (tabs, indentation, key order, Jinja spacing)
  └─► write changes if --apply

Phase 2: Idempotency Gate
  └─► format again
  └─► assert zero diffs (if not: formatter bug, abort)

Phase 3: Engine check (internal scan pipeline)
  └─► run engine (parse → annotate → hierarchy)
  └─► fan out to all validators (Native, OPA, Ansible, Gitleaks)
  └─► merge + deduplicate violations

Phase 4: Remediate (Tier 1 — deterministic)
  └─► partition violations via is_finding_resolvable()
  └─► apply Tier 1 transforms from the Transform Registry
  └─► re-check (internal re-scan) → repeat until converged or oscillation (max --max-passes)

Phase 5: AI Escalation (Tier 2 — AI-proposable)
  └─► route Tier 2 violations to Abbenay AIProvider (if --ai)
  └─► generate patches with confidence scores
  └─► apply accepted patches (--auto-approve) or present for review

Phase 6: Report
  └─► summary: Tier 1 fixed, Tier 2 proposals, Tier 3 manual review
```

### Where Each Component Lives

| Component | Location | Why |
|-----------|----------|-----|
| Formatter | CLI (in-process) or Primary (`Format` RPC) | No scan needed; operates on raw files |
| Engine check (internal scan) | Primary service (gRPC) / `FixSession` | Already implemented |
| Remediation Engine | Primary service (`FixSession` RPC) | Needs access to scan results and file content; can call AIProvider |
| Transform Registry | `src/apme_engine/remediation/transforms/` | Pure functions, no container needed |
| AI Escalation | Abbenay daemon (gRPC) | Separate process/container, optional, enabled via `--ai` |

## Three-Tier Finding Classification

Every violation flows through a three-tier classification that determines how it is handled:

| Tier | Label | Handler | Confidence | User Action |
|------|-------|---------|------------|-------------|
| **1 — Deterministic** | `fixable: true` | Transform Registry | 100% — the transform is a known-correct rewrite | None (auto-applied) |
| **2 — AI-Proposable** | `ai_proposable: true` | AIProvider (Abbenay) | Variable — LLM generates a patch with a confidence score | Review proposal, accept/reject (or `--auto-approve` in CI) |
| **3 — Manual Review** | neither | Human | N/A — requires judgment, policy, or external context | Fix by hand |

### Tier 1: Deterministic Fixes (Transform Registry)

These are mechanical rewrites where the correct output is unambiguous given the input and the rule definition. Examples:

- **L021** — add `mode: '0644'` to `file`/`copy`/`template` tasks missing an explicit mode
- **L007** — replace `ansible.builtin.shell` with `ansible.builtin.command` when no shell features are used
- **M001** — rewrite short module names to FQCN using `resolved_fqcn` from ansible-core introspection or OPA L005
- **M005** — rename deprecated parameter (`sudo:` → `become:`)

The transform function receives a `CommentedMap` task and the violation dict, modifies the task in-place, and returns `True` if changed. No ambiguity, no judgment.

### Tier 2: AI-Proposable Fixes (Abbenay AIProvider)

These violations have a clear "what needs to change" but the "how" requires understanding context that a static transform cannot capture. The AI generates a patch and attaches a confidence score. Examples:

- **R118** — restructure complex Jinja2 logic in `when:` clauses (many valid refactorings)
- **M003** — rewrite tasks using removed modules to use their replacement (may require restructuring parameters)
- **SEC:\*** — replace hardcoded secrets with vault lookups (AI can infer the variable name from context)
- **L030** — extract complex `ansible.builtin.shell` one-liners into scripts (requires understanding intent)

AI proposals are never auto-applied by default. The user reviews the diff and accepts or rejects. `--auto-approve` enables unattended mode for CI.

### Tier 3: Manual Review

A small residual category where the "right answer" depends on organizational policy, external systems, or human judgment that neither a transform nor an AI can resolve with confidence. Examples:

- Which vault path to store a rotated secret in
- Whether to split a 500-line playbook into roles (architectural decision)
- Which trusted Galaxy source to use for a dependency

These are reported as "manual review required" with the rule message and context. The remediation engine does not attempt a fix.

### Why Three Tiers, Not Two

A binary "fixable / not fixable" misrepresents the AI capability. Many violations *can* be fixed by AI with high confidence — they are not "manual review" in any meaningful sense. The three-tier model:

1. Gives users a clear expectation: Tier 1 is always safe, Tier 2 needs a glance, Tier 3 needs thought.
2. Lets CI pipelines opt in to AI fixes (`--auto-approve`).
3. Keeps the `fixable` flag honest — it means "deterministically correct, zero risk of wrong output."

## Finding Partition

### `is_finding_resolvable()`

The partition function routes violations into Tier 1 vs. Tier 2+3:

```python
def is_finding_resolvable(violation: dict, registry: TransformRegistry) -> bool:
    """Return True if the violation has a registered deterministic transform (Tier 1)."""
    return violation.get("rule_id", "") in registry
```

This is intentionally simple. A violation is resolvable if and only if the transform registry has a function for that rule ID. No heuristics, no guessing.

Violations that fail this check proceed to AI escalation (Tier 2) if an AIProvider is available (via `--ai`), otherwise they are reported as manual review (Tier 3).

A small set of **task-scoped** rules still route to Tier 3
(`CROSS_FILE_RULES` in `partition.py`): R111/R112 (need role/taskfile
inventory) and L006/L080 (need every consumer of a register or `set_fact`
rewritten). Node-local AI would rename the producer and leave readers on
the old name or the old result shape. These wait for project-level
remediation (ADR-027).

### Rule Metadata

Each rule across all validators declares tier-awareness in its metadata:

```python
@dataclass
class RuleMetadata:
    rule_id: str
    level: str              # "error", "warning", "info"
    fixable: bool           # True if a Tier 1 deterministic transform exists
    ai_proposable: bool     # True if the rule is a good candidate for AI fix
    description: str
```

- `fixable = True` → Tier 1 (transform registered, auto-applied)
- `fixable = False, ai_proposable = True` → Tier 2 (AI will attempt a patch)
- `fixable = False, ai_proposable = False` → Tier 3 (manual review only)

## Transform Registry

### Design

```python
from collections.abc import Callable
from ruamel.yaml.comments import CommentedMap

NodeTransformFn = Callable[[CommentedMap, dict], bool]

class TransformRegistry:
    """Maps rule IDs to node-level transform functions."""

    def __init__(self):
        self._node: dict[str, NodeTransformFn] = {}

    def register(self, rule_id: str, *, node: NodeTransformFn) -> None:
        self._node[rule_id] = node

    def __contains__(self, rule_id: str) -> bool:
        return rule_id in self._node

    def apply_node(self, rule_id: str, task: CommentedMap, violation: dict) -> bool:
        nfn = self._node.get(rule_id)
        if nfn is None:
            return False
        return nfn(task, violation)
```

### Transform Implementation Rules

1. **Operate on CommentedMap** — transforms receive a ruamel round-trip task mapping; modify in-place, return `True` if changed
2. **Single responsibility** — one transform per rule ID; a transform fixes exactly the issue its rule detects
3. **Idempotent** — applying a transform to already-fixed content produces no change
4. **Independently testable** — each transform has its own unit test with before/after YAML strings
5. **No side effects** — transforms modify the task CommentedMap in-place; they do not write files

### Example Transform

```python
def fix_missing_mode(task: CommentedMap, violation: dict) -> bool:
    """L021: add mode: '0644' to file/copy/template tasks missing explicit mode."""
    module_key = get_module_key(task)
    if module_key is None:
        return False

    module_args = task.get(module_key)
    if isinstance(module_args, dict) and "mode" not in module_args:
        module_args["mode"] = "0644"
        return True

    return False
```

### File Organization

```
src/apme_engine/remediation/
  ├── __init__.py
  ├── graph_engine.py          # GraphRemediationEngine (graph-aware convergence)
  ├── partition.py              # is_finding_resolvable(), classify_violation()
  ├── registry.py               # TransformRegistry (node transforms only)
  ├── ai_provider.py            # AIProvider protocol, AINodeFix, AINodeContext
  ├── ai_context.py             # AINodeContext builder from ContentGraph
  ├── abbenay_provider.py       # AbbenayProvider (Abbenay gRPC AI backend)
  └── transforms/
      ├── __init__.py            # auto-registers all transforms
      ├── _helpers.py            # Shared transform helpers
      ├── L007_shell_to_command.py
      ├── L021_missing_mode.py
      ├── M001_fqcn.py
      └── ...
```

## AI Escalation Path

When `is_finding_resolvable()` returns `False` and an AIProvider is available (via `--ai`), the remediation engine escalates to AI using **unit-level decomposition**.

### Unit Decomposition

AI remediation operates on individual **graph nodes** from the `ContentGraph`. Each node with unresolved violations (after Tier 1 deterministic transforms) is sent to the LLM as an `AINodeContext` containing the node's YAML, its violations, parent context, and best-practice guidance. This provides:

- **Focused context** — the LLM sees only the relevant task/block and its parent context, not the full file
- **Independent proposals** — each node fix is a separate `AINodeFix` the user can approve/reject via `approve_pending(source_filter="ai")`
- **No line-number dependency** — the LLM returns corrected YAML content, the graph engine handles state tracking

Violations on non-task nodes (e.g., play-level scope) are marked `MANUAL` for human review.

### Prompt Contract

The LLM receives the node's YAML and violations via `AINodeContext`. The prompt includes a **Rule-Specific Guidance** section built from optional `ai_prompt` frontmatter in rule doc `.md` files. This allows per-rule customization of how the AI handles specific violations (e.g., instructing it to add `# noqa` for false-positive-prone rules rather than modifying code). Hints are loaded lazily on first prompt build via `_load_ai_prompts()` and cached with `lru_cache` for subsequent calls. Rules without `ai_prompt` frontmatter use the default best-practices guidance.

The LLM returns an `AINodeFix`:

```json
{
  "fixed_snippet": "<the entire corrected YAML for this task/block>",
  "changes": [
    {"rule_id": "L024", "explanation": "Added task name", "confidence": 0.95}
  ],
  "skipped": [
    {"rule_id": "R101", "reason": "Cannot fix safely", "suggestion": "Review manually"}
  ]
}
```

### Graph-Native Application

AI fixes are applied via `node.update_from_yaml(fix.fixed_snippet)` on the `ContentNode`, which updates the node's content and marks it dirty. The graph engine records a `NodeState` with `source="ai"` and tracks content hashes to detect changes. After application, the graph is rescanned via `rescan_fn` to validate. AI proposals use the same approval semantics as deterministic transforms — they appear as pending proposals with `source="ai"` and can be selectively approved via `approve_pending(source_filter="ai")`.

### CLI Modes

| Flag | Behavior |
|------|----------|
| (default) | Tier 1 deterministic fixes only; Tier 2 violations reported as "AI-candidate" |
| `--ai` | Enable AI escalation; show proposed patch + diff, prompt user to accept/reject |
| `--ai --auto-approve` | Apply AI patches without prompting (CI mode) |

### Graceful Degradation

Without `--ai`, Tier 2 violations are reported as "AI-candidate" with a note about `--ai`. With `--ai`, AI escalation is only disabled up front when no Abbenay address can be resolved, no model is configured, or the `abbenay_grpc` client is unavailable, in which case no provider is created. If a provider is created but the Abbenay daemon is unreachable at runtime, node-level proposal attempts raise and are caught by the graph remediation engine, which skips those nodes. In that case, the affected violations remain unresolved / "AI-candidate" rather than being reclassified to Tier 3 (manual review).

## Convergence Loop

### Algorithm

```python
async def remediate(graph, violations, registry, max_passes=5):
    prev_count = float("inf")
    ai_proposals = []

    for pass_num in range(1, max_passes + 1):
        tier1, tier2, _ = partition_violations(violations, registry)

        # Phase A: Tier 1 deterministic transforms
        if tier1:
            applied = await _apply_tier1(graph, registry, tier1)

            if applied == 0:
                break

            violations = await rescan_fn(graph, graph.dirty_nodes)
            new_tier1, new_tier2, _ = partition_violations(violations, registry)
            new_fixable = len(new_tier1)

            if new_fixable >= prev_count:
                break  # oscillation

            prev_count = new_fixable
            if new_fixable > 0:
                continue

            tier1, tier2 = new_tier1, new_tier2

        # Phase B: Tier 2 AI transforms (when tier1 cleared)
        if not tier1 and tier2 and ai_provider is not None:
            ai_proposals.extend(await _apply_ai_transforms(graph, tier2))

            if graph.dirty_nodes:
                violations = await rescan_fn(graph, graph.dirty_nodes)

    graph.approve_pending(source_filter="deterministic")
    return GraphFixReport(...)
```

### Oscillation Detection

An oscillation occurs when a fix introduces a new violation that triggers another fix that re-introduces the original. Detection compares the **Tier-1-fixable** violation count (not total) after each pass. If the fixable count does not decrease, the loop stops. The `max_passes` parameter (default 5) provides a hard ceiling.

### Convergence Report

```python
@dataclass
class GraphFixReport:
    passes: int = 0
    fixed: int = 0
    applied_patches: list[FilePatch] = field(default_factory=list)
    remaining_violations: list[ViolationDict] = field(default_factory=list)
    fixed_violations: list[ViolationDict] = field(default_factory=list)
    oscillation_detected: bool = False
    nodes_modified: int = 0
    step_diffs: list[dict[str, object]] = field(default_factory=list)
    ai_proposals: list[AINodeProposal] = field(default_factory=list)
```

## Violation Ledger

### Why a Ledger, Not Derived Accounting

Earlier designs stored violations on `NodeState` snapshots and derived "fixed" counts by diffing successive snapshots. This was fragile:

- Double/triple-counting when multiple passes touched the same node
- AI fixes had no distinct accounting until user approval
- Co-fix attribution required set-difference heuristics

The **violation ledger** replaces all of this with a single source of truth: each violation has an explicit lifecycle tracked as a mutable `ViolationRecord` on the node.

### Data Model

```python
ViolationKey = tuple[str, str]  # (node_id, normalized_rule_id)

@dataclass
class ViolationRecord:
    key: ViolationKey
    violation: ViolationDict      # original validator payload
    status: str = "open"          # "open" | "fixed" | "proposed" | "declined"
    fixed_by: str | None = None   # "deterministic" | "ai"
    fixed_in_pass: int | None = None
    discovered_in_pass: int = 0
```

Each `ContentNode` has a `violation_ledger: dict[ViolationKey, ViolationRecord]`.

### Lifecycle States

```
open ──→ fixed      (deterministic transform, auto-approved)
  │
  └──→ proposed     (AI fix applied, pending human review)
          │
          ├──→ fixed    (user approved)
          │
          └──→ declined (user rejected, violation restored)
```

| State | Meaning | In `collect_violations()`? |
|-------|---------|---------------------------|
| `open` | Unresolved, present in file | Yes |
| `fixed` | Resolved (deterministic or approved AI) | No |
| `proposed` | AI fix applied but awaiting human review | No |
| `declined` | User rejected AI proposal, violation restored | No (queryable separately) |

### Graph API

| Method | Purpose |
|--------|---------|
| `register_violations(violations, pass_number)` | Insert new violations as `open`; re-confirm existing; reopen previously fixed |
| `resolve_violations(node_id, remaining_ids, *, fixed_by, pass_number, status)` | Transition open violations to `fixed` or `proposed` |
| `approve_proposed(node_id)` | Promote `proposed` → `fixed` |
| `decline_proposed(node_id)` | Transition `proposed` → `declined` (clears attribution) |
| `query_violations(*, status, fixed_by)` | Filter ledger entries across all nodes |
| `collect_violations()` | Shorthand for `query_violations(status="open")` |

### How It Integrates

1. **Initial scan** — `_record_violations()` calls `graph.register_violations()` (all violations enter as `open`)
2. **Tier 1 rescan** — `_rescan_and_record()` with `resolve_fixed_by="deterministic"` transitions absent violations to `fixed`
3. **AI rescan** — `_rescan_and_record()` with `resolve_fixed_by="ai", resolve_status="proposed"` transitions absent violations to `proposed`
4. **User approval** — `graph.approve_proposed(node_id)` promotes `proposed` → `fixed`
5. **User rejection** — `graph.decline_proposed(node_id)` transitions `proposed` → `declined`
6. **Report** — `GraphFixReport` counts come from `query_violations(status=...)` — no diffing

### NodeState Simplification

`NodeState` no longer carries `violations` or `violation_dicts`. It is a pure content snapshot (YAML text + content hash + metadata). Violation tracking is entirely the ledger's responsibility.

## Progress Streaming

`GraphRemediationEngine.remediate()` is an `async def` coroutine that runs as an `asyncio.create_task` alongside a queue drain loop. Without explicit progress plumbing, the gRPC stream (and downstream WebSocket) goes silent for the entire remediation duration — often minutes for large projects with AI escalation.

Three layers ensure continuous feedback:

### ProgressCallback

A `Callable[[str, str, float, int], None]` (`phase`, `message`, `fraction`, `level`) is threaded into `GraphRemediationEngine` and `_scan_pipeline`. Each component calls back at key milestones:

| Source | Phase | Example messages |
|--------|-------|-----------------|
| `_scan_pipeline` | `scan` | `Dispatching to 4 validators...`, `Gitleaks: 0 findings [...]` |
| `GraphRemediationEngine` | `graph-tier1` | `Pass 1/5: scanning...`, `Pass 1: 113 transforms applied` |
| `GraphRemediationEngine` | `graph-ai` | `AI attempt 1/2: 12 candidates` |

### Async Queue and Drain Loop

Because `remediate()` runs as a concurrent async task, progress is bridged through an async queue:

1. `_session_process` creates an `asyncio.Queue[ProgressUpdate | None]`.
2. The callback posts updates via `queue.put_nowait(update)`.
3. A drain loop (`while not remediate_task.done()`) polls the queue with a 1-second timeout and yields each `ProgressUpdate` as a `SessionEvent(progress=...)`.
4. After the task completes, remaining queued items are drained.

### Heartbeat

A concurrent `asyncio` task sends a generic `ProgressUpdate(phase="heartbeat", message="Processing...")` every 15 seconds. This fills gaps where neither the engine nor the scan pipeline emits application-level progress (e.g., during long ARI scans or venv setup), preventing WebSocket idle timeouts from browsers or reverse proxies.

## gRPC Contract

### `FixSession` RPC on Primary (ADR-028, ADR-039)

**Check** and **remediate** are user-facing actions; the engine uses **`FixSession`** internally for both (check mode without remediate options; remediate mode with `FixOptions`). The remediate pipeline uses **bidirectional streaming** (`FixSession`) for real-time progress, interactive proposal review, and session resume:

```protobuf
service Primary {
  rpc Format(FormatRequest) returns (FormatResponse);
  rpc FormatStream(stream ScanChunk) returns (FormatResponse);
  rpc Health(HealthRequest) returns (HealthResponse);
  rpc FixSession(stream SessionCommand) returns (stream SessionEvent);  // ADR-028, ADR-039
  rpc ListAIModels(ListAIModelsRequest) returns (ListAIModelsResponse);
  // Scan and ScanStream removed (ADR-039). FixSession carries ScanChunk uploads for check and remediate.
}

message FixOptions {
  int32 max_passes = 1;
  string ansible_core_version = 2;
  repeated string collection_specs = 3;
  repeated string exclude_patterns = 4;
  bool enable_ai = 5;               // opt-in AI escalation
  bool enable_agentic = 6;          // Tier 3 (future)
  string ai_model = 7;
  string session_id = 8;
  repeated GalaxyServerDef galaxy_servers = 9;
}

// Client -> Server: upload chunks, then approval/extend/close commands
message SessionCommand {
  oneof command {
    ScanChunk upload = 1;
    ApprovalRequest approve = 2;
    ExtendRequest extend = 3;
    CloseRequest close = 4;
    ResumeRequest resume = 5;
  }
}

// Server -> Client: progress, tier1 summary, proposals, result
message SessionEvent {
  oneof event {
    SessionCreated created = 1;
    ProgressUpdate progress = 2;
    Tier1Summary tier1_complete = 3;
    ProposalsReady proposals = 4;
    ApprovalAck approval_ack = 5;
    SessionResult result = 6;
    ExpirationWarning expiring = 7;
    SessionClosed closed = 8;
    DataPayload data = 9;
  }
}
```

### AI Escalation

AI escalation uses the `AIProvider` protocol (ADR-025) with `AbbenayProvider` as the default implementation. The Abbenay daemon is an external process/container that manages LLM providers and API keys via gRPC. See [DESIGN_AI_ESCALATION.md](DESIGN_AI_ESCALATION.md) for the full design.

## Container Topology (with Remediation)

```
┌────────────────────────────── apme-pod ──────────────────────────────────┐
│                                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │ Primary  │  │  Native  │  │   OPA    │  │ Ansible  │  │ Gitleaks │  │
│  │  :50051  │  │  :50055  │  │  :50054  │  │  :50053  │  │  :50056  │  │
│  │          │  │          │  │          │  │          │  │          │  │
│  │ engine + │  │ Python   │  │ OPA bin  │  │ ansible- │  │ gitleaks │  │
│  │ orchestr │  │ rules on │  │ + gRPC   │  │ core     │  │ + gRPC   │  │
│  │ remediat │  │ scandata │  │ wrapper  │  │ venvs    │  │ wrapper  │  │
│  └────┬─────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘  │
│       │                                                                  │
│       │ gRPC (optional, only when --ai)                                  │
│       ▼                                                                  │
│  ┌──────────┐                                                            │
│  │ Abbenay  │  AI daemon — manages LLM providers                        │
│  │  :50057  │  GHCR image or binary                                      │
│  └──────────┘                                                            │
│                                                                          │
│  ┌──────────────────────────────────────────┐                            │
│  │       Galaxy Proxy :8765 (PEP 503)       │                            │
│  └──────────────────────────────────────────┘                            │
└──────────────────────────────────────────────────────────────────────────┘
```

The remediation engine lives inside Primary. It reuses Primary's existing scan pipeline and adds the transform → re-check convergence loop. AI escalation is a gRPC call to the optional Abbenay daemon via the `AIProvider` protocol (ADR-025). See [DESIGN_AI_ESCALATION.md](DESIGN_AI_ESCALATION.md) for the full AI integration design.

## CLI Integration

### `apme remediate`

```
apme remediate [target] [options]

Options:
  --apply              Write fixes in place (without this, show diffs only)
  --check              Exit 1 if any fixes would be applied (CI mode)
  --ai                 Enable Tier 2 AI-assisted remediation (opt-in)
  --auto-approve       Approve all AI proposals without prompting (CI mode)
  --max-passes N       Max convergence passes (default: 5)
  --exclude PATTERN    Glob patterns to skip (parsed but not yet wired through to the engine)
  --ansible-version V  ansible-core version for validation
  --collections SPEC   Collection specs to make available
  --json               Output structured data payloads as JSON
```

### Output

```
Phase 1: Formatting... 3 file(s) reformatted
Phase 2: Idempotency check... Passed
Phase 3: Checking... 42 violation(s)
Phase 4: Remediating...
  Pass 1: 28 fixable (Tier 1) → applied 26, 2 failed
  Pass 2: 4 fixable (Tier 1) → applied 4
  Pass 3: 0 fixable → converged
Phase 5: AI escalation (Tier 2)... 10 candidates (skipped: --ai not set)
Phase 6: Summary
  Tier 1 (deterministic):  30 fixed
  Tier 2 (AI-proposable):  10 remaining → 8 proposals generated
  Tier 3 (manual review):   2 (policy/judgment required)
  Passes:    3
```

## Implementation Order

1. **Transform Registry + partition** — the data structures and registry pattern (done)
2. **First transforms** — L021, L007, M001, M006, M008, M009, L046, and more (done — 20+ transforms)
3. **Convergence loop** — check (internal scan) → transform → re-check loop with oscillation detection (done)
4. **`FixSession` RPC** — bidirectional streaming gRPC contract on Primary (done — ADR-028)
5. **CLI `remediate` integration** — interactive review, `--apply`, `--check`, `--auto-approve` (done)
6. **AI escalation** — `AIProvider` protocol + `AbbenayProvider` + graph-native AI convergence (done — Phase 3)
7. **Web UI remediation queue** — accept/reject AI proposals (Phase 4)
