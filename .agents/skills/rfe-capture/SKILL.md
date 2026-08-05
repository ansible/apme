---
name: rfe-capture
description: >-
  Capture an external RFE (Jira, customer request, feature idea) using a
  research-first approach. Use when: "capture this Jira", "customer RFE",
  "feature request from X", "AAPRFE-123 should be tracked". This skill
  researches existing capabilities BEFORE creating specs to avoid duplicating
  what already exists. Do NOT use for internal feature ideas already discussed
  (use req-new instead).
argument-hint: "[Jira key or feature description]"
user-invocable: true
metadata:
  author: APME Team
  version: 1.0.0
---

# RFE Capture

Capture external RFEs with a research-first approach to avoid creating specs for capabilities that already exist.

## Why This Skill Exists

When capturing external RFEs (Jira tickets, customer requests), AI agents often create well-formatted specs without first understanding what the project already does. This leads to:
- Specs describing existing capabilities as new requirements
- Missing context about static vs. runtime boundaries
- Incorrect cross-references to related work
- Wasted review cycles

This skill enforces a **research phase before creation**.

## Arguments

If `$ARGUMENTS` is provided:
- Jira key (e.g., `AAPRFE-1607`) → fetch issue details via MCP
- Quoted description → use as feature summary
- `--quick` → abbreviated research, trust user context

## Workflow

### Phase 1: Understand the Request

**If Jira key provided:**
```
Fetching AAPRFE-1607...

Title: [title]
Description: [summary]
Labels: [labels]
Status: [status]

Is this the correct issue? (Y/N)
```

**If description provided:**
```
Feature request: "[description]"

What's the source? (Jira key, customer name, or "internal")
```

### Phase 2: Research Existing Capabilities

**CRITICAL: Do this BEFORE creating any specs.**

1. **Read CLAUDE.md** — understand project architecture, services, constraints
2. **Search for existing rules** that might address this:
   ```
   Searching validators for related functionality...
   - OPA bundle: src/apme_engine/validators/opa/bundle/
   - Native rules: src/apme_engine/validators/native/rules/
   - Ansible validator: src/apme_engine/validators/ansible/rules/
   ```
3. **Check existing specs and DRs**:
   ```
   Checking .sdlc/specs/README.md for REQ numbering...
   Checking .sdlc/decisions/README.md for related DRs...
   ```
4. **Understand ecosystem boundaries**:
   - APME = static analysis (scans content before runtime)
   - AA = runtime observability (collects data during job execution)
   - AAP = execution platform
   - Where does this request fit?

5. **Check output formats**:
   - CLI: `apme check --json` capabilities
   - gRPC: `SessionResult` / `SessionEvent` proto structure
   - What structured data already exists?

### Phase 3: Gap Analysis

Present findings:

```
## Research Summary

### What APME Already Does
- [Rule X]: [description of existing capability]
- [Rule Y]: [description of existing capability]
- Output format: [what's available today]

### What the RFE Requests
- [capability 1]
- [capability 2]

### Actual Gap
- [specific gap, if any]
- OR: "No gap — this capability exists via [rules/output]"

### Ecosystem Consideration
- This is a [static/runtime/integration] concern
- Related existing work: [DR-NNN, REQ-NNN]
```

### Phase 4: Architectural Impact Assessment

**CRITICAL: Do this BEFORE recommending any action.**

Read the **Architectural Invariants** section of `AGENTS.md`. Evaluate the RFE
against every invariant. Present findings:

```
## Architectural Impact

### Invariant Check
| # | Invariant | Impact |
|---|-----------|--------|
| 1 | Validators are read-only | No impact / CONFLICT: [describe] |
| 2 | gRPC between backend services | No impact / CONFLICT: [describe] |
| 3 | Async servers + executor discipline | No impact / CONFLICT: [describe] |
| 4 | Unified Validator contract | No impact / CONFLICT: [describe] |
| 5 | Stateless engine, edge persistence | No impact / CONFLICT: [describe] |
| 6 | Scale pods, not services | No impact / CONFLICT: [describe] |
| 7 | Session venvs are Engine-owned | No impact / CONFLICT: [describe] |
| 8 | Rule ID conventions (ADR-008) | No impact / CONFLICT: [describe] |
| 9 | OPA uses subprocess, not REST | No impact / CONFLICT: [describe] |
| 10 | FixSession is the unified client path | No impact / CONFLICT: [describe] |
| 11 | Engine never queries out; only emits | No impact / CONFLICT: [describe] |
| 12 | Engine-core services are required | No impact / CONFLICT: [describe] |
| 13 | Transforms semantically trusted; engine owns state | No impact / CONFLICT: [describe] |
| 14 | Built-in validator bundles are closed | No impact / CONFLICT: [describe] |

### Dependency Direction
- Does this require engine to know about the caller? [Y/N]
- Does this require engine to import from gateway? [Y/N]
- Does this add state to the engine? [Y/N]

### Separation of Concerns
- Does this mix detection and mutation in a validator? [Y/N]
- Does this put persistence logic in the engine? [Y/N]
- Does this require a non-gRPC protocol between backend services? [Y/N]
```

**If ANY conflict is found:**
```
⚠ ARCHITECTURAL CONFLICT DETECTED

This RFE conflicts with invariant(s) [N]. Before proceeding:
1. Can the feature be designed to work within the existing architecture?
2. If not, an ADR is required to justify the exception (use /adr-new)
3. Do NOT create a REQ that violates invariants without an accepted ADR

Redesign suggestion: [propose alternative that respects the architecture]
```

**If the RFE requires the engine to be aware of the caller** (e.g., "engine
should format output differently for the UI vs CLI"), this is always a conflict.
The engine produces a `SessionResult` / `SessionEvent` — presentation is the
caller's responsibility.

### Phase 5: Decision

Based on research and architectural impact, recommend ONE of:

| Outcome | When | Action |
|---------|------|--------|
| **No artifact needed** | Capability exists | Document in Jira comment, close |
| **Bug/task on existing REQ** | Small gap in existing feature | Create task under REQ-NNN |
| **New DR needed** | Architectural question to resolve | Use `/dr-new` |
| **New REQ needed** | Genuine new capability, no invariant conflicts | Use `/req-new` |
| **ADR required first** | Conflicts with invariants | Use `/adr-new` before `/req-new` |
| **Defer to other team** | Belongs in AA/AAP roadmap | Document and redirect |

```
## Recommendation

Based on research, I recommend: [outcome]

Architectural impact: [clean / requires ADR]
Rationale:
- [reason 1]
- [reason 2]

Proceed? (Y to execute, N to discuss, D for different approach)
```

### Phase 6: Execute (if artifact needed)

**If creating DR:**
- Use `/dr-new` with research context pre-filled
- Cross-reference related REQs and ADRs found in research

**If creating REQ:**
- Use `/req-new` with correct numbering from research
- Include "Related Artifacts" section with cross-refs
- Note existing capabilities that this builds on

**If creating task:**
- Use `/task-new` on the appropriate existing REQ
- Reference the external RFE in task description

### Phase 7: Attribution

When creating artifacts from AI-assisted research:
- "Raised By" should credit the human author
- Add note: "AI-assisted research and drafting"
- Include external reference (Jira key, customer ID)

## Examples

### Example 1: RFE for Existing Capability

```
/rfe-capture AAPRFE-1607

Fetching AAPRFE-1607...
Title: Deprecated module reports in Automation Analytics

Researching existing capabilities...
- L004 (OPA): Static deprecated modules check against curated list
- M002 (Ansible): Runtime introspection via ansible-core's module_loader
- M004 (Ansible): Removed/tombstoned module detection
- Output: SessionResult includes violations with metadata map

Gap Analysis:
- APME already detects deprecated modules comprehensively
- CLI --json output missing metadata map (small bug)
- AA integration is separate concern (runtime vs static)

Recommendation: No new REQ needed
- Create bug task on REQ-001 for CLI metadata gap
- Note in Jira that APME already provides this capability
- AA integration tracked separately in DR-004

Proceed? (Y/N)
```

### Example 2: Genuine New Capability

```
/rfe-capture "Support scanning Terraform files for Ansible references"

Researching existing capabilities...
- No Terraform-related rules found
- Parser only handles YAML/Ansible content
- This would require new file type support

Gap Analysis:
- Genuine new capability not currently supported
- Would need parser extension + new rule category

Recommendation: New REQ needed
- REQ-012: Terraform Integration
- Phase: PHASE-004 or new phase
- Depends on: REQ-001 (parser architecture)

Proceed? (Y/N)
```

## Integration with Other Skills

| Skill | When RFE-Capture Invokes It |
|-------|----------------------------|
| `/dr-new` | When architectural question needs resolution |
| `/req-new` | When genuine new capability identified |
| `/task-new` | When small gap in existing feature |
| `/sdlc-status` | To check current REQ/DR numbering |

## Anti-Patterns to Avoid

1. **Creating specs without reading code** — always research first
2. **Duplicating existing capabilities** — check rules before spec'ing
3. **Ignoring ecosystem boundaries** — static vs runtime matters
4. **Wrong numbering** — check existing specs before assigning numbers
5. **Missing cross-references** — link to related DRs/REQs/ADRs
6. **Skipping architectural impact** — always check AGENTS.md invariants
7. **Making the engine caller-aware** — engine produces data, callers format it
8. **Putting state in the engine** — persistence belongs in the Gateway
9. **Mixing detection and mutation** — validators detect, remediation engine fixes
