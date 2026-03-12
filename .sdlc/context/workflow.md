# SDLC Workflow Guide

This document describes the Spec-Driven Development (SDD) workflow for APME, including when and how to use each skill.

## The SDLC Lifecycle

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              SDLC WORKFLOW                                      │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│   1. ASSESS           2. UNBLOCK          3. SPECIFY          4. EXECUTE       │
│   ───────────────     ───────────────     ───────────────     ───────────────  │
│   /sdlc-status   ───> /dr-review     ───> /req-new       ───> /task-new        │
│   (current state)     (blocking DRs)      (new feature)       (break down)     │
│                                                                      │         │
│                             ^                                        v         │
│                             │         <─────────────────────    Implement      │
│                             │         architectural decision?       │          │
│                             │                  yes                  │          │
│                        /adr-new  <──────────────────────────────────┘          │
│                             │                                                   │
│                             v                                                   │
│                        /dr-new (if question arises during work)                 │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Workflow Steps

### Step 1: Assess Current State (`/sdlc-status`)

**Always start here when resuming work.**

The status dashboard shows:
- All requirements and their completion status
- Open decision requests (DRs) by priority
- Architecture Decision Records (ADRs)
- Blockers that need attention
- Recent activity

This gives you visibility into what needs attention before diving into work.

### Step 2: Unblock (`/dr-review`)

**Before creating new work, address blocking DRs.**

Decision Requests capture open questions that may block progress. Address them in priority order:

1. **Blocking** - Stops other work entirely; resolve immediately
2. **High** - Affects upcoming work; resolve before it becomes blocking
3. **Medium** - Should be decided but doesn't block current work
4. **Low** - Nice to decide but can wait

Use `/dr-review` to:
- List all open DRs
- Review a specific DR
- Record a decision and rationale
- Move resolved DRs to `closed/`
- Optionally scaffold an ADR for architectural decisions

### Step 3: Specify (`/req-new`)

**Define the feature before implementing.**

When ready to define a new feature:
1. Ensure blocking DRs that affect this feature are resolved
2. Run `/req-new` to start the interactive wizard
3. Define user stories, acceptance criteria, and dependencies
4. The skill creates the spec directory structure

Requirements live in `.sdlc/specs/REQ-NNN-name/` with:
- `requirement.md` - User stories and acceptance criteria
- `design.md` - Technical approach
- `contract.md` - API/interface definitions
- `tasks/` - Implementation tasks

### Step 4: Execute (`/task-new`)

**Break requirements into actionable tasks.**

Use `/task-new REQ-NNN` to create implementation tasks:
- Each task should be 1-2 hours of focused work
- Define clear verification steps
- Link to specific acceptance criteria from the REQ
- Specify prerequisites (other tasks that must complete first)

Tasks include:
- Implementation steps
- Verification criteria
- Status tracking (Pending, In Progress, Complete, Blocked)

## During Implementation

### When Questions Arise (`/dr-new`)

If you encounter ambiguity or need a decision:
1. **Don't make ad-hoc choices** - Document the question
2. Run `/dr-new` to create a Decision Request
3. Set priority based on how blocking it is
4. Continue other work while awaiting decision

### When Architectural Decisions Are Made (`/adr-new`)

If you make a decision that:
- Affects multiple components
- Introduces new patterns or technologies
- Would be hard to reverse
- Needs to be communicated to the team

Then run `/adr-new` to document it as an Architecture Decision Record.

### Task Completion

When completing a task:
1. Run all verification steps from the TASK file
2. Update task status to Complete
3. Commit with message: `Implements TASK-NNN: description`
4. Check if this unblocks other tasks

## Common Workflows

### Starting a New Feature

```
/sdlc-status        # Check current state
                    # If blocking DRs exist:
/dr-review DR-NNN   #   Resolve blocking DRs first
/req-new            # Create requirement spec
/task-new REQ-NNN   # Break into tasks
                    # Implement tasks...
```

### Resuming Work

```
/sdlc-status        # See what's in progress
                    # Review blockers section
/dr-review          # Address any urgent DRs
                    # Continue on in-progress tasks
```

### Handling a Blocker

```
/dr-new             # Document the question
                    # Set appropriate priority
                    # Continue other work or...
/dr-review DR-NNN   # ...resolve if you have authority
```

### Making an Architectural Decision

```
/adr-new            # Document the decision
                    # Include context, options, consequences
                    # Update CLAUDE.md if it affects agent behavior
```

## Skill Quick Reference

| Skill | Purpose | When to Use |
|-------|---------|-------------|
| `/workflow` | Interactive workflow guide | When unsure what to do next |
| `/sdlc-status` | Status dashboard | Starting work, checking progress |
| `/dr-new` | Create Decision Request | Questions, blockers, ambiguity |
| `/dr-review` | Resolve Decision Request | Deciding open questions |
| `/req-new` | Create requirement | Starting a new feature |
| `/task-new` | Create task | Breaking down requirements |
| `/adr-new` | Create ADR | Documenting architectural decisions |

## Anti-Patterns to Avoid

### Don't Skip Assessment
Starting work without `/sdlc-status` risks:
- Missing blocking DRs that affect your work
- Duplicating work already in progress
- Not knowing about recent decisions

### Don't Let DRs Accumulate
Unresolved DRs cause:
- Blocked work piling up
- Ad-hoc decisions made inconsistently
- Loss of decision context over time

### Don't Skip Specs
Implementing without requirements leads to:
- Scope creep
- Missing acceptance criteria
- Difficulty verifying completion

### Don't Make Silent Decisions
Undocumented architectural choices cause:
- Inconsistent patterns across codebase
- Lost context when revisiting code
- Repeated debates about resolved questions

## Workflow States

### Healthy Workflow
- Few blocking DRs (0-1)
- Requirements have clear acceptance criteria
- Tasks are appropriately sized (1-2 hours)
- ADRs document significant decisions
- Status is checked regularly

### Warning Signs
- Multiple blocking DRs accumulating
- Tasks staying "In Progress" for days
- Requirements still "Draft" with tasks started
- Decisions being made without ADRs
- `/sdlc-status` not run before starting work

## Integration with Agent Guidelines

The workflow integrates with CLAUDE.md agent guidelines:

1. **Read this file (CLAUDE.md) first** - Covered by initial orientation
2. **Read relevant ADRs** - ADRs surface through `/sdlc-status`
3. **Read the REQ specification** - Created via `/req-new`
4. **Read the specific TASK** - Created via `/task-new`
5. **Follow implementation notes** - Task files include steps
6. **Run all verification steps** - Task files include verification
7. **Commit with message** - Reference TASK-NNN

## Related Documentation

- [CLAUDE.md](/CLAUDE.md) - Project constitution and agent guidelines
- [architecture.md](/.sdlc/context/architecture.md) - System architecture
- [ADR index](/.sdlc/adrs/README.md) - All architectural decisions
- [DR index](/.sdlc/decisions/README.md) - Decision request tracking
