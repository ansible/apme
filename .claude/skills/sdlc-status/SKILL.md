---
name: sdlc-status
description: >-
  Show project status, progress, and blockers. Use when asking "what's the
  status?", "where are we?", "what's blocking?", "show me progress", or
  starting a work session. Do NOT use for creating or modifying artifacts
  (use req-new, dr-new, etc. instead).
argument-hint: "[phase or requirement]"
user-invocable: true
metadata:
  author: APME Team
  version: 1.0.0
---

# SDLC Status Dashboard

Show project status, progress, and blockers.

## Arguments

If `$ARGUMENTS` is provided (e.g., "PHASE-001" or "REQ-002"), show detailed
status for that specific artifact. Otherwise, show the full dashboard.

## Behavior

Scan SDLC directories, extract status from each artifact, and present a concise dashboard with actionable insights.

### 1. Scan These Locations

| Artifact | Path Pattern |
|----------|--------------|
| Phases | `.sdlc/phases/PHASE-*/phase.md` |
| Requirements | `.sdlc/specs/REQ-*/requirement.md` |
| Tasks | `.sdlc/specs/REQ-*/tasks/TASK-*.md` |
| Open DRs | `.sdlc/decisions/open/DR-*.md` |
| Closed DRs | `.sdlc/decisions/closed/*/DR-*.md` |
| ADRs | `.sdlc/adrs/ADR-*.md` |

### 2. Extract Status

| Artifact | Status Values |
|----------|---------------|
| PHASE | Not Started, In Progress, Complete |
| REQ | Draft, In Review, Approved, In Progress, Implemented |
| TASK | Pending, In Progress, Complete, Blocked |
| DR | Open (Blocking/High/Medium/Low), Decided, Deferred |
| ADR | Proposed, Accepted, Deprecated |

**Phase status is derived from its REQs:**
- Not Started: All REQs are Draft
- In Progress: At least one REQ is In Progress/Approved
- Complete: All REQs are Implemented

### 3. Present Dashboard

```markdown
# SDLC Status Dashboard

## Roadmap (Phases)
| Phase | Name | Status | Progress |
|-------|------|--------|----------|
| PHASE-001 | CLI Scanner | In Progress | 2/3 REQs |
| PHASE-002 | Rewrite Engine | Not Started | 0/2 REQs |

Current Phase: PHASE-001 (In Progress)

## Requirements (Current Phase)
| REQ | Feature | Status | Tasks |
|-----|---------|--------|-------|
| REQ-001 | Scanning Engine | In Progress | 2/5 |
| REQ-002 | FQCN Detection | Draft | 0 |

## Open Decision Requests
| Priority | DR | Question |
|----------|-----|----------|
(Blocking > High > Medium > Low)

## Blockers & Warnings
(items needing attention)

## Recent Activity
(last 7 days)

---
Quick actions:
(2-3 suggestions)
```

### 4. Phase-Aware Reporting

**Show current phase prominently:**
```
Current Phase: PHASE-001 - CLI Scanner (In Progress)
Progress: 1/3 requirements complete
```

**Group REQs by phase when showing all:**
```
PHASE-001: CLI Scanner (In Progress)
  REQ-001: Scanning Engine - In Progress (2/5 tasks)
  REQ-002: FQCN Detection - Draft

PHASE-002: Rewrite Engine (Not Started)
  REQ-003: Auto-Fix - Draft
```

**If no phases defined:** Show REQs without phase grouping (legacy mode)

### 5. Handle Scale

**If >10 REQs**: Show only current phase's REQs, summarize others
**If >10 DRs**: Show Blocking/High only, summarize medium/low
**If >4 phases**: Show current + next phase, summarize others

### 6. Handle Empty States

| Condition | Message |
|-----------|---------|
| No phases | "No phases defined. Run `/prd-import` to import a PRD." |
| No REQs | "No requirements. Run `/req-new` to create one." |
| No DRs | "No open decision requests." |
| No blockers | "No blockers. Ready to proceed." |

### 7. Identify Blockers

- **Blocking DRs**: Priority = "Blocking"
- **Blocked Tasks**: Status = "Blocked"
- **Phase blockers**: Previous phase not complete
- **Stale DRs**: Open >14 days
- **Spec Gaps**: Draft REQs with tasks

### 8. Generate Quick Actions

| Condition | Suggestion |
|-----------|------------|
| Blocking DRs | `/dr-review DR-XXX` |
| Current phase has pending tasks | Continue task or `/task-new` |
| Current phase complete | "Ready for PHASE-NNN" |
| All phases complete | "Project complete!" |

### 9. Recent Activity

Last 7 days:
- Phase status changes
- REQ status changes
- Completed tasks
- Decided DRs
- Accepted ADRs
