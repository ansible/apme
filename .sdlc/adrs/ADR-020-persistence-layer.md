# ADR-020: Persistence Layer Requirements

## Status

Proposed

## Date

2026-03

## Context

The APME engine currently has no persistence layer — all scan results, risk assessments, and violation data are computed on every run and rendered to the terminal or returned via gRPC. There is no database.

A persistence layer will likely become necessary when the project adds an executive dashboard for cost-savings and time-saved metrics. Tracking those numbers over time, across runs, and across projects requires durable storage.

When that time comes, the choice of persistence technology must account for the fact that schema changes are inevitable as the product evolves. Upgrade hassle — the operational cost of migrating stored data when the schema changes — should be a first-class design consideration, not an afterthought.

## Decision

**No persistence layer yet.** This ADR documents the requirements and constraints that must be evaluated when the need arises, so the decision is informed rather than reactive.

## Requirements (when the time comes)

### 1. Upgrade hassle must be minimal

Schema migrations are one of the largest sources of operational pain in database-backed applications. The persistence layer must support one of these migration strategies:

| Strategy | How it works | Trade-offs |
|----------|-------------|------------|
| Schema migration tool (e.g. Alembic) | Versioned migration scripts run on upgrade | Proven pattern, but requires writing and testing migrations for every schema change |
| Schema-on-read / document store | Store loosely-structured data (JSON/BSON), handle version differences at read time | Flexible, but pushes complexity into application code |
| Append-only / event sourcing | Store immutable events, derive current state from the log | Natural audit trail, but more complex to query |
| Embedded versioned format | SQLite or similar with a version table and simple upgrade hooks | Zero-infrastructure, but limited concurrency |

The key constraint: a user upgrading APME should not need a DBA. The upgrade path should be `pip install --upgrade apme-engine` (or container pull) and the tool handles its own data migration transparently.

### 2. Minimal infrastructure burden

APME currently deploys as a Podman pod with no external state. Adding a database server (PostgreSQL, MySQL) changes the operational model significantly. Prefer approaches that either:

- Embed storage in the process (SQLite, DuckDB), or
- Use a database that's trivial to run in the same pod (SQLite file, embedded key-value store)

A full database server is justified only if the dashboard requires concurrent multi-user access or cross-host aggregation.

### 3. Type safety

Per ADR-018, all code must pass `mypy --strict`. The persistence layer must have type stubs or `py.typed` support. Per ADR-019, it must also pass the dependency governance checklist.

### 4. What needs to be stored

At minimum (speculative, to be refined):

- Scan results per project/run (violations, severity counts, timestamps)
- Cost/time-saved metrics derived from remediation actions
- Trend data for dashboard charts (deltas between runs)
- Configuration state (which rules are enabled/disabled per project)

## Alternatives to Evaluate Later

These are not options being decided now — they're a shortlist to investigate when persistence is needed:

- **SQLite + Alembic**: Zero-infrastructure, proven migration tooling, good `py.typed` support via `sqlite3` stdlib. Limited concurrent writes.
- **DuckDB**: Columnar, excellent for analytics/dashboards, embedded, Python-native. Newer, smaller ecosystem.
- **SQLite + JSON columns**: Hybrid — relational structure for indexes, JSON for flexible scan-result payloads. Avoids rigid schema for evolving data.
- **Plain JSON/JSONL files**: Simplest possible approach. No dependencies, no migrations, easy to inspect. Won't scale to large history or concurrent access.

## Related Decisions

- ADR-004: Podman pod as deployment unit (adding a DB changes the pod topology)
- ADR-018: mypy strict mode (persistence layer must be fully typed)
- ADR-019: Dependency governance (any new DB dependency must pass the checklist)
