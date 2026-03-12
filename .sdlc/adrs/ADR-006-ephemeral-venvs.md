# ADR-006: Ephemeral Per-Request venvs for Ansible Validator

## Status

Accepted

## Date

2026-03

## Context

The Ansible validator requires an `ansible-core` installation to run syntax checks, argspec validation, and module introspection. Multiple ansible-core versions (2.18, 2.19, 2.20) must be supported concurrently.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Pre-built venv pool with semaphores | Amortized build cost | Shared state, stale venvs, locking complexity |
| Ephemeral per-request venvs | Perfect isolation, automatic cleanup | ~1-2s creation cost per request |
| Pre-built at container build time (static) | Zero runtime cost | Cannot support dynamic version selection |

## Decision

**Ephemeral per-request venvs.**

Each `Validate()` call:
1. Creates a temporary venv using UV (from warm cache)
2. Runs all rules
3. Destroys the venv in a `finally` block

## Rationale

- UV's persistent wheel cache makes venv creation fast (~1-2 seconds from warm cache)
- Zero shared state between concurrent requests — no locking, no stale venvs
- Automatic cleanup: the venv is destroyed after each request regardless of success or failure
- The Ansible Dockerfile pre-warms the UV cache at build time (`prebuild-venvs.sh`)
- Concurrency is bounded by `maximum_concurrent_rpcs=8` on the Ansible gRPC server

> "I think we should build a venv per request and dispose of it when done per session per core version." — user decision

## Consequences

### Positive
- Perfect isolation between requests
- No stale state issues
- Automatic cleanup
- Dynamic version selection

### Negative
- 1-2s overhead per request
- Requires UV and warm cache

## Implementation Notes

```python
async def validate(self, request):
    venv_path = tempfile.mkdtemp()
    try:
        await create_venv(venv_path, request.ansible_core_version)
        results = await run_ansible_rules(venv_path, request)
        return results
    finally:
        shutil.rmtree(venv_path)
```

- Pre-warm cache: `scripts/prebuild-venvs.sh`
- UV cache: `/root/.cache/uv/`

## Related Decisions

- ADR-007: Async gRPC servers (enables concurrent venv creation)
