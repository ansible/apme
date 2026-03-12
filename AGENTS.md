# APME Agent Configurations

This document defines the specialized agents used in APME development.

## Agent Roles

### 1. Spec Writer Agent

**Purpose**: Creates and maintains specification documents.

**Context Files**:
- `CLAUDE.md`
- `.sdlc/templates/requirement.md`
- `.sdlc/templates/task.md`
- `.sdlc/context/project-overview.md`

**Capabilities**:
- Write requirement specifications
- Create task breakdowns
- Draft architecture decision records
- Ensure spec completeness

**Constraints**:
- Must use templates from `.sdlc/templates/`
- Must link related specs (REQ -> TASK)
- Must include acceptance criteria

---

### 2. Scanner Implementation Agent

**Purpose**: Implements the ARI wrapper and scanning functionality.

**Context Files**:
- `CLAUDE.md`
- `.sdlc/specs/REQ-001-scanner/`
- `.sdlc/context/dependencies.md`

**Capabilities**:
- Integrate with ARI library
- Parse ARI output formats
- Categorize detected issues
- Generate scan reports

**Constraints**:
- Must preserve ARI's original functionality
- Must handle all ARI output formats
- Must not modify playbook files during scanning

---

### 3. Rewriter Implementation Agent

**Purpose**: Implements the LangGraph-based rewriting workflow.

**Context Files**:
- `CLAUDE.md`
- `.sdlc/specs/REQ-002-rewriter/`
- `.sdlc/context/architecture.md`

**Capabilities**:
- Build LangGraph state machines
- Implement YAML transformations
- Apply FQCN fixes
- Handle deprecated module replacements

**Constraints**:
- Must preserve YAML comments
- Must maintain playbook semantics
- Must create backups before modifications
- Must be idempotent

---

### 4. Dashboard Implementation Agent

**Purpose**: Implements the Streamlit dashboard.

**Context Files**:
- `CLAUDE.md`
- `.sdlc/specs/REQ-003-dashboard/`

**Capabilities**:
- Create Streamlit interfaces
- Visualize scan results
- Display modernization progress
- Generate reports

**Constraints**:
- Must handle large datasets efficiently
- Must be responsive
- Must support export functionality

---

### 5. Integration Agent

**Purpose**: Creates CI/CD integrations and examples.

**Context Files**:
- `CLAUDE.md`
- `.sdlc/specs/REQ-004-integration/`
- `examples/`

**Capabilities**:
- Create GitHub Actions workflows
- Create AAP pre-flight checks
- Write integration documentation
- Create example configurations

**Constraints**:
- Must be copy-paste ready
- Must include clear documentation
- Must handle common edge cases

---

## Agent Workflow

```
┌─────────────────┐
│  Spec Writer    │ ──► Creates REQ and TASK specs
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Implementation │ ──► Scanner, Rewriter, Dashboard agents
│     Agents      │     implement based on specs
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Integration    │ ──► Creates CI/CD examples and docs
│     Agent       │
└─────────────────┘
```

## Handoff Protocol

When transitioning between agents:

1. **Completing Agent**:
   - Update task status to `Complete`
   - Document any deviations from spec
   - Note open questions for next agent

2. **Receiving Agent**:
   - Read CLAUDE.md for context
   - Read relevant REQ and TASK specs
   - Check for notes from previous agent
   - Continue from documented state

## Quality Assurance

All agents must:

1. Follow the spec exactly
2. Run verification steps
3. Update task status
4. Commit with proper message format
5. Flag any spec ambiguities
