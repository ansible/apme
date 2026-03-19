# ADR-027: Agentic Project-Level AI Remediation

## Status

Proposed

## Date

2026-03-19

## Context

APME's current AI remediation (Phase 3) operates at the **task level**: individual tasks are extracted as `FixableUnit`s, sent to an LLM with their violations, and patches are returned. This works well for task-scoped rules (FQCN fixes, module parameter updates, etc.).

However, many violations cannot be fixed at the task level:

| Rule | Issue | Why task-level fails |
|------|-------|---------------------|
| L042 | High task count (>100 tasks) | Requires restructuring the entire play |
| M010 | Python 2 interpreter | May need group_vars or inventory changes |
| R108 | Privilege escalation at play level | Requires understanding play structure |
| R501 | Undeclared collection dependencies | Needs to read/create collections/requirements.yml |
| — | Variable defined in one file, used wrong in another | Cross-file context required |

These violations are currently routed to Tier 3 (manual review) because:
1. Task snippets lack sufficient context
2. Fixes may require creating new files
3. Changes may span multiple files

We need a higher-level AI remediation tier that can understand and modify the entire project.

## Decision

**We will implement a two-tier AI remediation architecture:**

1. **Tier 2: Task-scoped AI** (current) — Fast, parallel, unit-segmented
2. **Tier 3: Agentic project-level AI** (new) — Full project context with MCP tools

The agentic tier will:
- Copy the project to an isolated sandbox
- Provide MCP file tools (read, write, search, list)
- Send all remaining violations to an LLM agent
- Require structured JSON output with patches and explanations
- Re-validate proposed changes through APME scan
- Present changes for human review before applying

### Architecture

```
Remediation Pipeline
====================

  Tier 1: Deterministic transforms
          (FQCN, formatting, deprecated params)
                      |
                      v
  Tier 2: Task-scoped AI
          - Unit segmentation via NodeIndex
          - Parallel LLM calls (semaphore-limited)
          - Re-validation + Tier 1 cleanup
                      |
                      v
  Tier 3: Agentic project-level AI
          - Sandbox isolation
          - MCP file tools
          - Multi-file patch proposals
          - Re-validation loop
                      |
                      v
  Tier 4: Manual review
          (violations AI cannot fix)
```

### Sandbox Environment

The agent operates in an isolated copy of the project:

1. APME copies project to /tmp/apme-sandbox-xxxxx/
2. MCP file server provides tools scoped to sandbox
3. LLM agent explores and modifies files via tools
4. APME collects patches and re-validates
5. Changes applied to original only after human review

### MCP Tools

| Tool | Description | Parameters |
|------|-------------|------------|
| read_file | Read file contents | path: str |
| write_file | Write/create file | path: str, content: str |
| list_directory | List directory contents | path: str |
| search_files | Search for pattern in files | pattern: str, glob: str |
| delete_file | Remove a file | path: str |

Tools operate within the sandbox only; the agent cannot access the original project.

### Structured Output Contract

The agent must return JSON conforming to this schema:

```json
{
  "patches": [
    {
      "file": "site.yml",
      "line_start": 45,
      "line_end": 52,
      "fixed_lines": "- name: Configure webserver\n  hosts: web\n  ...",
      "violations_addressed": ["L042", "R108"],
      "explanation": "Refactored monolithic play into role-based structure"
    }
  ],
  "new_files": [
    {
      "path": "group_vars/all.yml",
      "content": "ansible_python_interpreter: auto\n",
      "explanation": "Centralized Python interpreter configuration"
    }
  ],
  "deleted_files": [
    {
      "path": "legacy/old_playbook.yml",
      "explanation": "Consolidated into main site.yml"
    }
  ],
  "unfixable": [
    {
      "rule_id": "R501",
      "file": "site.yml",
      "line": 12,
      "reason": "Requires access to external inventory file not in project"
    }
  ]
}
```

### Re-validation Loop

```python
async def escalate_tier3(project_path: Path, violations: list[Violation]) -> AgenticResult:
    sandbox = create_sandbox(project_path)
    mcp_server = SandboxMCPServer(sandbox)
    
    for attempt in range(max_attempts):
        proposal = await call_agent(violations, mcp_server, feedback)
        
        # Apply proposed changes to sandbox
        apply_patches_to_sandbox(sandbox, proposal)
        
        # Re-run APME scan
        new_violations = await scan_sandbox(sandbox)
        
        # Check if we made progress
        addressed = set(v.id for v in violations) - set(v.id for v in new_violations)
        introduced = set(v.id for v in new_violations) - set(v.id for v in violations)
        
        if not introduced:
            # Success: no new violations introduced
            return AgenticResult(
                patches=proposal.patches,
                new_files=proposal.new_files,
                remaining=new_violations,
            )
        
        # Retry with feedback about introduced violations
        feedback = format_validation_feedback(introduced)
        reset_sandbox(sandbox, project_path)
    
    return AgenticResult(remaining=violations)  # Escalate to Tier 4
```

### Integration with Abbenay

Abbenay `v2026.3.6-alpha` supports dynamic MCP tool registration (DR-025 merged). The integration:

```python
async def call_agent(violations, mcp_server, feedback=None):
    tools = mcp_server.get_tool_definitions()
    prompt = build_agentic_prompt(violations, feedback)
    
    response = await abbenay.chat(
        model="anthropic/claude-sonnet-4",
        message=prompt,
        tools=tools,
        policy={
            "output": {"format": "json_only", "max_tokens": 16384},
            "reliability": {"timeout": 120000},
        },
    )
    
    return await execute_agent_loop(response, mcp_server)
```

Direct Anthropic API calls also work as a fallback for prototyping without Abbenay.

## Alternatives Considered

### Alternative 1: Play-Level Intermediate Tier

**Description**: Add a "play-scoped" tier between task and project.

**Pros**:
- Smaller context than full project
- Faster inference

**Cons**:
- Many play-level issues need project context (group_vars, inventory)
- Additional complexity (three AI tiers)
- Still cannot handle cross-file issues

**Why not chosen**: Jump from task to full project with tools is cleaner and more capable.

### Alternative 2: Dump Full Project into Context

**Description**: Send entire project as text to LLM, no tools.

**Pros**:
- Simple implementation
- Single LLM call

**Cons**:
- Token limits (~128K) exceeded by large projects
- Expensive (pay for full context every call)
- No ability to create new files

**Why not chosen**: Not scalable; agentic approach with tools is more robust.

### Alternative 3: RAG + Retrieval

**Description**: Index project with embeddings, retrieve relevant chunks.

**Pros**:
- Efficient for very large projects

**Cons**:
- Embedding/indexing overhead
- May miss relevant context
- Complex infrastructure

**Why not chosen**: MCP tools provide deterministic access; RAG adds complexity without clear benefit for typical Ansible project sizes.

## Consequences

### Positive

- **Complete remediation coverage** — Violations that task-level AI cannot fix now have a path
- **Multi-file fixes** — Can create group_vars/, update requirements.yml, restructure roles
- **Safer** — Sandbox isolation prevents accidental damage
- **Explainable** — Structured output includes rationale for each change
- **Validated** — Re-scan catches regressions before applying

### Negative

- **Slower** — Agentic loops with tool calls take longer than single-shot
- **More expensive** — Multiple LLM calls per session
- **Complexity** — MCP server, sandbox management, agent loop
- **Reliability** — Agents can get stuck or make poor decisions

### Neutral

- Only triggered for violations that Tier 2 cannot fix
- Abbenay MCP support is available (v2026.3.6-alpha); direct API also works as fallback
- Human review still required before applying changes

## Implementation Notes

1. **Phase 1: Abbenay MCP integration**
   - Abbenay `v2026.3.6-alpha` supports dynamic tool registration
   - Wire sandbox MCP tools through Abbenay's unified interface
   - Prove sandbox + tool + re-validation loop works
   - Measure success rate and cost

2. **Phase 2: Harden and optimize**
   - Direct Anthropic API available as fallback
   - Performance tuning based on Phase 1 metrics

3. **Sandbox implementation**:
   ```python
   def create_sandbox(project_path: Path) -> Path:
       sandbox = Path(tempfile.mkdtemp(prefix="apme-sandbox-"))
       shutil.copytree(
           project_path, 
           sandbox / "project",
           ignore=shutil.ignore_patterns('.git', '__pycache__', '*.pyc')
       )
       return sandbox / "project"
   ```

4. **MCP server** (~200 lines Python):
   - Implement as class with tool methods
   - Convert to MCP tool definitions for LLM
   - Track all writes for patch extraction

5. **CLI integration**:
   - --ai-agentic flag to enable Tier 3 (opt-in)
   - Or automatic escalation when Tier 2 has remaining violations

## Related Decisions

- ADR-009: Remediation Engine (tiered architecture)
- ADR-024: AI Provider Protocol (Abbenay integration)
- ADR-025: Rule Scope as First-Class Metadata (determines escalation tier)

## References

- MCP Specification: https://modelcontextprotocol.io/
- Anthropic Tool Use: https://docs.anthropic.com/en/docs/build-with-claude/tool-use
- Current unit segmentation: src/apme_engine/remediation/unit_segmenter.py

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-19 | AI Agent | Initial proposal |
