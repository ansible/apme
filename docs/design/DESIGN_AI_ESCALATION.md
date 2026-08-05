# AI Escalation Design

## Status: implemented (Phase 3 complete)

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
  |  |          GraphRemediationEngine                           |  |
  |  |                                                            |  |
  |  |  Phase 1: Tier 1 convergence loop (graph-aware)           |  |
  |  |    scan -> partition -> apply_transform (per node) ->     |  |
  |  |    re-scan (NodeState tracking, content hashes)           |  |
  |  |                                                            |  |
  |  |  Phase 2: Tier 2 AI escalation (graph-native)             |  |
  |  |    for each remaining_ai node:                            |  |
  |  |      build AINodeContext from ContentGraph                |  |
  |  |      -> AIProvider.propose_node_fix()                     |  |
  |  |      -> node.update_from_yaml(fix.fixed_snippet)          |  |
  |  |      -> re-validate via rescan_fn                         |  |
  |  |    return GraphFixReport with ai_proposals                |  |
  |  +----------------------------------------------------------+  |
  |                                                                  |
  |  +-----------------+     +--------------------+                 |
  |  | AIProvider      |     | Best Practices     |                 |
  |  | Protocol        |     | Mapping (YAML)     |                 |
  |  | propose_node_fix|     +--------------------+                 |
  |  +--------+--------+                                             |
  |           |                                                      |
  |  +--------v--------+                                             |
  |  | AbbenayProvider  | <-- sole coupling point                   |
  |  | (default impl)   |                                            |
  |  | abbenay_grpc     |                                            |
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
class AISkipped:
    """A violation the AI could not fix, with an explanation."""
    rule_id: str
    line: int
    reason: str
    suggestion: str

@dataclass
class AINodeFix:
    """AI-generated fix for a single graph node."""
    fixed_snippet: str
    rule_ids: list[str] = field(default_factory=list)
    explanation: str = ""
    confidence: float = 0.85
    skipped: list[AISkipped] = field(default_factory=list)

class AIProvider(Protocol):
    async def propose_node_fix(
        self,
        context: AINodeContext,
        *,
        model: str | None = None,
    ) -> AINodeFix | None:
        """Propose a fix for a single graph node.

        The context carries the node's YAML, violations, parent context,
        and best-practice guidance. Returns None on failure.
        """
        ...
```

`AINodeContext` is built by `build_ai_node_context()` from `ai_context.py`, which extracts the node's YAML, violations, parent context, sibling snippets, and best-practice guidance from the `ContentGraph`.

### AbbenayProvider (Default Implementation)

`src/apme_engine/remediation/abbenay_provider.py` -- the sole file that imports `abbenay_grpc`.

Responsibilities:

- **Auto-discovery**: find the Abbenay daemon via `$XDG_RUNTIME_DIR/abbenay/daemon.sock`, `/run/user/<uid>/abbenay/daemon.sock`, or `/tmp/abbenay/daemon.sock`
- **Provider resolution**: `_resolve_ai_provider()` does not preflight the daemon; it returns `None` only when AI is disabled or required configuration is missing (address, model, import). Runtime failures surface during `propose_node_fix()` and the graph engine catches/skips those nodes
- **Prompt construction**: build a graph-native prompt from `AINodeContext` (node YAML, violations, parent context, sibling snippets, best practices)
- **Inline policy**: send policy on every request (temperature: 0.0, json_only format, max_tokens: 8192, timeout: 60000)
- **Response parsing**: extract `fixed_snippet`, `changes[]`, `skipped[]` from structured JSON response; aggregate confidence from `changes`
- **Error handling**: connection errors, timeouts, and API failures raise exceptions; the graph engine catches them and skips the node

```python
def discover_abbenay() -> str | None:
    """Auto-discover Abbenay daemon address from runtime socket."""
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    candidates = []
    if xdg:
        candidates.append(Path(xdg) / "abbenay" / "daemon.sock")
    candidates.append(Path(f"/run/user/{os.getuid()}/abbenay/daemon.sock"))
    candidates.append(Path("/tmp/abbenay/daemon.sock"))
    for sock in candidates:
        if sock.exists():
            return f"unix://{sock}"
    return None
```

---

## Hybrid Validation Loop

The core innovation: every AI proposal is re-validated through APME's own validators before presentation. If the AI introduces Tier 1-fixable side effects, deterministic transforms clean them up automatically. Only genuinely unfixable issues trigger an LLM retry.

### Per-Node Flow

```
  1. Build AINodeContext from ContentGraph
     +-- node YAML (yaml_lines from ContentNode)
     +-- violations targeting this node
     +-- parent context (parent node's YAML, if any)
     +-- sibling snippets (nearby tasks for context)
     +-- best practices (universal + rule-category-specific)
     +-- feedback from prior attempt (if retry)

  2. LLM call
     +-- ai_provider.propose_node_fix(context) -> AINodeFix | None

  3. Parse check
     +-- None / parse error -> skip node
     +-- valid -> continue

  4. Apply to graph
     +-- node.update_from_yaml(fix.fixed_snippet)
     +-- mark node dirty
     +-- record NodeState(source="ai")

  5. Re-validate (after all AI nodes in this pass)
     +-- rescan_fn(graph, dirty_nodes)
     +-- if new Tier 1 violations: post-AI cleanup pass

  6. Retry decision (global, not per-node)
     +-- ai_attempts < max_ai_attempts -> re-partition, repeat Phase B
     +-- feedback = prior violations for previously-attempted nodes

  7. User review (post-convergence)
     +-- proposals emitted as AINodeProposal with before/after YAML
     +-- approve via approve_pending(source_filter="ai")
     +-- interactive: y/n/a/s/q per proposal
```

### Why Hybrid Cleanup Before Retry

The LLM might fix the semantic issue perfectly but leave a formatting or FQCN gap that our Tier 1 transforms handle deterministically. Sending that back to the LLM wastes a call and risks the LLM introducing a different issue while "fixing" the first. The hybrid approach:

1. Tries our transforms first (fast, free, deterministic)
2. Only retries the LLM if issues remain that transforms cannot handle
3. Counts the transforms applied and reports them to the user

### Max Attempts

`max_ai_attempts` (default 2) limits how many times Phase B (AI) runs globally within the convergence loop. Each pass sends all current Tier 2 nodes to the LLM. On retry, nodes that were previously attempted receive feedback describing what violations remained after their first fix.

- One retry with specific feedback is usually sufficient
- More retries risk diminishing returns and increasing cost
- Total LLM calls per session = `max_ai_attempts * len(tier2_nodes)`

---

## Prompt Engineering

### Prompt Template

The prompt is built per-node by `_build_node_prompt()` in `abbenay_provider.py`. The structure (simplified; see `NODE_PROMPT_TEMPLATE` for the full template including the `Rules:` section):

```
You are an expert Ansible automation engineer and code reviewer.
Fix the YAML violations listed below.

## Violations
- [{rule_id}] {message} (line {line})
...

## YAML to fix
```yaml
{yaml_lines}
```

{parent_context_section}

{sibling_context_section}

## Ansible Best Practices
{best_practices}

{feedback_section}

## Instructions

Return the COMPLETE corrected YAML for this task/block in "fixed_snippet".
Do NOT return line numbers — just the corrected YAML text.

Respond with ONLY this JSON (no markdown fences, no explanation outside JSON):
{
  "fixed_snippet": "<the entire corrected YAML for this task/block>",
  "changes": [
    {
      "rule_id": "<rule ID fixed>",
      "explanation": "<one-sentence explanation>",
      "confidence": 0.95
    }
  ],
  "skipped": [
    {
      "rule_id": "<rule ID that could not be fixed>",
      "reason": "<why this cannot be auto-fixed>",
      "suggestion": "<how the user can fix this manually>"
    }
  ]
}
```

### Feedback Section (Retry Only)

```
## Previous Attempt Feedback
Your previous fix still has violations:
- M001: Use FQCN for module 'copy' (line 14)
- L008: Incorrect indentation (line 15)

Please correct these issues in your new response.
```

### Inline Policy

Every request to Abbenay includes an inline policy dict:

```python
policy = {
    "sampling": {"temperature": 0.0},
    "output": {
        "format": "json_only",
        "max_tokens": 8192,
    },
    "reliability": {
        "timeout": 60000,
    },
}
```

Temperature 0.0 ensures deterministic output. JSON-only format enables structured parsing.

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
| M006, M008, M009 | module_usage |
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

When `--ai` is set, Engine auto-discovers the Abbenay daemon:

1. Check `$XDG_RUNTIME_DIR/abbenay/daemon.sock` (Linux standard)
2. Fall back to `/run/user/<uid>/abbenay/daemon.sock`
3. Fall back to `/tmp/abbenay/daemon.sock`
4. Override with `APME_ABBENAY_ADDR` env var

### Preflight Health Check

Before entering the remediation loop, `_resolve_ai_provider` in Engine checks prerequisites:

```python
if not fix_opts or not fix_opts.enable_ai:
    return None

addr = os.environ.get("APME_ABBENAY_ADDR") or discover_abbenay()
if not addr:
    logger.warning("AI escalation requested but no Abbenay daemon found")
    return None

model = fix_opts.ai_model or os.environ.get("APME_AI_MODEL")
if not model:
    logger.warning("AI escalation requested but no model specified")
    return None

token = os.environ.get("APME_ABBENAY_TOKEN")
provider = AbbenayProvider(addr, token=token, model=model)
```

Graceful degradation: if the daemon address, model, or `abbenay_grpc` import is missing, `_resolve_ai_provider` returns `None` — AI escalation is disabled and Tier 2 violations fall to manual review.

### Health Check

The `apme health-check` subcommand checks all engine services:

```
$ apme health-check

  engine  (localhost:50051)  ok   12ms
  native   (localhost:50055)  ok    8ms
  opa      (localhost:50054)  ok   15ms
  ansible  (localhost:50053)  ok   22ms
  proxy    (localhost:8765)   ok    5ms
```

---

## CLI Integration

### Flags

```
apme remediate [target] [options]

AI Escalation:
  --ai                 Enable AI escalation for Tier 2 violations (opt-in)
  --model MODEL        Model for AI proposals (e.g., openai/gpt-4o)
  --auto-approve       Approve all AI proposals without prompting (CI mode)

Environment Variables:
  APME_ABBENAY_ADDR    Daemon address (default: auto-discover from socket)
  APME_ABBENAY_TOKEN   Consumer auth token
  APME_AI_MODEL        Default AI model
```

AI never runs unless explicitly requested via `--ai`.

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

Apply this fix? [y]es / [n]o / [a]ccept all / [s]kip rest / [q]uit:
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
| a | Accept all remaining | AI_PROPOSED for all |
| s | Skip all remaining | USER_REJECTED for all |
| q | Quit immediately | Remaining stay UNRESOLVED |

### CI Mode

When `--auto-approve` is set, all proposals are accepted without prompting — no confidence threshold filtering.

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

The `abbenay-client` Python package (import: `abbenay_grpc`) runs in-process with the engine (no separate container for the client). It connects to the Abbenay daemon via Unix socket (local) or TCP (remote/container).

### Inline Policy

Every `ChatRequest` includes an inline `PolicyConfig` (see `DESIGN-inline-policy.md` in the `abbenay-rd` repo). This means the user needs zero Abbenay-side configuration beyond a provider and API key.

### Consumer Auth

If the Abbenay daemon has a `consumers` section in its config, APME passes its token via `x-abbenay-token` metadata. The token is sourced from `--abbenay-token` or `APME_ABBENAY_TOKEN` env var.

### Model Selection

The `--model` flag is passed through to `AbbenayClient.chat(model=...)`. If omitted, Abbenay uses whatever default model the user has configured.

---

## MCP Tools (Deferred)

Dynamic MCP registration is available in Abbenay but has not yet been integrated into APME. The `src/apme_engine/mcp/` package does not exist. Best practices and module documentation are currently embedded directly in the prompt by the `AbbenayProvider` prompt builder, not served via MCP.

Future MCP integration (when implemented) would allow the LLM to autonomously call tools like `get_ansible_doc(fqcn)` and `get_ansible_best_practices(category)` during generation.

---

## Security

### Prompt Injection Mitigation

- Inline policy uses `system_prompt_mode: "prepend"` (not "replace") -- the admin's system prompt is preserved
- The user's Ansible YAML content is included as data in the user message, not in the system prompt
- Response is parsed as JSON; free-text from the LLM cannot inject commands

### Secret Handling

- API keys are managed by Abbenay (env, memory, keychain, or file store), never persisted by the Gateway
- Consumer tokens are passed via gRPC metadata, not logged
- File content sent to the LLM may contain sensitive data -- this is the user's responsibility (same as any LLM-assisted coding tool)

### Graceful Degradation

`_resolve_ai_provider()` returns `None` only when AI configuration or prerequisites are missing (no daemon address, no model configured, or the optional client import is unavailable). In that case, AI escalation is not activated. If `--ai` is set and a provider is resolved but the daemon is unreachable at proposal time, `propose_node_fix()` raises; the graph engine catches that failure and skips proposal generation for the affected node. Remaining Tier 2 violations are still reported as "AI-candidate", but with no proposals. Without `--ai`, Tier 2 violations are also reported as "AI-candidate" with no proposals.

---

## Optional Dependency

`abbenay-client` is an optional dependency, published on PyPI. Pin it to
the same CalVer as the Abbenay daemon image (currently `v2026.8.6`):

```toml
[project.optional-dependencies]
ai = [
    "abbenay-client==2026.8.6",
]
```

**Install (local / development):**

```bash
uv sync --extra ai
```

**Install (production / containers — hashed via ``uv.lock``):**

```bash
uv sync --frozen --extra ai
```

Container images use the frozen form (see ``containers/base/Dockerfile``)
so deployed builds verify wheel/sdist digests from the lockfile.

**Install (pip — version pin only, not for production):**

```bash
pip install apme-engine[ai]
```

``pip`` does not consume ``uv.lock`` hashes. That matches other optional
extras (``gateway``, ``dev``): the version pin is the contract; artifact
integrity for production comes from ``uv sync --frozen``.

If `--ai` is set but `abbenay-client` is not installed:

```text
Error: AI escalation requires the 'ai' extra.
Install with: uv sync --extra ai
# or: pip install apme-engine[ai]
# Production/containers: uv sync --frozen --extra ai
```

---

## Container Topology (with AI Escalation)

Pre-built multi-arch Abbenay images (amd64 + arm64) are available on GHCR:

```bash
podman pull ghcr.io/redhat-developer/abbenay:v2026.8.6
```

| Tag | Meaning |
|-----|---------|
| `:main` | Latest merged code |
| `:sha-<short>` | Specific commit |
| `:v2026.8.6` | Release (`v` prefix; APME pin) |
| `:latest` | Latest stable release |

```
+--------------------------- apme-pod ----------------------------+
|                                                                   |
|  +----------+  +----------+  +----------+  +----------+         |
|  | Engine  |  |  Native  |  |   OPA    |  | Ansible  |  ...    |
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
|  |  :50057  |  GHCR image or binary                               |
|  +----------+                                                     |
|                                                                   |
|  +--------------------------------------------+                  |
|  |       Galaxy Proxy :8765 (PEP 503)         |                  |
|  +--------------------------------------------+                  |
+-------------------------------------------------------------------+
```

Note: the Abbenay container's `CMD` sets `--grpc-host 0.0.0.0` so published ports work. A bare-metal daemon defaults to `--grpc-host 127.0.0.1` (localhost only); pass `--grpc-host 0.0.0.0` explicitly if connecting from another host.

---

## File Organization

```
src/apme_engine/remediation/
  +-- __init__.py
  +-- graph_engine.py          # GraphRemediationEngine (graph-aware convergence + AI)
  +-- partition.py              # is_finding_resolvable(), classify_violation()
  +-- registry.py               # TransformRegistry (node transforms only)
  +-- ai_provider.py            # AIProvider protocol, AINodeFix, AINodeContext
  +-- ai_context.py             # AINodeContext builder from ContentGraph
  +-- abbenay_provider.py       # AbbenayProvider, discover_abbenay(), preflight
  +-- transforms/
      +-- ...

src/apme_engine/data/
  +-- ansible_best_practices.yml   # structured mapping from agents.md

# src/apme_engine/mcp/ -- deferred, not yet implemented (see MCP Tools section)
```

---

## References

- [DESIGN_REMEDIATION.md](DESIGN_REMEDIATION.md) -- Tier 1 convergence loop, transform registry, fix pipeline
- [DESIGN_VALIDATORS.md](DESIGN_VALIDATORS.md) -- Validator protocol, scan pipeline
- [ADR-009: Separate Remediation Engine](../.sdlc/adrs/ADR-009-remediation-engine.md)
- [ADR-023: Per-Finding Classification](../.sdlc/adrs/ADR-023-per-finding-classification.md)
- [ADR-025: AIProvider Protocol Abstraction](../.sdlc/adrs/ADR-025-ai-provider-protocol.md)
- DESIGN-inline-policy.md (in abbenay-rd repo) -- Abbenay inline policy
- DESIGN-dynamic-mcp-registration.md (in abbenay-rd repo) -- Abbenay MCP registration RFE
- [ansible-creator agents.md](https://raw.githubusercontent.com/ansible/ansible-creator/refs/heads/main/docs/agents.md) -- Ansible coding guidelines
