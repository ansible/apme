# ADR-007: Fully Async gRPC Servers (grpc.aio)

## Status

Accepted

## Date

2026-03

## Context

The original gRPC servers used synchronous `grpc.server()` with `ThreadPoolExecutor`. Under concurrent load, thread exhaustion and blocking I/O in validators (subprocess calls, HTTP requests to OPA) were identified as bottlenecks.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Synchronous gRPC + ThreadPoolExecutor | Simple, familiar | Thread exhaustion under load, blocking I/O wastes threads |
| grpc.aio (fully async) | Non-blocking I/O, `asyncio.gather()` for fan-out | Requires async-aware libraries |

## Decision

**grpc.aio for all five gRPC servers.**

- CPU-bound work runs via `run_in_executor()`
- I/O-bound work uses native async libraries:
  - `httpx.AsyncClient` for OPA
  - `asyncio.create_subprocess_exec` for Gitleaks

## Rationale

- The implementation pattern is consistent across validators — the async overhead is minimal
- Primary benefits most: `asyncio.gather()` for parallel validator fan-out replaces `ThreadPoolExecutor.map()`
- OPA validator: `requests.post()` → `httpx.AsyncClient.post()` — truly non-blocking HTTP
- Each server sets `maximum_concurrent_rpcs` for backpressure control
- `request_id` propagation is naturally supported through async call chains

> "How much more complex? Our implementation should be pretty simple and similar across validators." — user decision

## Consequences

### Positive
- Non-blocking I/O throughout
- Better resource utilization
- Parallel validator fan-out
- Consistent implementation pattern

### Negative
- Requires async-aware libraries
- Debugging async code can be harder

## Implementation Notes

```python
# Primary fan-out
async def validate(self, request):
    tasks = [
        self.native_stub.Validate(request),
        self.opa_stub.Validate(request),
        self.ansible_stub.Validate(request),
        self.gitleaks_stub.Validate(request),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return merge_results(results)
```

```python
# Server configuration
server = grpc.aio.server(
    options=[
        ('grpc.max_concurrent_rpcs', 8),
    ]
)
```

## Related Decisions

- ADR-001: gRPC communication
- ADR-006: Ephemeral venvs (async creation)
