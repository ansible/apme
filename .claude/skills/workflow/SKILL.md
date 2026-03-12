---
name: workflow
description: >-
  Get workflow guidance for spec-driven development. Use when unsure what
  to do next, "help me get started", "what should I work on", "I'm stuck",
  or starting a new session. Do NOT use for directly creating artifacts
  (this skill guides you to the right skill for that).
argument-hint: "[next|blockers|start|resume|decision|import]"
user-invocable: true
metadata:
  author: APME Team
  version: 1.0.0
---

# Workflow

Get workflow guidance for spec-driven development.

## Arguments

If `$ARGUMENTS` is provided, execute that subcommand directly:
- `next` — Single best next action
- `blockers` — Show what's blocking progress
- `start` — Start a new feature
- `resume` — Continue existing work
- `decision` — Handle a question or choice
- `import` — Import PRD to bootstrap project

If no argument, show interactive guide.

## Usage

```
/workflow              # Interactive guide
/workflow next         # Single best next action
/workflow blockers     # Show what's blocking progress
/workflow start        # Start a new feature
/workflow resume       # Continue existing work
/workflow decision     # Handle a question or choice
/workflow import       # Import PRD to bootstrap project
```

## Workflow Diagram

```
  0. BOOTSTRAP       1. ASSESS          2. UNBLOCK         3. SPECIFY         4. EXECUTE
  ──────────────     ──────────────     ──────────────     ──────────────     ──────────────
  /prd-import   ───> /sdlc-status ───> /dr-review    ───> /phase-new    ───> /task-new
  (from PRD)         (current state)   (blocking DRs)     /req-new           (break down)
                                                          (new feature)           │
                          ▲                                                       ▼
                          │            ◄──────────────────────────────────   Implement
                          │            architectural decision?                    │
                     /adr-new  ◄──────────────────────────────────────────────────┘
                          │
                          ▼
                     /dr-new (if question arises)
```

## Behavior

### Default: Interactive Guide

Show workflow diagram, then ask:
```
What would you like to do?
1. Import a PRD (new project)
2. Start a new feature
3. Resume work
4. Handle a decision
5. Get oriented

Or describe what you're trying to accomplish:
```

### `/workflow import` — Bootstrap from PRD

1. Check if phases/REQs already exist
2. If yes, warn and offer merge vs replace
3. Guide to `/prd-import`

### `/workflow next` — Smart Recommendation

Analyze state and return single best action:

1. No phases/REQs exist → suggest `/prd-import` or `/phase-new`
2. Scan for blocking DRs → suggest `/dr-review DR-NNN`
3. Scan for blocked tasks → suggest unblocking action
4. Scan for in-progress tasks → suggest continuing
5. Scan for pending tasks → suggest starting one
6. Current phase complete → suggest next phase

Output:
```
**Recommended next action:**
→ /dr-review DR-004

Reason: Blocking DR affecting REQ-001.
```

### `/workflow blockers` — Quick Blockers Check

Show only blocking items:
```
## Current Blockers

**Blocking DRs:** DR-004, DR-008
**Blocked Tasks:** TASK-003 (waiting on DR-004)
**Phase Blockers:** PHASE-002 waiting on PHASE-001
**Stale Items:** DR-002 (open 14+ days)

Quick fix: /dr-review DR-004
```

### `/workflow start` — New Feature

1. Check for blocking DRs that might affect new work
2. If blockers exist, warn and suggest resolving first
3. If phases exist, ask which phase
4. Guide to `/req-new --phase PHASE-NNN`

### `/workflow resume` — Continue Work

1. Run status analysis
2. Show current phase and progress
3. Show in-progress items
4. Show blocked items and why
5. Recommend specific next action

### `/workflow decision` — Handle Question/Choice

Present decision handling table and ask which applies:

| Situation | Action |
|-----------|--------|
| Have a question/blocker | `/dr-new` |
| Made an architecture choice | `/adr-new` |
| Need to resolve open DR | `/dr-review` |

## Context-Aware Suggestions

After any guidance, provide specific next action based on state:

| State | Suggestion |
|-------|------------|
| No phases exist | `/prd-import` or `/phase-new` |
| Blocking DRs exist | `/dr-review DR-NNN` first |
| Current phase complete | "Ready for PHASE-NNN" |
| In-progress tasks | Continue TASK-NNN |
| Pending tasks, no blockers | Start TASK-NNN |
| No requirements | `/req-new --phase PHASE-NNN` |
| All caught up | "No blockers. Ready to proceed." |

## Common Scenarios

| Situation | Action | Skill |
|-----------|--------|-------|
| Have a PRD to implement | Import it | `/prd-import` |
| Planning delivery phases | Create phase | `/phase-new` |
| Stuck on implementation | Capture blocker | `/dr-new` |
| Technical choice needed | Document decision | `/adr-new` |
| Requirements unclear | Raise question | `/dr-new` |
| Need to review work | Check status | `/sdlc-status` |
| Breaking down feature | Create tasks | `/task-new REQ-NNN` |

## Natural Language Understanding

Accept variations:
- "import this PRD" → `/workflow import`
- "what should I do next" → `/workflow next`
- "what's blocking me" → `/workflow blockers`
- "help me get started" → `/workflow start`
- "I'm lost" → default interactive guide
- "continue where I left off" → `/workflow resume`

## Skill Quick Reference

| Skill | Purpose |
|-------|---------|
| `/prd-import` | Import PRD, create phases and REQs |
| `/phase-new` | Create a delivery phase |
| `/sdlc-status` | Dashboard with phase progress |
| `/req-new` | Create requirement (assign to phase) |
| `/task-new` | Create tasks for a REQ |
| `/dr-new` | Capture question/blocker |
| `/dr-review` | Decide and close a DR |
| `/adr-new` | Record architecture decision |
