# Workflow Example Sessions

This is a reference example for the `/workflow` skill.

## Basic Navigation

```
User: /workflow

Claude: SDLC WORKFLOW

  0. BOOTSTRAP       1. ASSESS          2. UNBLOCK         3. SPECIFY         4. EXECUTE
  ──────────────     ──────────────     ──────────────     ──────────────     ──────────────
  /prd-import   ───> /sdlc-status ───> /dr-review    ───> /phase-new    ───> /task-new
  (from PRD)         (current state)   (blocking DRs)     /req-new           (break down)
                                                          (new feature)           │
                          ▲                                                       ▼
                          │            ◄──────────────────────────────────   Implement
                     /adr-new  ◄──────────────────────────────────────────────────┘
                          │
                     /dr-new (if question arises)

What would you like to do?
1. Import a PRD (new project)
2. Start a new feature
3. Resume work
4. Handle a decision
5. Get oriented

User: 1

Claude: ## Import PRD

Do you have a PRD file to import?

1. Paste file path (PDF, MD, or TXT)
2. Paste content directly
3. Provide a URL

→ /prd-import
```

## Bootstrap from PRD

```
User: /workflow import

Claude: ## Import PRD

Checking for existing artifacts...
No phases or REQs found. Ready for fresh import.

→ /prd-import

Provide the PRD as:
- File path: /path/to/prd.pdf
- Pasted content
- URL
```

## Phase-Aware Resume

```
User: /workflow resume

Claude: ## Current State

**Current Phase:** PHASE-001 - CLI Scanner (In Progress)
Progress: 1/3 requirements complete

**In Progress:**
- REQ-001: Scanner (2/5 tasks complete)
  - TASK-003: In Progress
  - TASK-004, TASK-005: Pending

**Blocked:**
- TASK-003 waiting on DR-004 decision

**Recommended:**
→ /dr-review DR-004 (unblocks TASK-003)

Or continue with unblocked work:
→ TASK-004: Implement reporter module
```

## Quick Next Action

```
User: /workflow next

Claude: Analyzing current state...

**Current Phase:** PHASE-001 - CLI Scanner (67% complete)

**Recommended next action:**
→ /dr-review DR-004

Reason: DR-004 is blocking and affects multiple requirements.

After that: Complete TASK-003, then PHASE-001 will be ready for completion.
```

## Quick Blockers Check

```
User: /workflow blockers

Claude: ## Current Blockers

**Blocking DRs:**
- DR-004: AAP Pre-Flight Integration
- DR-008: Scan Result Persistence

**Blocked Tasks:**
- TASK-003: Waiting on DR-004

**Phase Blockers:**
- PHASE-002 waiting on PHASE-001 completion

**Stale Items:**
- None

Quick fix: `/dr-review DR-004`
```

## Start New Feature with Phase

```
User: /workflow start

Claude: ## Starting a New Feature

Checking for blockers...
Found 1 blocking DR: DR-004

**Available Phases:**
| Phase | Status | Progress |
|-------|--------|----------|
| PHASE-001 | In Progress | 2/3 REQs |
| PHASE-002 | Not Started | 0/2 REQs |

Which phase is this feature for?

User: PHASE-001

Claude: → /req-new --phase PHASE-001

Or address blocker first:
→ /dr-review DR-004
```

## Handle Decision

```
User: /workflow decision

Claude: ## Decision Handling

| Situation | Action |
|-----------|--------|
| Have a question/blocker | `/dr-new` |
| Made an architecture choice | `/adr-new` |
| Need to resolve open DR | `/dr-review` |

Which applies?

User: I need to decide between two approaches

Claude: That sounds like an architectural decision.

→ /adr-new

Or if you need input from others first:
→ /dr-new (capture as open question)
```

## Describe Intent Naturally

```
User: /workflow I'm stuck and don't know what to work on

Claude: Let me help you get oriented.

Checking status...

**Current Phase:** PHASE-001 - CLI Scanner
- Progress: 2/3 requirements (67%)
- Blockers: 2 DRs

**Recommended path:**
1. `/dr-review DR-004` — resolve blocking question
2. `/dr-review DR-008` — resolve second blocker
3. Pick up TASK-003 in REQ-001
4. Complete PHASE-001, then move to PHASE-002

Start with: `/dr-review DR-004`
```

## Phase Completion

```
User: /workflow next

Claude: Analyzing current state...

**PHASE-001: CLI Scanner** - All requirements complete!

**Recommended next action:**
→ Mark PHASE-001 complete and start PHASE-002

PHASE-002: Rewrite Engine (Not Started)
- REQ-003: Auto-Fix (Draft)
- REQ-004: Transform Registry (Draft)

→ Review REQ-003 and begin task breakdown
```
