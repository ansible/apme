# AI Escalation Design

## Status: in progress

This document describes the AI escalation subsystem for Tier 2 violations. It extends the remediation engine (see `DESIGN_REMEDIATION.md`) with an LLM-backed proposal pipeline that validates, cleans up, and interactively presents AI-generated fixes.

---

## Overview

The remediation engine classifies every violation into three tiers:

| Tier | Handler | Confidence |
|------|---------|------------|
| 1 -- Deterministic | Transform Registry | 100% -- known-correct rewrite |
| 2 -- AI-Proposable | AIProvider | Variable -- LLM proposes a patch |
| 3 -- Manual Review | Human | N/A -- requires judgment |

Tier 1 is fully implemented and runs in a convergence loop. Tier 2 is the subject of this document. After the Tier 1 loop converges (or bails on oscillation), remaining AI-candidate violations are escalated to an LLM via the `AIProvider` protocol.

The design prioritizes:

- **Correctness** -- every AI proposal is re-validated through APME's own validators before being presented to the user
- **Deterministic cleanup** -- if the AI introduces a Tier 1-fixable side effect, our transforms handle it automatically (no LLM call wasted)
- **Decoupling** -- the engine depends only on the `AIProvider` Protocol, never on a concrete LLM library
- **Opt-in** -- AI escalation is explicitly requested via `--ai`, never runs by default

---

## Architecture

```
                              APME Engine Process
  +----------------------------------------------------------------+
  |                                                                  |
  |  +----------------------------------------------------------+  |
  |  |          RemediationEngine                                |  |
  |  |                                                            |  |
  |  |  Phase 1: Tier 1 convergence loop                         |  |
  |  |    scan -> partition -> transform -> re-scan               |  |
  |  |    (existing, unchanged)                                   |  |
  |  |                                                            |  |
  |  |  Phase 2: Tier 2 AI escalation                            |  |
  |  |    for each remaining_ai violation:                       |  |
  |  |      build prompt                                         |  |
  |  |      -> AIProvider.propose_fix()                          |  |
  |  |      -> re-validate (scan snippet)                        |  |
  |  |      -> hybrid cleanup (Tier 1 transforms)                |  |
  |  |      -> yield proposal to CLI                             |  |
  |  +----------------------------------------------------------+  |
  |                                                                  |
  |  +-----------------+     +--------------------+                 |
  |  | AIProvider      |     | Best Practices     |                 |
  |  | Protocol        |     | Mapping (YAML)     |                 |
  |  | propose_fix()   |     +--------------------+                 |
  |  +--------+--------+                                             |
  |           |                                                      |
  |  +--------v--------+                                             |
  |  | AbbenayProvider  | <-- sole coupling point                   |
  |  | (default impl)   |                                            |
  |  | abbenay_client   |                                            |
  |  +--------+---------+                                            |
  +-----------|----------------------------------------------------- +
              | gRPC (Unix socket or TCP)
              v
     +------------------+
     | Abbenay Daemon   |   external process (binary or container)
     | :50057           |   manages providers, API keys, policies
     +------------------+
              |
              v HTTPS
     +------------------+
     | OpenAI/Anthropic |
     | Gemini/OpenRouter|
     +------------------+
```

### Key Boundaries

| Boundary | Mechanism | Why |
|----------|-----------|-----|
| Engine / AIProvider | Python `Protocol` | Swap providers without touching engine code |
| AbbenayProvider / Daemon | gRPC (Unix socket) | Abbenay manages provider secrets and policies |
| Engine / CLI | Generator/callback | Serial proposal delivery for interactive review |
| Best practices | Static YAML file | Curated, version-controlled, maintainable via SKILL |

---

## AIProvider Protocol

The engine's only dependency on AI is this protocol. See ADR-025.

```python
from __future__ import annotations
from typing import Protocol
from dataclasses import dataclass, field

@dataclass
class AIPatch:
    """A single task-level fix proposed by an AI provider."""
    rule_id: str
    line_start: int
    line_end: int
    fixed_lines: str
    explanation: str
    confidence: float
    diff_hunk: str = ""

@dataclass
class AISkipped:
    """A violation the AI could not fix, with an explanation."""
    rule_id: str
    line: int
    reason: str
    suggestion: str

@dataclass
class AIProposal:
    """AI-generated fixes for a single file (batch of patches)."""
    file: str
    original_yaml: str
    fixed_yaml: str
    patches: list[AIPatch]
    diff: str
    skipped: list[AISkipped] = field(default_factory=list)
    hybrid_transforms_applied: int = 0

class AIProvider(Protocol):
    async def propose_fixes(
        self,
        violations: list[ViolationDict],
        file_content: str,
        file_path: str,
        *,
        model: str | None = None,
        feedback: str | None = None,
    ) -> tuple[list[AIPatch] | None, list[AISkipped]]:
        """Propose fixes for multiple violations in a single file (batch).

        Returns tuple of (patches or None on failure, skipped violations).
        """
        ...

    async def propose_unit_fixes(
        self,
        violations: list[ViolationDict],
        snippet: str,
        file_path: str,
        line_start: int,
        line_end: int,
        *,
        model: str | None = None,
        feedback: str | None = None,
    ) -> tuple[list[AIPatch] | None, list[AISkipped]]:
        """Propose fixes for violations within a single unit (task snippet).

        Line numbers in returned patches refer to the original file.
        """
        ...
```

### AbbenayProvider (Default Implementation)

`src/apme_engine/remediation/abbenay_provider.py` -- the sole file that imports `abbenay_grpc`.

Responsibilities:

- **Auto-discovery**: find the Abbenay daemon via `$XDG_RUNTIME_DIR/abbenay/daemon.sock` or `~/.abbenay/daemon.sock`
- **Preflight**: call `health_check()` before starting, fail fast if unreachable
- **Prompt construction**: build a structured prompt from violation metadata, file content, ansible-doc output, and best practices
- **Inline policy**: send `PolicyConfig` on every request (temperature: 0.0, json_only format, retry_on_invalid_json)
- **Response parsing**: extract `fixed_yaml`, `explanation`, `confidence` from structured JSON response
- **Error handling**: connection errors, timeouts, malformed responses return `None`

```python
def discover_abbenay() -> str | None:
    """Auto-discover Abbenay daemon address from runtime socket."""
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    candidates = []
    if xdg:
        candidates.append(Path(xdg) / "abbenay" / "daemon.sock")
    candidates.append(Path.home() / ".abbenay" / "daemon.sock")
    for sock in candidates:
        if sock.exists():
            return f"unix://{sock}"
    return None
```

---

## Hybrid Validation Loop

The core innovation: every AI proposal is re-validated through APME's own validators before presentation. If the AI introduces Tier 1-fixable side effects, deterministic transforms clean them up automatically. Only genuinely unfixable issues trigger an LLM retry.

### Per-Violation Flow

```
  1. Build prompt
     +-- violation metadata (rule_id, message, file, line)
     +-- file content (full YAML)
     +-- code window (10 lines before/after violation)
     +-- ansible-doc output for relevant module (pre-fetched)
     +-- best practices (universal + rule-category-specific)
     +-- feedback from prior attempt (if retry)

  2. LLM call (attempt 1)
     +-- ai_provider.propose_fix() -> AIProposal | None

  3. Parse check
     +-- None / parse error -> resolution = AI_FAILED, skip
     +-- valid -> continue

  4. Re-validate
     +-- run scan_fn() on the proposed YAML
     +-- clean (0 new violations) -> present to user
     +-- new violations found -> step 5

  5. Hybrid cleanup
     +-- partition new violations via is_finding_resolvable()
     +-- Tier 1 fixable -> apply deterministic transforms
     +-- re-scan after transforms
     +-- clean -> present to user (note transforms applied)
     +-- still violations -> step 6

  6. Retry decision
     +-- attempt < 2 -> retry LLM with feedback (step 2)
     |   feedback = "your fix introduced: [violation list]"
     +-- attempt >= 2 -> resolution = AI_FAILED, skip

  7. User review (interactive, serial)
     +-- y -> write patch, resolution = AI_PROPOSED
     +-- n -> resolution = USER_REJECTED
     +-- s -> skip all remaining
     +-- q -> quit, remaining stay UNRESOLVED
```

### Why Hybrid Cleanup Before Retry

The LLM might fix the semantic issue perfectly but leave a formatting or FQCN gap that our Tier 1 transforms handle deterministically. Sending that back to the LLM wastes a call and risks the LLM introducing a different issue while "fixing" the first. The hybrid approach:

1. Tries our transforms first (fast, free, deterministic)
2. Only retries the LLM if issues remain that transforms cannot handle
3. Counts the transforms applied and reports them to the user

### Max Attempts

Two LLM calls per violation (initial + 1 retry with feedback). Rationale:

- One retry with specific feedback ("your fix introduced M001 on line 14") is usually sufficient
- More retries risk diminishing returns and increasing cost
- Configurable via `max_ai_attempts` parameter (default 2)

---

## Prompt Engineering

### Prompt Template

```
You are an Ansible remediation assistant. A static analysis rule has flagged
an issue in an Ansible YAML file. Your task is to fix the issue while following
Ansible best practices.

## Violation
- Rule: {rule_id}
- Message: {message}
- File: {file_path}
- Line: {line}

## Code Context (lines {start}-{end})
{code_window}

## Full File
{file_content}

## Module Documentation
{ansible_doc_output}

## Best Practices
{best_practices}

{feedback_section}

## Instructions
Fix the violation. Respond with a JSON object:
{
  "fixed_yaml": "the corrected YAML for the entire file",
  "explanation": "one-sentence explanation of what you changed",
  "confidence": 0.95
}

Rules:
- Preserve all YAML comments
- Maintain exact indentation (2 spaces)
- Use FQCN for all modules (e.g., ansible.builtin.copy, not copy)
- Use YAML syntax for task arguments, not key=value
- Use true/false for booleans, not yes/no
- Do not add or remove tasks, only fix the flagged issue
- If you cannot fix the issue with confidence, set confidence to 0.0
```

### Feedback Section (Retry Only)

```
## Previous Attempt Feedback
Your previous fix introduced new violations:
- M001: Use FQCN for module 'copy' (line 14)
- L008: Incorrect indentation (line 15)

Please correct these issues in your new response.
```

### Inline Policy

Every request to Abbenay includes an inline `PolicyConfig`:

```python
policy = {
    "sampling": {"temperature": 0.0},
    "output": {
        "format": "json_only",
        "max_tokens": 4096,
        "system_prompt_snippet": SYSTEM_PROMPT,
        "system_prompt_mode": "prepend",
    },
    "reliability": {
        "retry_on_invalid_json": True,
        "timeout": 30000,
    },
}
```

Temperature 0.0 ensures deterministic output. JSON-only format enables structured parsing. Retry-on-invalid-JSON handles transient LLM formatting issues at the Abbenay layer.

---

## Best Practices Mapping

Source: [ansible-creator agents.md](https://raw.githubusercontent.com/ansible/ansible-creator/refs/heads/main/docs/agents.md)

### Structured Mapping

`src/apme_engine/data/ansible_best_practices.yml` -- a structured YAML file keyed by rule category:

```yaml
_meta:
  source: https://raw.githubusercontent.com/ansible/ansible-creator/refs/heads/main/docs/agents.md
  commit: <pinned-sha>
  updated: 2026-03-17

universal:
  - "Use .yml extension for all YAML files"
  - "Use true and false for boolean values, not yes/no"
  - "Spell out task arguments in YAML style, not key=value format"
  - "All tasks must have descriptive names"
  - "All tasks must be idempotent"

fqcn:
  - "Use FQCN for all modules, roles, and playbooks"
  - "Prefer ansible.builtin for internal Ansible actions"
  - "Avoid collections keyword, use FQCN instead"
  - "Use canonical module names"

yaml_formatting:
  - "Indent at two spaces"
  - "Keep lines under 160 characters"
  - "Split long lines using YAML folding sign >-"

module_usage:
  - "Use specific modules instead of generic command or shell"
  - "Always use changed_when with command and shell"
  - "Always specify state parameter for clarity"
```

### Rule-to-Category Mapping

| APME Rule IDs | Best Practices Category |
|----------------|------------------------|
| M001-M004 | fqcn |
| L007, L008, L009 | yaml_formatting |
| M006, M008 | module_usage |
| L011, L012, L013 | naming |
| L043, L046 | jinja2 |

The prompt builder loads `universal` + the matching category for each violation.

### Maintenance

The best practices YAML can be updated manually or via automation:

1. Fetch the latest guidelines from upstream Ansible documentation
2. Parse and categorize by heading hierarchy
3. Update `ansible_best_practices.yml`, bump `_meta.commit` and `_meta.updated`
4. Review changes for accuracy

---

## Preflight and Discovery

### Auto-Discovery

When `--ai` is set, the CLI auto-discovers the Abbenay daemon:

1. Check `$XDG_RUNTIME_DIR/abbenay/daemon.sock` (Linux standard)
2. Fall back to `~/.abbenay/daemon.sock`
3. Override with `--abbenay-addr host:port`

### Preflight Health Check

Before entering the remediation loop:

```python
addr = args.abbenay_addr or discover_abbenay()
if addr is None:
    sys.exit("Error: --ai requires a running Abbenay daemon.\n"
             "Start with: abbenay daemon start\n"
             "Or specify: --abbenay-addr host:port")

provider = AbbenayProvider(addr, token=args.abbenay_token)
if not await provider.preflight():
    sys.exit("Error: Abbenay daemon at {addr} is not healthy.")
```

The `preflight()` method calls `abbenay_client.health_check()` -- the same `HealthCheck` RPC exposed by the Abbenay daemon.

### Health Check Extension

The existing `apme-scan health-check` subcommand is extended with `--include-ai`:

```
$ apme-scan health-check --include-ai

  primary  (localhost:50051)  ok   12ms
  native   (localhost:50055)  ok    8ms
  opa      (localhost:50054)  ok   15ms
  ansible  (localhost:50053)  ok   22ms
  cache    (localhost:50052)  ok    5ms
  abbenay  (unix:///run/user/1000/abbenay/daemon.sock)  ok  v2026.3.3  18ms
```

---

## CLI Integration

### Flags

```
apme-scan fix [target] [options]

AI Escalation:
  --ai                 Enable AI escalation for Tier 2 violations (opt-in)
  --model MODEL        Model for AI proposals (e.g., openai/gpt-4o)
  --abbenay-addr ADDR  Daemon address (default: auto-discover from socket)
  --abbenay-token TOK  Consumer auth token (or ABBENAY_TOKEN env var)
```

The existing `--no-ai` flag is replaced by `--ai` (opt-in). AI never runs unless explicitly requested.

### Interactive Review

Proposals are presented serially -- one at a time as each is validated:

```
[1/5] Rule M006 -- playbooks/setup.yml:12
AI validated (clean after hybrid cleanup, 2 transforms applied)

--- a/playbooks/setup.yml
+++ b/playbooks/setup.yml (AI proposed)
@@ -12,4 +12,5 @@
-    - name: copy config file
-      command: cp /tmp/app.conf /etc/app/app.conf
+    - name: Copy config file
+      ansible.builtin.copy:
+        src: /tmp/app.conf
+        dest: /etc/app/app.conf

Explanation: Replaced shell cp with ansible.builtin.copy for idempotency.
Confidence: 0.92

Apply this fix? [y]es / [n]o / [s]kip remaining / [q]uit:
```

Low-confidence proposals display a warning:

```
[3/5] Rule L043 -- playbooks/deploy.yml:28
[!] LOW CONFIDENCE: 0.45
```

| Key | Action | Resolution |
|-----|--------|------------|
| y | Write patch to disk | AI_PROPOSED |
| n | Skip this proposal | USER_REJECTED |
| s | Skip all remaining | USER_REJECTED for all |
| q | Quit immediately | Remaining stay UNRESOLVED |

### CI Mode

When `--apply` is set without a TTY (non-interactive), proposals with `confidence >= 0.9` are auto-accepted. All others are rejected. The threshold is not configurable in the initial implementation.

### Summary Output

```
  Tier 1 (deterministic):  30 fixed
  Tier 2 (AI-proposable):  10 candidates
    - 6 AI_PROPOSED (accepted)
    - 2 AI_FAILED (validation failed after 2 attempts)
    - 1 AI_LOW_CONFIDENCE (confidence: 0.45, rejected)
    - 1 USER_REJECTED
  Tier 3 (manual review):   2
  Passes: 3
```

---

## Abbenay Integration

### Communication

The `abbenay_client` Python library runs in-process with the engine (no separate container for the client). It connects to the Abbenay daemon via Unix socket (local) or TCP (remote/container).

### Inline Policy

Every `ChatRequest` includes an inline `PolicyConfig` (see `DESIGN-inline-policy.md` in the `abbenay-rd` repo). This means the user needs zero Abbenay-side configuration beyond a provider and API key.

### Consumer Auth

If the Abbenay daemon has a `consumers` section in its config, APME passes its token via `x-abbenay-token` metadata. The token is sourced from `--abbenay-token` or `ABBENAY_TOKEN` env var.

### Model Selection

The `--model` flag is passed through to `AbbenayClient.chat(model=...)`. If omitted, Abbenay uses whatever default model the user has configured.

---

## Deferred: MCP Tools

Blocked on Abbenay implementing `RegisterMcpServer` RPC (see `DESIGN-dynamic-mcp-registration.md`).

### Ansible Docstring Server

`src/apme_engine/mcp/ansible_doc_server.py` -- a lightweight MCP server (stdio transport) wrapping `ansible-doc` via APME's venv sessions. The LLM can autonomously call `get_ansible_doc(fqcn)` when it needs module documentation.

**Until then**: the prompt builder pre-fetches `ansible-doc` output for the relevant module and embeds it in the prompt.

### Best Practices Server

`src/apme_engine/mcp/ansible_best_practices_server.py` -- MCP server exposing the structured best practices mapping. The LLM calls `get_ansible_best_practices(category)` to look up guidelines.

**Until then**: best practices are pre-selected by the prompt builder based on violation category.

---

## Security

### Prompt Injection Mitigation

- Inline policy uses `system_prompt_mode: "prepend"` (not "replace") -- the admin's system prompt is preserved
- The user's Ansible YAML content is included as data in the user message, not in the system prompt
- Response is parsed as JSON; free-text from the LLM cannot inject commands

### Secret Handling

- API keys are managed by Abbenay (in its config or env vars), never passed through APME
- Consumer tokens are passed via gRPC metadata, not logged
- File content sent to the LLM may contain sensitive data -- this is the user's responsibility (same as any LLM-assisted coding tool)

### Graceful Degradation

If `--ai` is set but the daemon is unreachable, the CLI exits with a clear error. AI escalation never fails silently -- the user explicitly opted in and deserves an explicit failure. Without `--ai`, Tier 2 violations are reported as "AI-candidate" with no proposals.

---

## Optional Dependency

`abbenay-client` is an optional dependency:

```toml
[project.optional-dependencies]
ai = ["abbenay-client>=2026.3.3a0"]
```

Install with: `pip install apme-engine[ai]`

If `--ai` is set but `abbenay_client` is not installed:

```
Error: AI escalation requires the 'ai' extra.
Install with: pip install apme-engine[ai]
```

---

## Container Topology (with AI Escalation)

```
+--------------------------- apme-pod ----------------------------+
|                                                                   |
|  +----------+  +----------+  +----------+  +----------+         |
|  | Primary  |  |  Native  |  |   OPA    |  | Ansible  |  ...    |
|  |  :50051  |  |  :50055  |  |  :50054  |  |  :50053  |         |
|  | engine + |  |          |  |          |  |          |         |
|  | remediat |  |          |  |          |  |          |         |
|  | + AI esc |  |          |  |          |  |          |         |
|  +----+-----+  +----------+  +----------+  +----------+         |
|       |                                                           |
|       | gRPC (optional, only when --ai)                           |
|       v                                                           |
|  +----------+                                                     |
|  | Abbenay  |  AI daemon -- manages LLM providers                |
|  |  :50057  |  binary or container                                |
|  +----------+                                                     |
|                                                                   |
|  +--------------------------------------------+                  |
|  |         Cache Maintainer :50052            |                  |
|  +--------------------------------------------+                  |
+-------------------------------------------------------------------+
```

---

## File Organization

```
src/apme_engine/remediation/
  +-- __init__.py
  +-- engine.py              # RemediationEngine (Tier 1 loop + Tier 2 escalation)
  +-- partition.py            # is_finding_resolvable(), classify_violation()
  +-- registry.py             # TransformRegistry
  +-- ai_provider.py          # AIProvider Protocol, AIProposal dataclass
  +-- abbenay_provider.py     # AbbenayProvider, discover_abbenay(), preflight
  +-- transforms/
      +-- ...

src/apme_engine/data/
  +-- ansible_best_practices.yml   # structured mapping from agents.md

src/apme_engine/mcp/               # deferred -- blocked on Abbenay MCP RPC
  +-- ansible_doc_server.py
  +-- ansible_best_practices_server.py
```

---

## References

- [DESIGN_REMEDIATION.md](DESIGN_REMEDIATION.md) -- Tier 1 convergence loop, transform registry, fix pipeline
- [DESIGN_VALIDATORS.md](DESIGN_VALIDATORS.md) -- Validator protocol, scan pipeline
- [ADR-009: Separate Remediation Engine](../.sdlc/adrs/ADR-009-remediation-engine.md)
- [ADR-023: Per-Finding Classification](../.sdlc/adrs/ADR-023-per-finding-classification.md)
- [ADR-024: AIProvider Protocol Abstraction](../.sdlc/adrs/ADR-024-ai-provider-protocol.md)
- DESIGN-inline-policy.md (in abbenay-rd repo) -- Abbenay inline policy
- DESIGN-dynamic-mcp-registration.md (in abbenay-rd repo) -- Abbenay MCP registration RFE
- [ansible-creator agents.md](https://raw.githubusercontent.com/ansible/ansible-creator/refs/heads/main/docs/agents.md) -- Ansible coding guidelines
