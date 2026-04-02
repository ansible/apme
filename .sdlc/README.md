# Spec-Driven Development Framework

## Executive Summary

This framework enables **AI-assisted software development** through structured specifications. By defining requirements, decisions, and architecture in a consistent format, both human developers and AI agents can collaborate effectively on complex projects.

### The Transformation

| Input | Process | Output |
|-------|---------|--------|
| **PRD Document** | **Structured Specs** | **Working Software** |
| Product vision | Phases, REQs, Tasks | Tested code |
| Goals & metrics | Decision records | Deployed features |
| *"What we want"* | *"How we'll build it"* | *"What we ship"* |

### Key Benefits

| Benefit | Description |
|---------|-------------|
| **Traceability** | Every line of code traces back to a requirement |
| **AI-Ready** | Specifications are optimized for AI agent consumption |
| **Decision Log** | All architectural choices are documented with rationale |
| **Progress Visibility** | Real-time status across phases, requirements, and tasks |
| **Reduced Ambiguity** | Questions are captured and resolved before coding |

---

## The Approach: Spec-Driven Development

### Philosophy

> **"Specifications are the source of truth for both humans and AI agents."**

Traditional development often loses context between planning and implementation. Spec-Driven Development (SDD) maintains a living documentation system that:

1. **Captures intent** — What are we building and why?
2. **Records decisions** — What choices did we make and why?
3. **Tracks progress** — Where are we and what's blocking us?
4. **Guides implementation** — What exactly should be built?

### The Information Hierarchy

| Level | Location | Contains | Purpose |
|-------|----------|----------|---------|
| 1 | `CLAUDE.md` | Project rules, constraints, key ADRs | Constitution |
| 2 | `.sdlc/context/` | Architecture, conventions, personas | Knowledge Base |
| 3 | `.sdlc/phases/` | PHASE-001, PHASE-002, ... | Delivery Roadmap |
| 4 | `.sdlc/specs/` | REQ-001/, REQ-002/, ... | Requirements |
| 5 | `.sdlc/specs/REQ-*/tasks/` | TASK-001, TASK-002, ... | Implementation |
| 6 | `src/` | Code + Tests | Deliverables |

**Flow:** Constitution → Context → Phases → Requirements → Tasks → Code

---

## Skills & Automation

The framework provides **16 interactive skills** organized by purpose:

### Skill Categories

| Category | Skills | Purpose |
|----------|--------|---------|
| **Bootstrap** | `/prd-import`, `/sdlc-status`, `/workflow` | Start projects, check status |
| **Plan** | `/phase-new`, `/req-new`, `/task-new` | Define roadmap, create specs |
| **Decide** | `/dr-new`, `/dr-review`, `/adr-new` | Manage decisions |
| **Review** | `/pr-new`, `/pr-review`, `/pr-contributor-review` | Code review and PRs |
| **Operations** | `/lean-ci`, `/rfe-capture`, `/branch-align`, `/security-scan` | CI, RFEs, branch ops |

### Skill Reference

| Skill | Purpose | Example |
|-------|---------|---------|
| `/prd-import` | Import PRD → phases, REQs, context | `/prd-import /path/to/prd.pdf` |
| `/phase-new` | Create delivery milestone | `/phase-new "Enterprise Dashboard"` |
| `/sdlc-status` | Dashboard of all artifacts | `/sdlc-status` |
| `/workflow` | Interactive guidance | `/workflow next` |
| `/req-new` | Create requirement spec | `/req-new "Feature" --phase PHASE-001` |
| `/task-new` | Break requirement into tasks | `/task-new REQ-001` |
| `/dr-new` | Capture question/blocker | `/dr-new "How should X work?"` |
| `/dr-review` | Resolve decision request | `/dr-review DR-001` |
| `/adr-new` | Document architecture decision | `/adr-new` |

---

## Workflow

### The Development Cycle

| Step | Action | Skill | Output |
|------|--------|-------|--------|
| **1. Assess** | Check current state | `/sdlc-status` | Dashboard with blockers |
| **2. Unblock** | Resolve blocking decisions | `/dr-review` | Decided DRs |
| **3. Specify** | Define what to build | `/req-new`, `/phase-new` | Requirements |
| **4. Execute** | Break down and implement | `/task-new` | Working code |

**During implementation:**
- Question arises? → `/dr-new` (capture it)
- Architecture decision? → `/adr-new` (document it)
- Then return to **Assess**

### Phase Progression

| Phase | Name | Requirements | Status |
|-------|------|--------------|--------|
| PHASE-001 | CLI Scanner | REQ-001 (Scanning Engine) | **In Progress** |
| PHASE-002 | Rewrite Engine | REQ-002 (Automated Remediation) | **Implemented** |
| PHASE-003 | Enterprise Dashboard | REQ-003, REQ-004, REQ-008, REQ-010–014 | **In Progress** |
| PHASE-004 | AI Remediation | Abbenay AI integration (DR-005 decided) | **Implemented** |

**Phase status is derived from requirements:**
- **Not Started** = All REQs are Draft
- **In Progress** = At least one REQ is In Progress
- **Complete** = All REQs are Implemented

---

## Artifact Types

### Decision Tracking

| Type | Purpose | Lifecycle | Example |
|------|---------|-----------|---------|
| **DR** (Decision Request) | Questions needing answers | Open → Decided/Deferred | "Which database?" |
| **ADR** (Architecture Decision Record) | Decisions shaping the system | Proposed → Accepted/Deprecated | "Use PostgreSQL" |

**Flow:** Question (DR) → Decision made → Record (ADR)

### Requirement Structure

Each requirement lives in its own directory:

| File | Purpose |
|------|---------|
| `requirement.md` | What to build (user stories, acceptance criteria) |
| `design.md` | How to build it (approach, components) |
| `contract.md` | API/interface definitions |
| `tasks/TASK-*.md` | Atomic implementation units |

**Example:** `REQ-001-scanning-engine/`
- `requirement.md` — User stories, acceptance criteria
- `design.md` — Architecture approach
- `contract.md` — CLI interface, output formats
- `tasks/TASK-001-ari-wrapper.md` — First implementation task

---

## Quick Start

### For New Projects

| Step | Command | Result |
|------|---------|--------|
| 1 | `/prd-import /path/to/prd.pdf` | Phases, REQs, context created |
| 2 | `/sdlc-status` | Review generated artifacts |
| 3 | `/task-new REQ-001` | Start implementing |

### For Existing Projects

| Step | Command | Result |
|------|---------|--------|
| 1 | `/sdlc-status` | See current state |
| 2 | `/workflow next` | Get recommended action |
| 3 | `/dr-review DR-XXX` | Address blockers |

### For Questions or Decisions

| Situation | Command |
|-----------|---------|
| Have a question? | `/dr-new` |
| Made a decision? | `/adr-new` |

---

## Directory Structure

| Directory | Purpose |
|-----------|---------|
| `.sdlc/context/` | Stable project knowledge (architecture, conventions, personas) |
| `.sdlc/phases/` | Delivery roadmap (PHASE-001, PHASE-002, ...) |
| `.sdlc/specs/` | Feature specifications (REQ-001/, REQ-002/, ...) |
| `.sdlc/adrs/` | Architecture Decision Records |
| `.sdlc/decisions/open/` | Open questions (DRs) |
| `.sdlc/decisions/closed/` | Resolved questions |
| `.sdlc/research/` | Investigation and analysis documents |
| `.sdlc/templates/` | Reusable document templates |

### Context Files

See [context/README.md](context/README.md) for the full index. Key documents:

| File | Contains |
|------|----------|
| `architecture.md` | System design and topology |
| `deployment.md` | Podman pod setup and configuration |
| `conventions.md` | Coding standards |
| `workflow.md` | Process documentation |
| `getting-started.md` | Onboarding guide |
| `rule-catalog.md` | Snapshot of core validation rules (see [`docs/RULE_CATALOG.md`](../docs/RULE_CATALOG.md) for generated catalog) |

---

## Core Principles

| Principle | Description |
|-----------|-------------|
| **Spec-First** | Write specifications before code. No implementation without a REQ. |
| **Traceable** | Every artifact links: Phase → REQ → Task → Code → Test |
| **Question-Driven** | Ambiguity is captured as DRs, not left to assumption |
| **Decision-Logged** | Architectural choices recorded with context and rationale |
| **AI-Optimized** | Consistent formats enable AI agents to read and write specs |

---

## Key Files

| File | Purpose |
|------|---------|
| [CLAUDE.md](/CLAUDE.md) | Project constitution — rules and constraints |
| [context/architecture.md](context/architecture.md) | System design and topology |
| [context/conventions.md](context/conventions.md) | Coding standards |
| [phases/README.md](phases/README.md) | Delivery roadmap |
| [specs/README.md](specs/README.md) | Requirements index |
| [adrs/README.md](adrs/README.md) | Architecture decisions |
| [decisions/README.md](decisions/README.md) | Open questions |
| [research/](research/) | Investigation documents |

---

## Research Documents

Investigation and analysis documents that inform decisions:

| Document | Purpose |
|----------|---------|
| [integration-options-analysis.md](research/integration-options-analysis.md) | APME consumption patterns: standalone, CI/CD, AAP, Backstage |
| [ui-capabilities-assessment.md](research/ui-capabilities-assessment.md) | Frontend capabilities and Gateway API surface |
| [rfe-coverage-mapping.md](research/rfe-coverage-mapping.md) | Customer RFE requirements mapping |
| [ari-to-contentgraph-migration.md](research/ari-to-contentgraph-migration.md) | ARI engine migration planning |
| [contentgraph-migration-tracker.md](research/contentgraph-migration-tracker.md) | Migration progress tracking |
| [ansible-core-deprecation-mining.md](research/ansible-core-deprecation-mining.md) | Deprecation rule research |
| [cli-reporting-options.md](research/cli-reporting-options.md) | CLI output format analysis |
| [terrible-playbook-scan-gaps.md](research/terrible-playbook-scan-gaps.md) | Test coverage gaps |

---

## Current Project Status

**Project:** APME (Ansible Playbook Modernization Engine)

| Metric | Value |
|--------|-------|
| Phases | 4 defined (2 implemented, 2 in progress) |
| Requirements | 10 specified (1 implemented, 2 in progress, 7 draft) |
| DRs | 3 open, 10 decided, 2 deferred |
| ADRs | 44 total (31 implemented, 4 accepted, 7 proposed, 2 superseded) |
| Current Focus | PHASE-001/003: CLI Scanner + Enterprise Dashboard |

Run `/sdlc-status` for live dashboard.
