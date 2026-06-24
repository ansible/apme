# Remediation Engine Design

The provided text outlines the architecture of a remediation engine, a specialised system designed to automatically repair configuration errors by transforming scan results into file patches. Unlike a standard formatter that merely organises code style, this engine follows a violation-driven pipeline that uses specific rules to identify and resolve functional issues. The system organises these repairs into a three-tier classification model, ranging from deterministic transforms that are applied automatically to AI-proposable fixes requiring review and manual interventions for complex architectural decisions. By decoupling the formatting pre-pass from the fix logic, the design ensures a clean, idempotent workflow where changes are verified through a convergence loop to prevent new errors. Ultimately, this framework establishes a robust, automated repair strategy that integrates human oversight with machine learning to maintain high-quality infrastructure code.

---

## Overview

The remediation engine is a separate service that consumes scan violations and produces file patches. It is **not** the formatter. The formatter is a blind pre-pass that normalizes YAML style; the remediation engine is a violation-driven transform pipeline that fixes detected issues.

```
┌──────────┐     ┌─────────────┐     ┌──────────┐     ┌──────────────────┐     ┌──────────┐
│ Formatter│ ──► │ Idempotency │ ──► │  Scan    │ ──► │  Remediation    │ ──► │ Re-scan  │
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

---

## Why the Formatter Is Not Part of the Remediation Engine

| Aspect | Formatter (Phase 1) | Remediation Engine (Phase 2+) |
|--------|---------------------|-------------------------------|
| **Input** | Raw YAML text | Violations from a scan |
| **Trigger** | Always runs (blind pre-pass) | Only runs when violations exist |
| **Logic** | Fixed transforms: tabs, indentation, key order, Jinja spacing | Rule-specific transforms + AI escalation |
| **Needs a scan?** | No | Yes |
| **Goal** | Canonical formatting so downstream diffs are clean | Fix detected issues |

Routing the formatter through the remediation engine would require:
- Running a scan before formatting (to produce violations for the engine to consume)
- Inventing artificial "formatting violation" rules that don't exist today
- Creating a circular dependency: format needs scan, scan assumes formatted input

The formatter is a **pre-condition** for the remediation engine. `apme format` works without any scan infrastructure — fast, standalone, no containers needed.

---

## Fix Pipeline

The `apme remediate` command orchestrates the full pipeline:

```
Phase 1: Format
  └─► format all YAML files (tabs, indentation, key order, Jinja spacing)
  └─► write changes if --apply

Phase 2: Idempotency Gate
  └─► format again
  └─► assert zero diffs (if not: formatter bug, abort)

Phase 3: Engine scan (validators)
  └─► run engine (load → build_content_graph → apply_rules → hierarchy)
  └─► fan out to all validators (Native, OPA, Ansible, Gitleaks, Coll Health, Dep Audit)
  └─► merge + deduplicate violations

Phase 4: Remediate (Tier 1 — deterministic)
  └─► partition violations via is_finding_resolvable()
  └─► apply Tier 1 transforms from the Transform Registry
  └─► re-scan → repeat until converged or oscillation (max --max-passes)

Phase 5: AI Escalation (Tier 2 — AI-proposable)
  └─► route Tier 2 violations to Abbenay AI (if available)
  └─► generate patches with confidence scores
  └─► apply accepted patches (--auto) or present for review

Phase 6: Report
  └─► summary: Tier 1 fixed, Tier 2 proposals, Tier 3 manual review
```

---

## Where Each Component Lives

| Component | Location | Why |
|-----------|----------|-----|
| Formatter | CLI (in-process) or Primary (`Format` RPC) | No scan needed; operates on raw files |
| Scan | Primary service (gRPC) or CLI (in-process) | Already implemented |
| Remediation Engine | Primary service (`FixSession` RPC) | Needs access to scan results and file content; can call Abbenay |
| Transform Registry | `src/apme_engine/remediation/transforms/` | Pure functions, no container needed |
| AI Escalation | Abbenay daemon (gRPC, :50057) | Separate container, optional |

---

## Three-Tier Finding Classification

Every violation flows through a three-tier classification that determines how it is handled:

| Tier | Label | Handler | Confidence | User Action |
|------|-------|---------|------------|-------------|
| **1 — Deterministic** | `fixable: true` | Transform Registry | 100% — the transform is a known-correct rewrite | None (auto-applied) |
| **2 — AI-Proposable** | `ai_proposable: true` | Abbenay gRPC | Variable — LLM generates a patch with a confidence score | Review proposal, accept/reject (or `--auto` in CI) |
| **3 — Manual Review** | neither | Human | N/A — requires judgment, policy, or external context | Fix by hand |

### Tier 1: Deterministic Fixes (Transform Registry)

These are mechanical rewrites where the correct output is unambiguous given the input and the rule definition. Examples:

| Rule | Transform |
|------|-----------|
| L021 | Add `mode: '0644'` to file/copy/template tasks missing an explicit mode |
| L007 | Replace `ansible.builtin.shell` with `ansible.builtin.command` when no shell features are used |
| M001 | Rewrite short module names to FQCN (`debug` → `ansible.builtin.debug`) |
| M005 | Rename deprecated parameter (`sudo:` → `become:`) |

The transform function receives the node's `CommentedMap` (ruamel round-trip YAML) and the violation, mutates it in-place, and returns `True`. No ambiguity, no judgment.

### Tier 2: AI-Proposable Fixes (Abbenay)

These violations have a clear "what needs to change" but the "how" requires understanding context that a static transform cannot capture. The AI generates a patch and attaches a confidence score. Examples:

| Rule | Why AI |
|------|--------|
| R118 | Restructure complex Jinja2 logic in `when:` clauses (many valid refactorings) |
| M003 | Rewrite tasks using removed modules to use their replacement (may require restructuring parameters) |
| SEC:* | Replace hardcoded secrets with vault lookups (AI can infer the variable name from context) |
| L030 | Extract complex `ansible.builtin.shell` one-liners into scripts (requires understanding intent) |

AI proposals are **never auto-applied by default**. The user reviews the diff and accepts or rejects. `--auto --min-confidence 0.8` enables unattended mode for CI.

### Tier 3: Manual Review

A small residual category where the "right answer" depends on organizational policy, external systems, or human judgment that neither a transform nor an AI can resolve with confidence. Examples:

- Which vault path to store a rotated secret in
- Whether to split a 500-line playbook into roles (architectural decision)
- Which trusted Galaxy source to use for a dependency

These are reported as "manual review required" with the rule message and context. The remediation engine does not attempt a fix.

### Why Three Tiers, Not Two

A binary "fixable / not fixable" misrepresents the AI capability. Many violations *can* be fixed by AI with high confidence — they are not "manual review" in any meaningful sense. The three-tier model:

- Gives users a clear expectation: Tier 1 is always safe, Tier 2 needs a glance, Tier 3 needs thought.
- Lets CI pipelines opt in to AI fixes above a confidence threshold (`--auto --min-confidence N`).
- Keeps the `fixable` flag honest — it means "deterministically correct, zero risk of wrong output."

---

## Finding Partition

### `is_finding_resolvable()`

The partition function routes violations into Tier 1 vs. Tier 2+3:

```python
def is_finding_resolvable(violation: dict, registry: TransformRegistry) -> bool:
    """Return True if the violation has a registered deterministic transform (Tier 1)."""
    return violation.get("rule_id", "") in registry
```

This is intentionally simple. A violation is resolvable if and only if the transform registry has a function for that rule ID. No heuristics, no guessing.

Violations that fail this check proceed to AI escalation (Tier 2) if Abbenay is available, otherwise they are reported as manual review (Tier 3).

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

---

## Transform Registry

### Design

Transforms operate on **graph nodes**, not raw file content. Each transform receives a `CommentedMap` (the ruamel.yaml round-trip representation of a single task/play) and a violation dict. It mutates the map in-place and returns `True` if a change was made. The engine handles serialization back to disk.

```python
from collections.abc import Callable
from ruamel.yaml.comments import CommentedMap
from apme_engine.engine.models import ViolationDict

NodeTransformFn = Callable[[CommentedMap, ViolationDict], bool]
# NodeTransformFn(task_data: CommentedMap, violation: ViolationDict) -> applied: bool


class TransformRegistry:
    """Maps rule IDs to node-level transform functions."""

    def __init__(self) -> None:
        self._node: dict[str, NodeTransformFn] = {}

    def register(self, rule_id: str, *, node: NodeTransformFn | None = None) -> None:
        if node is None:
            raise ValueError(f"register({rule_id!r}): node transform is required")
        self._node[rule_id] = node

    def get_node_transform(self, rule_id: str) -> NodeTransformFn | None:
        return self._node.get(rule_id)

    def __contains__(self, rule_id: str) -> bool:
        return rule_id in self._node
```

### Transform Implementation Rules

| Rule | Description |
|------|-------------|
| **Operate on CommentedMap** | Transforms receive a ruamel round-trip `CommentedMap` — comments and formatting are preserved automatically |
| **Mutate in-place** | Modify the map directly and return `True`; do not rebuild or re-serialize |
| **Single responsibility** | One transform per rule ID; a transform fixes exactly the issue its rule detects |
| **Idempotent** | Applying a transform to already-fixed content returns `False` (no change) |
| **Independently testable** | Each transform has its own unit test with before/after YAML strings |
| **No file I/O** | Transforms never read or write files; they operate on the in-memory node representation |

### Example Transform

```python
def fix_missing_mode(task: CommentedMap, violation: ViolationDict) -> bool:
    """L021: add mode: '0644' to file/copy/template tasks missing explicit mode."""
    module_key = _get_module_key(task)
    if module_key is None:
        return False

    module_opts = task.get(module_key)
    if not isinstance(module_opts, dict):
        return False

    if "mode" in module_opts:
        return False

    module_opts["mode"] = "0644"
    return True
```

### File Organization

```
src/apme_engine/remediation/
  ├── __init__.py
  ├── graph_engine.py        # Graph-based remediation engine (convergence loop)
  ├── partition.py           # is_finding_resolvable()
  ├── registry.py            # TransformRegistry
  ├── ai_provider.py         # AIProvider protocol
  ├── ai_context.py          # Context builder for AI requests
  ├── abbenay_provider.py    # Abbenay gRPC client (implements AIProvider)
  └── transforms/
      ├── __init__.py        # auto-registers all transforms
      ├── _helpers.py        # shared transform utilities
      ├── L007_shell_to_command.py
      ├── L008_local_action.py
      ├── L009_empty_string.py
      ├── M001_fqcn.py
      └── ... (~19 transform modules)
```

---

## AI Escalation Path

When `is_finding_resolvable()` returns `False` and the Abbenay AI service is available, the remediation engine escalates to AI.

### Request Packaging

Violations are grouped by graph node and packaged with graph-derived context for the LLM:

```python
@dataclass(frozen=True, slots=True)
class AINodeContext:
    node_id: str                     # graph node identifier
    node_type: str                   # task, block, handler, etc.
    yaml_lines: str                  # current YAML text for this node
    violations: list[ViolationDict]  # all violations on this node
    file_path: str                   # source file (display only)
    parent_context: str              # summarized ancestor chain (play vars, become, tags)
    sibling_snippets: list[str]      # YAML of surrounding siblings for awareness
    feedback: str                    # validation feedback from prior failed AI attempt
```

This is node-scoped, not file-scoped — the AI fixes one graph node at a time with rich structural context from the `ContentGraph`.

### Structured Prompt (Node-Level)

The prompt is structured as `NODE_PROMPT_TEMPLATE` and includes:

1. **Violations list** — all violations on this node (rule_id, message)
2. **Rule-specific guidance** — loaded from rule doc frontmatter `ai_prompt` field
3. **YAML to fix** — the node's current `yaml_lines`
4. **Parent context** — play vars, become, tags from ancestor nodes
5. **Sibling context** — surrounding sibling node YAML for awareness
6. **Best practices** — curated per-rule Ansible best practices
7. **Feedback** (resubmission only) — why the prior attempt was rejected

The AI must respond with structured JSON: `fixed_snippet` (complete corrected YAML), `changes[]` (per-violation rule_id + explanation + confidence), and `skipped[]` (violations it couldn't fix). This enables per-violation tracking and confidence scoring.

### Response Schema

```python
@dataclass
class AINodeFix:
    fixed_snippet: str          # corrected YAML text for the node
    rule_ids: list[str]         # rule IDs addressed by this fix
    explanation: str            # human-readable summary of changes
    confidence: float           # 0.0-1.0 (default 0.85)
    skipped: list[AISkipped]    # violations the AI could not fix
```

The `AIProvider` protocol's sole entry point is `propose_node_fix(context: AINodeContext) -> AINodeFix | None`. Returns `None` when the AI cannot produce a fix.

### CLI Modes

| Flag | Behavior |
|------|----------|
| (default) | AI disabled; only Tier 1 deterministic transforms run |
| `--ai` | Enable Tier 2 AI-assisted remediation |
| `--auto-approve` | Apply AI patches without prompting (CI mode) |
| `--model MODEL` | AI model identifier (e.g. `openai/gpt-4o`; falls back to `APME_AI_MODEL` env var) |

### Graceful Degradation

If `APME_ABBENAY_ADDR` is unset or the service is unreachable, the remediation engine skips AI escalation silently. Non-fixable violations are reported as "manual review required." The fix pipeline never fails due to missing AI.

---

## Convergence Loop

### Algorithm

The convergence loop operates entirely **in memory on the `ContentGraph`** — no files are read or written during convergence. After convergence, `splice_modifications()` produces file patches.

```python
class GraphRemediationEngine:
    def __init__(self, registry, graph, rules, *, max_passes=5, ai_provider=None): ...

    async def remediate(self, initial_violations=None) -> GraphFixReport:
        """Unified Tier 1 + Tier 2 convergence loop."""
        if initial_violations is None:
            violations = scan(graph, rules)  # initial full graph scan
        else:
            violations = initial_violations

        for pass_num in range(1, max_passes + 1):
            tier1, tier2, tier3 = partition_violations(violations, registry)

            # Phase A: Tier 1 — deterministic node transforms
            if tier1:
                for v in tier1:
                    node = graph.get_node(v["node_id"])
                    transform = registry.get_node_transform(v["rule_id"])
                    if transform and transform(node.commented_map, v):
                        graph.mark_dirty(node.id)

            # Rescan only dirty nodes (incremental)
            violations = rescan_dirty(graph, dirty_node_ids, rules)

            if not violations or count >= prev_count:
                break  # converged or oscillation

            # Phase B: Tier 2 — AI transforms (when Tier 1 exhausts)
            if not tier1 and tier2 and ai_provider:
                proposals = await ai_provider.propose(tier2, graph)
                # Apply accepted proposals, rescan, loop back to Tier 1

        return GraphFixReport(passes, fixed, remaining, ai_proposals, ...)
```

Key differences from a file-based engine:
- **No file I/O during convergence** — all mutations happen on `ContentNode.yaml_lines` in memory
- **Incremental rescan** — only dirty (modified) nodes are re-evaluated, not the entire project
- **Unified Tier 1 + Tier 2 loop** — AI proposals are applied to the same graph; post-AI rescanning catches cross-tier issues and Tier 1 cleanup runs automatically
- **Transaction safety** — a transform that fails mid-execution leaves the graph unchanged

### Oscillation Detection

An oscillation occurs when a fix introduces a new violation that triggers another fix that re-introduces the original. Detection: if the violation count does not decrease after a pass, stop. The `max_passes` parameter (default 5) provides a hard ceiling.

### Convergence Report

```python
@dataclass
class GraphFixReport:
    passes: int                                # convergence passes executed
    fixed: int                                 # violations resolved
    applied_patches: list[FilePatch]           # populated by splice_modifications()
    remaining_violations: list[ViolationDict]  # open + ai_abstained
    fixed_violations: list[ViolationDict]      # resolved during convergence
    ai_abstained_violations: list[ViolationDict]  # AI attempted but failed
    oscillation_detected: bool                 # True if loop bailed
    nodes_modified: int                        # ContentNodes mutated
    ai_proposals: list[AINodeProposal]         # pending human approval

@dataclass
class FilePatch:
    path: str           # file that was patched
    original: str       # original content
    patched: str        # content after transforms
    diff: str           # unified diff
    rule_ids: list[str] # applied rule IDs

@dataclass
class AINodeProposal:
    node_id: str        # graph node modified by AI
    file_path: str      # source file (for display)
    before_yaml: str    # YAML before AI transform
    after_yaml: str     # YAML after AI transform
    rule_ids: list[str] # addressed rule IDs
    explanation: str    # human-readable summary
    confidence: float   # AI confidence score
```

---

## gRPC Contract

User-facing **check** and **remediate** both run through **`FixSession`** (bidirectional stream, ADR-039). There is no separate unary Fix or Scan RPC — `FixSession` is the sole client path.

### Primary Service (Implemented)

```protobuf
service Primary {
  rpc Format(FormatRequest) returns (FormatResponse);
  rpc FormatStream(stream ScanChunk) returns (FormatResponse);
  rpc Health(HealthRequest) returns (HealthResponse);
  rpc FixSession(stream SessionCommand) returns (stream SessionEvent);
  rpc ListAIModels(ListAIModelsRequest) returns (ListAIModelsResponse);
}
```

`FixSession` uses `SessionCommand` messages (containing `ScanChunk` file uploads, `FixOptions`, approval/rejection commands) and streams back `SessionEvent` messages (progress, violations, patches, AI proposals, completion).

### Abbenay AI Service

```protobuf
service Abbenay {
  rpc Remediate(RemediateRequest) returns (RemediateResponse);
  rpc Health(HealthRequest) returns (HealthResponse);
}
```

The Abbenay service receives violation context (rule_id, message, node content, surrounding context) and returns suggested YAML + confidence + reasoning.

---

## Container Topology (with Remediation)

```
┌──────────────────────────────── apme-pod ────────────────────────────────────┐
│                                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │ Primary  │  │  Native  │  │   OPA    │  │ Ansible  │  │ Gitleaks │      │
│  │  :50051  │  │  :50055  │  │  :50054  │  │  :50053  │  │  :50056  │      │
│  │          │  │          │  │          │  │          │  │          │      │
│  │ engine + │  │ GraphRule │  │ OPA bin  │  │ ansible- │  │ gitleaks │      │
│  │ orchestr │  │ rules on │  │ + gRPC   │  │ core     │  │ + gRPC   │      │
│  │ remediat │  │ graph    │  │ wrapper  │  │ venvs    │  │ wrapper  │      │
│  └────┬─────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘      │
│       │                                                                      │
│       │         ┌──────────────┐  ┌──────────────┐                           │
│       │         │ Coll Health  │  │  Dep Audit   │                           │
│       │         │    :50058    │  │    :50059    │                           │
│       │         └──────────────┘  └──────────────┘                           │
│       │                                                                      │
│       │ gRPC (optional)                                                      │
│       ▼                                                                      │
│  ┌──────────┐                                                                │
│  │ Abbenay  │  AI escalation — LLM provider for Tier 2 remediation           │
│  │  :50057  │                                                                │
│  └──────────┘                                                                │
│                                                                              │
│  ┌──────────────────────────────────────────┐                                │
│  │       Galaxy Proxy :8765 (PEP 503)       │                                │
│  └──────────────────────────────────────────┘                                │
└──────────────────────────────────────────────────────────────────────────────┘
```

The remediation engine lives inside **Primary**. It operates on the same `ContentGraph` built by the scan pipeline and runs the transform → rescan convergence loop entirely in memory. AI escalation is a gRPC call to the optional Abbenay container.

---

## CLI Integration

### `apme remediate`

```
apme remediate [target] [options]

Options:
  --max-passes N           Max convergence passes (default: 5)
  --ansible-version VER    ansible-core version for validation (e.g. 2.18, 2.20)
  --collections SPEC...    Collection specs (e.g. community.general:9.0.0)
  --ai                     Enable Tier 2 AI-assisted remediation
  --auto-approve           Approve all AI proposals without prompting (CI mode)
  --model MODEL            AI model identifier (e.g. 'openai/gpt-4o')
  --session ID             Session ID for venv reuse (default: hash of project root)
  --skip-dep-scan          Disable both dependency validators
  --skip-collection-scan   Disable collection health scanning only
  --skip-python-audit      Disable Python CVE audit only
  --show-suppressed        Include suppressed violations in output (ADR-055)
  --json                   Output structured data payloads as JSON
```

### Output

```
Phase 1: Formatting... 3 file(s) reformatted
Phase 2: Idempotency check... Passed
Phase 3: Engine scan... 42 violation(s)
Phase 4: Remediating...
  Pass 1: 28 fixable (Tier 1) → applied 26, 2 failed
  Pass 2: 4 fixable (Tier 1) → applied 4
  Pass 3: 0 fixable → converged
Phase 5: AI escalation (Tier 2)... 10 candidates → 8 proposals
Phase 6: Summary
  Tier 1 (deterministic):  30 fixed
  Tier 2 (AI-proposable):  10 remaining → 8 proposals generated
  Tier 3 (manual review):   2 (policy/judgment required)
  Passes:    3
```

---

## Implementation Order

1. **Transform Registry + partition** — the data structures and registry pattern
2. **First transforms** — L021 (missing mode), L007 (shell→command), M001 (FQCN) as proof-of-concept
3. **Graph-based convergence loop** — ContentGraph → transform → rescan-dirty loop with oscillation detection
4. **FixSession integration** — wire the remediation path through `FixSession` bidirectional stream (ADR-039)
5. **CLI remediate integration** — `apme remediate` → daemon → `FixSession` with fix options
6. **AI escalation** — Abbenay gRPC client + prompt builder
7. **Web UI remediation queue** — accept/reject AI proposals via Gateway

---

## Related Documents

- [ADR-009: Remediation Engine](/.sdlc/adrs/ADR-009-remediation-engine.md) — Architectural decision for separate remediation
- [rule-catalog.md](rule-catalog.md) — All 93 rules with fixer status
- [design-validators.md](design-validators.md) — Validator abstraction (scan pipeline)
- [architecture.md](architecture.md) — Container topology
