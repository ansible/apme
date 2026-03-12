# ADR New Example Session

This is a reference example for the `/adr-new` skill.

## Interactive Mode (Streamlined)

```
User: /adr-new

Claude: Let's create an Architecture Decision Record.

Decision title?

User: Redis for Session Cache

Claude: Context — why is this decision needed?

User: Dashboard needs session state for authenticated users. Need fast, reliable session store that works with Podman pod architecture.

Claude: Constraints and drivers? (bullet list)

User:
- Must run as container in pod
- Must be lightweight (<100MB)
- Need sub-millisecond reads
- Must support key expiration

Claude: Options considered (name: description, pros/cons):

User:
Redis: Redis 7 Alpine container with TTL keys
+ Sub-millisecond, built-in TTL, simple API, well-documented
- Extra container, connection pooling needed

In-memory dict: Shared dict in dashboard process
+ No container, zero latency, simple
- Lost on restart, no scaling, unbounded memory

SQLite: File-based with cleanup job
+ No container, persistent
- Slower, no TTL, file locking issues

Claude: Decision — which option and why?

User: Redis - best balance of performance, reliability, features. TTL essential for sessions.

Claude: Consequences? (use +/- prefix)

User:
+ Fast session lookups
+ Automatic expiration
+ Scales with pod replication
- One more container
- Network hop for reads
~ Can use Redis for other caching later

Claude: Status? (1=Proposed, 2=Accepted)

User: 1

Claude: Related ADRs? (numbers, or skip)

User: ADR-004

Claude: From a DR? (number, or skip)

User: DR-008

Claude: Done!
- Created: ADR-014-redis-session-cache.md
- Status: Proposed
- Linked to: DR-008

Next: Share for review, then update status to Accepted
```

## From-DR Mode

```
User: /adr-new --from-dr DR-008

Claude: Reading DR-008: Data Persistence...

DR-008 was decided:
- Decision: SQLite for MVP
- Context: [extracted]
- Options: SQLite, PostgreSQL
- Rationale: [extracted]

Create ADR from this? (Y to confirm, N to modify)

User: Y

Claude: Status? (1=Proposed, 2=Accepted)

User: 2

Claude: Done!
- Created: ADR-014-sqlite-persistence.md
- Status: Accepted
- Context, options, decision pre-filled from DR-008
```

## Quick Mode

```
User: /adr-new "gRPC over REST" --status accepted

Claude: Creating ADR-014...

Context? Internal service communication needs efficient binary protocol

Options? (brief)
- gRPC: binary, streaming, codegen. +fast -complexity
- REST: JSON, simple. +familiar -verbose

Decision? gRPC for performance and type safety

Consequences?
+ Faster inter-service calls
+ Generated client/server stubs
- Learning curve
- Harder to debug

Done! Created ADR-014-grpc-over-rest.md (Accepted)
```

## From Conversation

```
User: I think we should use Redis instead of Memcached for the cache layer because of persistence options...

[discussion continues]

User: /adr-new

Claude: I see you decided Redis over Memcached for caching.

Create ADR from this conversation?
- Title: Redis for Cache Layer
- Decision: Redis (over Memcached)
- Rationale: persistence options

Confirm? (Y to use, N to start fresh)

User: Y

Claude: Any additional context or consequences to add?

User: Also need pub/sub for cache invalidation

Claude: Done! Created ADR-014-redis-cache-layer.md
```

## Implementation Notes Example

```
Claude: Implementation guidance? (or skip)

User: Use redis-py with connection pooling. Default TTL 30 minutes. Health check on startup.

Claude: [includes in ADR under Implementation Notes section]
```
