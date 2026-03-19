# DR-003: Dashboard Architecture

## Status

Open

## Raised By

Team Review — 2026-03-11

## Category

Architecture

## Priority

Medium

---

## Question

What is the dashboard architecture? The PRD mentions "Aggregated reporting for executives" but provides no wireframes, user flows, or technical design:

- Is it a standalone web app?
- An API that feeds into existing BI tools?
- What's the data pipeline from scan results to dashboard?
- Where do scan results get stored?

Currently scans are stateless — results are returned to the CLI and not persisted.

## Context

The current APME architecture is completely stateless. The CLI sends files, Primary scans them, results return to CLI. There's no database, no persistence, no history.

A dashboard implies:
- Persistent storage of scan results
- Historical trending
- Multi-project aggregation
- User authentication (maybe)
- Potentially multi-tenancy

This is a significant architectural addition.

## Impact of Not Deciding

- REQ-003 (Dashboard) cannot be implemented
- Data persistence architecture is undefined (DR-008 dependency)
- No clarity on deployment model for dashboard component

---

## Options Considered

### Option A: Standalone Streamlit App (Simple)

**Description**: Streamlit app that reads scan result JSON files from a directory. No database. User manually saves `--json` output to a known location.

**Pros**:
- Dead simple to implement
- No new infrastructure
- Users control their own data
- Works with existing stateless architecture

**Cons**:
- Manual file management
- No automatic history
- No multi-user support
- Doesn't scale

**Effort**: Low

### Option B: Dashboard with SQLite/PostgreSQL Backend

**Description**: Add a `ScanResultStore` service that persists scan results to a database. Dashboard queries the database.

**Pros**:
- Automatic history and trending
- Query flexibility
- Can add multi-project support

**Cons**:
- New service to deploy/maintain
- Adds state to previously stateless system
- Schema migrations
- Backup requirements

**Effort**: High

### Option C: Feed Existing BI Tools via API

**Description**: APME outputs scan results in formats consumable by Grafana, PowerBI, etc. No native dashboard — users use their existing BI infrastructure.

**Pros**:
- Enterprises already have BI tools
- No dashboard to build or maintain
- Leverages existing skills

**Cons**:
- No out-of-box experience
- More setup for users
- Less differentiation

**Effort**: Medium

### Option D: Defer Dashboard to v2

**Description**: Ship CLI-first v1 (like Spotter). Add dashboard in v2 once we understand user needs better.

**Pros**:
- Faster v1 ship
- Avoids premature optimization
- Learn from real usage

**Cons**:
- Missing PRD feature
- May lose competitive ground

**Effort**: None (deferral)

---

## Recommendation

**Option D** (defer to v2) is pragmatic. Spotter's CLI works without their dashboard. We should:
1. Ship CLI-first v1 with `--json` output
2. Define dashboard requirements in v2 based on user feedback
3. Revisit DR-008 (data persistence) when dashboard is in scope

If dashboard is a v1 requirement, then **Option A** (Streamlit + JSON files) is the lowest friction path.

---

## Related Artifacts

- REQ-003: Dashboard (blocked)
- DR-008: Data Persistence (dependency)
- PRD: Executive reporting requirement

---

## Discussion Log

| Date | Participant | Input |
|------|-------------|-------|
| 2026-03-11 | Team | Initial question raised during PRD review |

---

## Decision

**Status**: Decided → Resolved by ADR-029
**Date**: 2026-03-16 (original), 2026-03-19 (resolved)
**Decided By**: Team

**Original Decision**: Option D — Defer Dashboard to v2

**Rationale**:
- Ship CLI-first v1 with `--json` output
- Create UI mockups using AAP UI patterns for user surfacing and feedback
- Define dashboard requirements in v2 based on real user feedback
- Revisit DR-008 (data persistence) when dashboard implementation is in scope

**Resolution (2026-03-19)**: Dashboard architecture is now defined in
[ADR-029: Web Gateway Architecture](/.sdlc/adrs/ADR-029-web-gateway-architecture.md)
(Python/FastAPI gateway, cross-pod deployment, SQLite persistence, WebSocket-to-FixSession
HITL bridge) and [ADR-030: Frontend Deployment Model](/.sdlc/adrs/ADR-030-frontend-deployment-model.md)
(standalone PatternFly/React SPA with Backstage plugin as enterprise path).

**Action Items**:
- [x] Ensure CLI outputs clean JSON format for future dashboard consumption
- [x] Create UI mockups using AAP UI / PatternFly patterns (TASK-002 complete)
- [ ] Gather user feedback on mockups before v2 planning
- [x] Revisit DR-008 (data persistence) when dashboard is prioritized → resolved by ADR-029
