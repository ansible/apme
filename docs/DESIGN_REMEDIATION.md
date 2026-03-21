# Remediation Engine Design

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

The formatter is a **pre-condition** for the remediation engine. `apme-scan format` works without any scan infrastructure — fast, standalone, no containers needed.

## Fix Pipeline

The `apme-scan fix` command orchestrates the full pipeline:

```
Phase 1: Format
  └─► format all YAML files (tabs, indentation, key order, Jinja spacing)
  └─► write changes if --apply

Phase 2: Idempotency Gate
  └─► format again
  └─► assert zero diffs (if not: formatter bug, abort)

Phase 3: Scan
  └─► run engine (parse → annotate → hierarchy)
  └─► fan out to all validators (Native, OPA, Ansible, Gitleaks)
  └─► merge + deduplicate violations

Phase 4: Remediate (Tier 1 — deterministic)
  └─► partition violations via is_finding_resolvable()
  └─► apply Tier 1 transforms from the Transform Registry
  └─► re-scan → repeat until converged or oscillation (max --max-passes)

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
| Scan | Primary service (gRPC) or CLI (in-process) | Already implemented |
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
- **M001** — rewrite short module names to FQCN using `resolved_fqcn` from ansible-core introspection or OPA L005; escalates to AI (Tier 2) when no FQCN can be resolved
- **M005** — rename deprecated parameter (`sudo:` → `become:`)

The transform function receives the file content and violation, returns the corrected content. No ambiguity, no judgment.

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
from typing import Callable, NamedTuple

class TransformResult(NamedTuple):
    content: str        # modified file content
    applied: bool       # True if a change was made

TransformFn = Callable[[str, dict], TransformResult]
# TransformFn(file_content: str, violation: dict) -> TransformResult


class TransformRegistry:
    """Maps rule IDs to deterministic fix functions."""

    def __init__(self):
        self._transforms: dict[str, TransformFn] = {}

    def register(self, rule_id: str, fn: TransformFn) -> None:
        self._transforms[rule_id] = fn

    def __contains__(self, rule_id: str) -> bool:
        return rule_id in self._transforms

    def apply(self, rule_id: str, content: str, violation: dict) -> TransformResult:
        fn = self._transforms.get(rule_id)
        if fn is None:
            return TransformResult(content=content, applied=False)
        return fn(content, violation)
```

### Transform Implementation Rules

1. **Operate on YAML AST** — use `FormattedYAML` (ruamel round-trip) to preserve comments and formatting
2. **Single responsibility** — one transform per rule ID; a transform fixes exactly the issue its rule detects
3. **Idempotent** — applying a transform to already-fixed content produces no change
4. **Independently testable** — each transform has its own unit test with before/after YAML strings
5. **No side effects** — transforms receive content + violation, return content; they do not write files

### Example Transform

```python
def fix_missing_mode(content: str, violation: dict) -> TransformResult:
    """L021: add mode: '0644' to file/copy/template tasks missing explicit mode."""
    yaml = FormattedYAML(typ="rt", pure=True, version=(1, 1))
    data = yaml.load(content)

    # Navigate to the task identified by the violation
    task = _find_task_at_line(data, violation.get("line", 0))
    if task is None:
        return TransformResult(content=content, applied=False)

    module_key = _get_module_key(task)
    if module_key and "mode" not in task[module_key]:
        task[module_key]["mode"] = "0644"
        return TransformResult(content=yaml.dumps(data), applied=True)

    return TransformResult(content=content, applied=False)
```

### File Organization

```
src/apme_engine/remediation/
  ├── __init__.py
  ├── engine.py              # RemediationEngine class (convergence loop)
  ├── partition.py            # is_finding_resolvable(), classify_violation()
  ├── registry.py             # TransformRegistry
  ├── ai_provider.py          # AIProvider Protocol, AIProposal dataclass
  ├── abbenay_provider.py     # AbbenayProvider (default AI impl via abbenay_grpc)
  ├── enrich.py               # Enrich violations/context for remediation
  ├── structured.py           # Structured remediation payloads
  ├── unit_segmenter.py       # Split content into task snippets for AI
  └── transforms/
      ├── __init__.py          # auto-registers all transforms
      ├── _helpers.py          # Shared transform helpers
      ├── L007_shell_to_command.py
      ├── L021_missing_mode.py
      ├── M001_fqcn.py
      └── ...
```

## AI Escalation Path

When `is_finding_resolvable()` returns `False` and an AIProvider is available (via `--ai`), the remediation engine escalates to AI.

### Request Packaging

Each violation is packaged with context for the LLM:

```python
@dataclass
class AIRemediationRequest:
    rule_id: str
    level: str
    message: str
    file_path: str
    line: int
    code_window: str        # 10 lines before + after the violation
    file_content: str       # full file (for broader context)
    ansible_version: str    # target version (e.g., "2.20")
```

### Structured Prompt

```
You are an Ansible modernization assistant. A static analysis rule has
flagged an issue in the following YAML file.

Rule: {rule_id}
Message: {message}
File: {file_path}
Line: {line}

Code context (lines {start}-{end}):
```yaml
{code_window}
```

Provide a fix for this issue. Respond with ONLY valid YAML for the
corrected section. Preserve comments and formatting. If you cannot
fix it with confidence, respond with "SKIP".
```

### Response Schema

```python
@dataclass
class AIRemediationResponse:
    suggested_code: str     # corrected YAML (or "SKIP")
    confidence: float       # 0.0-1.0
    reasoning: str          # why this fix is correct
    applicable: bool        # False if LLM says "SKIP"
```

### CLI Modes

| Flag | Behavior |
|------|----------|
| (default) | Tier 1 deterministic fixes only; Tier 2 violations reported as "AI-candidate" |
| `--ai` | Enable AI escalation; show proposed patch + diff, prompt user to accept/reject |
| `--ai --auto-approve` | Apply AI patches without prompting (CI mode) |

### Graceful Degradation

Without `--ai`, Tier 2 violations are reported as "AI-candidate" with a note about `--ai`. With `--ai`, if the Abbenay daemon is unreachable, the CLI exits with a clear error — the user explicitly opted in and deserves an explicit failure.

## Convergence Loop

### Algorithm

```python
def remediate(files, max_passes=5):
    prev_count = float("inf")

    for pass_num in range(1, max_passes + 1):
        violations = scan(files)
        fixable = [v for v in violations if is_finding_resolvable(v, registry)]

        if not fixable:
            break  # nothing left to fix deterministically

        for v in fixable:
            content = read_file(v["file"])
            result = registry.apply(v["rule_id"], content, v)
            if result.applied:
                write_file(v["file"], result.content)

        # Re-scan to check progress
        new_violations = scan(files)
        new_count = len(new_violations)

        if new_count >= prev_count:
            # Oscillation or no progress — bail
            break

        prev_count = new_count

        if new_count == 0:
            break  # fully converged

    # After deterministic passes (Tier 1), partition remaining into Tier 2 + 3
    remaining = scan(files)
    tier2 = [v for v in remaining
             if not is_finding_resolvable(v, registry) and v.get("ai_proposable", True)]
    tier3 = [v for v in remaining
             if not is_finding_resolvable(v, registry) and not v.get("ai_proposable", True)]

    ai_results = escalate_to_ai(tier2)  # no-op if AI unavailable

    return FixReport(
        passes=pass_num,
        fixed=prev_initial_count - len(remaining),
        remaining_ai=tier2,         # Tier 2: AI-proposed patches
        remaining_manual=tier3,     # Tier 3: manual review only
        ai_proposed=ai_results,
    )
```

### Oscillation Detection

An oscillation occurs when a fix introduces a new violation that triggers another fix that re-introduces the original. Detection is simple: if the violation count does not decrease after a pass, stop. The `max_passes` parameter (default 5) provides a hard ceiling.

### Convergence Report

```python
@dataclass
class FixReport:
    passes: int                     # number of convergence passes executed
    fixed: int                      # violations resolved by Tier 1 transforms
    remaining_ai: list[dict]        # Tier 2: violations with AI proposals
    remaining_manual: list[dict]    # Tier 3: violations requiring manual review
    ai_proposed: list[dict]         # AI-suggested patches (pending review)
    oscillation_detected: bool      # True if loop bailed due to no progress
```

## gRPC Contract

### `FixSession` RPC on Primary (ADR-028)

The fix pipeline uses **bidirectional streaming** (`FixSession`) for real-time progress, interactive proposal review, and session resume:

```protobuf
service Primary {
  rpc Scan(ScanRequest) returns (ScanResponse);
  rpc ScanStream(stream ScanChunk) returns (ScanResponse);
  rpc Format(FormatRequest) returns (FormatResponse);
  rpc FormatStream(stream ScanChunk) returns (FormatResponse);
  rpc FixSession(stream SessionCommand) returns (stream SessionEvent);
  rpc Health(HealthRequest) returns (HealthResponse);
  // Cache proxy RPCs (PullGalaxy, PullRequirements, CloneOrg)
}

message FixOptions {
  int32 max_passes = 1;
  string ansible_core_version = 2;
  repeated string collection_specs = 3;
  repeated string exclude_patterns = 4;
  bool enable_ai = 5;               // opt-in AI escalation
  bool enable_agentic = 6;          // Tier 3 (future)
  string ai_model = 7;
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

The remediation engine lives inside Primary. It reuses Primary's existing scan pipeline and adds the transform → re-scan convergence loop. AI escalation is a gRPC call to the optional Abbenay daemon via the `AIProvider` protocol (ADR-025). See [DESIGN_AI_ESCALATION.md](DESIGN_AI_ESCALATION.md) for the full AI integration design.

## CLI Integration

### `apme-scan fix`

```
apme-scan fix [target] [options]

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
Phase 3: Scanning... 42 violation(s)
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
3. **Convergence loop** — scan → transform → re-scan loop with oscillation detection (done)
4. **`FixSession` RPC** — bidirectional streaming gRPC contract on Primary (done — ADR-028)
5. **CLI `fix` integration** — interactive review, `--apply`, `--check`, `--auto-approve` (done)
6. **AI escalation** — `AIProvider` protocol + `AbbenayProvider` + hybrid validation loop (in progress — Phase 3)
7. **Web UI remediation queue** — accept/reject AI proposals (Phase 4)
