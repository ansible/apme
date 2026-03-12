# Getting Started with APME Development

This guide introduces the Spec-Driven Development (SDD) workflow used in APME.

## What is CLAUDE.md?

`CLAUDE.md` is the **project constitution** — the authoritative source of truth for AI agents working on APME. It defines:

- Project overview and goals
- Architecture decisions (ADRs)
- Development methodology
- Agent guidelines and constraints
- Quality gates

**Always read CLAUDE.md before starting work.**

## Understanding the .sdlc/ Structure

```
.sdlc/
├── context/           # Project context docs (you are here)
│   ├── architecture.md    # System architecture
│   ├── conventions.md     # Coding standards
│   ├── workflow.md        # SDLC workflow guide
│   └── getting-started.md # This file
├── templates/         # Templates for specs and decisions
│   ├── requirement.md
│   ├── task.md
│   ├── adr.md
│   └── decision-request.md
├── specs/             # Feature specifications
│   └── REQ-NNN-name/
│       ├── requirement.md
│       ├── design.md
│       ├── contract.md
│       └── tasks/
├── adrs/              # Architecture Decision Records
│   └── ADR-NNN-title.md
└── decisions/         # Decision Requests
    ├── open/          # Questions needing answers
    └── closed/        # Resolved decisions
```

## Which Skill to Use When

| Situation | Skill | Example |
|-----------|-------|---------|
| Starting work or checking progress | `/sdlc-status` | See blockers and current state |
| Have a question that blocks work | `/dr-new` | "Which caching strategy?" |
| Need to resolve an open question | `/dr-review` | Review and decide DR-001 |
| Starting a new feature | `/req-new` | Create REQ-005-reporting |
| Breaking down a requirement | `/task-new REQ-005` | Create implementation tasks |
| Made an architectural decision | `/adr-new` | Document pattern choice |
| Unsure what to do next | `/workflow` | Get interactive guidance |

## Quick Start Workflow

### 1. Check Current State
```
/sdlc-status
```
See what's in progress, blocked, or needs attention.

### 2. Address Blockers First
```
/dr-review
```
Resolve any blocking decision requests before starting new work.

### 3. Create or Find Your Work
- **New feature?** → `/req-new` then `/task-new`
- **Existing feature?** → Read the REQ and TASK files in `.sdlc/specs/`

### 4. Implement
1. Read relevant ADRs for context
2. Follow implementation steps in TASK file
3. Run verification steps
4. Commit with `Implements TASK-NNN: description`

## Key Concepts

### Requirements (REQ)
Define *what* to build with user stories and acceptance criteria.

### Tasks (TASK)
Define *how* to implement a requirement with concrete steps.

### Decision Requests (DR)
Capture questions that need answers before work can proceed.

### Architecture Decision Records (ADR)
Document significant technical decisions with context and rationale.

## Related Documentation

- [CLAUDE.md](/CLAUDE.md) — Project constitution
- [workflow.md](/.sdlc/context/workflow.md) — Detailed workflow guide
- [conventions.md](/.sdlc/context/conventions.md) — Coding standards
- [architecture.md](/.sdlc/context/architecture.md) — System architecture
