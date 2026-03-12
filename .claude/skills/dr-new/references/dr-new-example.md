# DR New Example Session

This is a reference example for the `/dr-new` skill.

## Interactive Mode

```
User: /dr-new

Claude: Let's create a new Decision Request.

What question needs to be decided?

User: Should we use SQLite or PostgreSQL for scan result persistence?

Claude: Context — why is this decision needed now?

User: DR-008 identified persistence as blocking, now we need to choose implementation

Claude: Category and Priority?
1. Architecture + Blocking
2. Architecture + High
3. Technical + Blocking
4. Let me specify separately

User: 1

Claude: What's blocked if we don't decide?

User: Dashboard implementation, historical scan comparison, CLI persistence

Claude: Describe the options (I'll structure them):

User: Option A: SQLite - embedded single-file database, no extra container, simple but limited concurrency.
Option B: PostgreSQL - dedicated container, full SQL, scales to multi-user but more complex.

Claude: Recommendation? (or "none")

User: SQLite for v1 MVP, migrate to PostgreSQL later if needed

Claude: Searching for related artifacts...

Found:
- DR-008: Data Persistence (directly related)
- ADR-004: Podman Pod Deployment
- REQ-003: Dashboard

Creating DR-012-database-backend-selection.md...

Done!
- Created: open/DR-012-database-backend-selection.md
- Added to README.md (Priority: Blocking)

Next: /dr-review DR-012 when ready to decide
```

## Quick Mode

```
User: /dr-new "SQLite vs PostgreSQL for persistence" --priority blocking --category arch

Claude: Creating DR from input...

Question: SQLite vs PostgreSQL for persistence
Category: Architecture
Priority: Blocking

I'll need a bit more info:
- What's blocked? Dashboard, historical scans
- Options? (brief) SQLite=simple/limited, PostgreSQL=scalable/complex

Done! Created DR-012-database-backend-selection.md
```

## From Conversation

```
User: I'm not sure if we should use REST or gRPC for the dashboard API...

[discussion continues]

User: /dr-new

Claude: I see you've been discussing REST vs gRPC for the dashboard API.

Create DR from this conversation?
- Question: REST or gRPC for dashboard API
- Category: Architecture
- Context: [extracted from discussion]

Confirm? (Y to create, N to start fresh)

User: Y

Claude: Priority level?
1. Blocking  2. High  3. Medium  4. Low

User: 2

Claude: Done! Created DR-013-dashboard-api-protocol.md
```
