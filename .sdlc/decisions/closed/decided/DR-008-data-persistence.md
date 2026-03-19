# DR-008: Scan Result Persistence

## Status

Open

## Raised By

Team Review — 2026-03-11

## Category

Architecture

## Priority

Blocking

---

## Question

Where do scan results get persisted?

Currently, scans are completely stateless — results return to CLI and are gone. But:
- Dashboard requires historical data
- Trending requires time-series storage
- Multi-project views require aggregation

This is an architecture-forcing question: adding persistence changes the deployment model significantly.

## Context

Current architecture (per ARCHITECTURE.md):
- CLI → Primary → Validators → CLI
- No database, no persistence, no history
- Results are in-memory only

A dashboard implies:
- Scan results stored somewhere
- Queryable by project, time, rule, etc.
- Retention policy
- Backup strategy

This may require:
- New service (ResultStore)
- Database (SQLite, PostgreSQL, ClickHouse)
- Schema design
- Migration strategy

## Impact of Not Deciding

- DR-003 (Dashboard) blocked
- No historical trending possible
- Cannot implement multi-project aggregation

---

## Options Considered

### Option A: No Persistence (File-Based)

**Description**: Users save JSON output themselves. Dashboard reads files from a directory.

```bash
apme-scan . --json > results/$(date +%Y%m%d).json
```

**Pros**:
- Zero infrastructure
- User controls retention
- Works today

**Cons**:
- Manual file management
- No query capability
- Limited dashboard features

**Effort**: None (current state)

### Option B: SQLite (Embedded)

**Description**: Add SQLite database to Primary container. Scan results persisted automatically.

**Pros**:
- Zero additional infrastructure
- ACID transactions
- SQL query capability
- Backups = copy file

**Cons**:
- Single-node only
- Concurrent write limitations
- Schema migrations needed

**Effort**: Medium

### Option C: PostgreSQL (External)

**Description**: Require external PostgreSQL. Add `APME_DATABASE_URL` config.

**Pros**:
- Production-grade
- Multi-node support
- Rich ecosystem

**Cons**:
- Infrastructure requirement
- User must provision database
- More complex deployment

**Effort**: Medium

### Option D: ClickHouse (Analytics-Optimized)

**Description**: Use ClickHouse for time-series analytics. Excellent for dashboards.

**Pros**:
- Blazing fast for analytics queries
- Built for time-series
- Compression efficient

**Cons**:
- Overkill for most users
- Not great for small datasets
- Operational complexity

**Effort**: High

### Option E: Defer Persistence

**Description**: v1 remains stateless. Users export JSON. Persistence in v2.

**Pros**:
- Simpler v1
- Learn requirements from real usage

**Cons**:
- Dashboard blocked
- Missing feature vs Spotter

**Effort**: None (deferral)

---

## Recommendation

**Option E** (defer) for v1 with **Option A** (file-based) as interim.

If dashboard is required for v1, then **Option B** (SQLite) is the right balance:
- Zero external deps
- Embedded in existing container
- Sufficient for single-org use case
- Can migrate to PostgreSQL later if needed

---

## Related Artifacts

- DR-003: Dashboard Architecture (dependent)
- DR-007: Target Persona (DevOps-first reduces urgency)
- ARCHITECTURE.md: Current stateless design

---

## Discussion Log

| Date | Participant | Input |
|------|-------------|-------|
| 2026-03-11 | Team | Initial question raised during PRD review |

---

## Decision

**Status**: Deferred → Resolved by ADR-029
**Date**: 2026-03-16 (original), 2026-03-19 (resolved)
**Decided By**: Team

**Original Decision**: Deferred — follows DR-003 (Dashboard Architecture) deferral

**Rationale**:
- Dashboard deferred to v2 (DR-003)
- Persistence is only needed for dashboard features
- v1 remains stateless with JSON export capability
- Will decide persistence approach when dashboard work begins

**Resolution (2026-03-19)**: Persistence approach decided in
[ADR-029: Web Gateway Architecture](/.sdlc/adrs/ADR-029-web-gateway-architecture.md).
The web gateway owns persistence as a presentation concern (consistent with
ADR-020). **SQLite for V1** (zero infrastructure, embedded in gateway container),
with PostgreSQL as a documented upgrade path for enterprise deployments. The
engine remains stateless — persistence lives entirely in the gateway/reporting
layer.

**Action Items**:
- [x] Ensure CLI JSON output is well-structured for future persistence
- [x] Re-open this DR when dashboard is prioritized → resolved by ADR-029
- [x] Evaluate SQLite vs PostgreSQL based on v2 requirements → ADR-029 documents both
