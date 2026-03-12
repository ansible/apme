# SDLC Status Example Output

This is a reference example for the `/sdlc-status` skill.

```
User: /sdlc-status

Claude: # SDLC Status Dashboard

## Roadmap (Phases)

| Phase | Name | Status | Progress |
|-------|------|--------|----------|
| PHASE-001 | CLI Scanner | In Progress | 2/3 REQs |
| PHASE-002 | Rewrite Engine | Not Started | 0/2 REQs |
| PHASE-003 | Enterprise Dashboard | Not Started | 0/2 REQs |

Current Phase: **PHASE-001 - CLI Scanner** (In Progress)

## Requirements (Current Phase)

| REQ | Feature | Status | Tasks |
|-----|---------|--------|-------|
| REQ-001 | Scanning Engine | In Progress | 2/5 complete |
| REQ-002 | FQCN Detection | Implemented | 3/3 complete |
| REQ-003 | Risk Analyzer | Draft | 0 tasks |

## Open Decision Requests

| Priority | DR | Question | Raised |
|----------|-----|----------|--------|
| Blocking | DR-004 | AAP Pre-Flight Integration | 2026-03-11 |
| Blocking | DR-008 | Scan Result Persistence | 2026-03-11 |
| High | DR-001 | Version-Specific Analysis | 2026-03-11 |
| High | DR-007 | Target Persona Priority | 2026-03-11 |

...plus 7 medium/low priority DRs

## Architecture Decision Records

| Status | Count | Recent |
|--------|-------|--------|
| Accepted | 13 | ADR-013: Structured Diagnostics |
| Proposed | 0 | - |
| Deprecated | 0 | - |

## Blockers & Warnings

- DR-004 (Blocking): AAP Pre-Flight Integration
- DR-008 (Blocking): Scan Result Persistence
- REQ-001 TASK-003: Blocked (waiting on TASK-002)
- PHASE-002: Waiting on PHASE-001 completion

## Recent Activity

| Date | Type | Item | Change |
|------|------|------|--------|
| 2026-03-11 | REQ | REQ-002 | Implemented |
| 2026-03-11 | DR | DR-012 | Decided |
| 2026-03-11 | ADR | ADR-013 | Accepted |

---

Quick actions:
- `/dr-review DR-004` - Address blocking DR
- Continue TASK-003 in REQ-001
- PHASE-001 at 67% - 1 REQ remaining
```

## Example with No Phases

```
User: /sdlc-status

Claude: # SDLC Status Dashboard

## Requirements

| REQ | Feature | Status | Tasks |
|-----|---------|--------|-------|
| REQ-001 | Scanner | In Progress | 2/5 complete |
| REQ-002 | Rewriter | Draft | 0 tasks |

No phases defined. Run `/prd-import` to import a PRD.

...
```

## Example with All Phases Complete

```
User: /sdlc-status

Claude: # SDLC Status Dashboard

## Roadmap (Phases)

| Phase | Name | Status | Progress |
|-------|------|--------|----------|
| PHASE-001 | CLI Scanner | Complete | 3/3 REQs |
| PHASE-002 | Rewrite Engine | Complete | 2/2 REQs |

All phases complete! Project delivered.

...
```
