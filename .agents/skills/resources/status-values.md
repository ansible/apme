# SDLC Status Values

Shared reference for status values across all SDLC artifacts.

## Phase Status

| Status | Meaning |
|--------|---------|
| Not Started | All REQs are Draft |
| In Progress | At least one REQ is In Progress/Approved |
| Complete | All REQs are Implemented |

## Requirement Status

| Status | Meaning |
|--------|---------|
| Draft | Initial capture, not reviewed |
| In Review | Under stakeholder review |
| Approved | Ready for implementation |
| In Progress | Implementation underway |
| Implemented | All tasks complete |

## Task Status

| Status | Meaning |
|--------|---------|
| Pending | Not started |
| In Progress | Currently being worked on |
| Complete | Done and verified |
| Blocked | Waiting on dependency |

## Decision Request Priority

| Priority | Meaning |
|----------|---------|
| Blocking | Stops all progress |
| High | Affects current sprint |
| Medium | Should resolve soon |
| Low | Nice to decide |

## Decision Request Status

| Status | Meaning |
|--------|---------|
| Open | Needs decision |
| Decided | Choice made |
| Deferred | Postponed |
| Superseded | Replaced by another DR |

## ADR Status

| Status | Meaning |
|--------|---------|
| Proposed | Under review |
| Accepted | Approved and active |
| Deprecated | No longer applies |
| Superseded | Replaced by newer ADR |
