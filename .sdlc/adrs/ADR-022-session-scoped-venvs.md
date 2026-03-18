# ADR-022: Session-Scoped Venvs with Lifecycle Management

## Status

Accepted

## Date

2026-03-17

## Context

The Ansible validator creates Python venvs containing `ansible-core` (and optionally Ansible collections) to perform plugin introspection (FQCN resolution, redirect detection, deprecation checks). The original implementation used a shared, hash-keyed venv directory under `~/.apme-data/collection-cache/venvs/`. This had two problems:

1. **Concurrency bug**: `build_venv` checked for an existing directory and then created it without holding a lock. Two concurrent requests for the same `ansible_core_version + collection_specs` hash could race, corrupting the shared venv.

2. **No lifecycle management**: Venvs accumulated on disk indefinitely. There was no concept of sessions, TTLs, or cleanup. For future features (AI escalation via Abbenay, MCP tools for enterprise collection docstrings), we need venvs that persist for a known session duration and can be queried by other components.

## Decision

**Replace the shared hash-keyed venv pool with session-scoped venvs managed by `VenvSessionManager`.**

Each session gets its own isolated directory with:
- A file lock (`.lock`) for safe concurrent creation via `fcntl.flock()`
- Metadata (`session.json`) tracking ansible version, collection specs, timestamps
- A `venv/` subdirectory containing the actual virtualenv

Sessions come in two flavors:
- **Ephemeral** (default): auto-generated UUID, cleaned up on `release()`
- **Named** (via `--session <id>`): persist for a configurable TTL, reusable across CLI invocations

### Storage Layout

```
~/.apme-data/collection-cache/sessions/
    <session_id>/
        venv/             # the virtualenv
        session.json      # metadata (version, specs, timestamps, ephemeral flag)
        .lock             # flock target for creation serialization
```

### CLI Integration

```bash
# Ephemeral (default, backward compatible)
apme-scan scan playbook.yml

# Named session (reusable, VS Code extension use case)
apme-scan scan playbook.yml --session my-project --session-ttl 7200

# Session management
apme-scan session list
apme-scan session info my-project
apme-scan session delete my-project
apme-scan session reap --ttl 3600
```

## Alternatives Considered

### Alternative 1: Add a Mutex to the Existing Shared Pool

Add `fcntl.flock()` around `build_venv` calls in the existing hash-keyed scheme. This fixes the race condition but doesn't provide session lifecycle management needed for future AI escalation and MCP tools.

**Rejected**: Solves concurrency but not lifecycle requirements.

### Alternative 2: Truly Ephemeral tempdir-based Venvs

Use `tempfile.mkdtemp()` for every request, deleting when done. UV cache makes creation fast (~1-2s).

**Rejected**: Wasteful for repeated scans in the same session. Named sessions for VS Code extension integration require persistence.

### Alternative 3: External Cache Manager (Redis/SQLite)

Use an external store to coordinate venv access across processes.

**Rejected**: Over-engineered. File locks are sufficient for the single-host, multi-process case. No multi-host coordination needed.

## Consequences

### Positive

- **Concurrency safe**: File locks prevent race conditions during venv creation
- **Backward compatible**: Default behavior (no `--session` flag) creates ephemeral venvs, matching previous semantics
- **Future-ready**: Named sessions enable VS Code extension integration and AI escalation workflows where venvs need to persist for collection docstring queries
- **Observable**: `session list/info/delete/reap` CLI subcommands provide visibility into venv lifecycle
- **Atomic metadata**: `os.replace()` for metadata writes prevents corruption

### Negative

- **Disk usage**: Named sessions persist until TTL expiry or explicit deletion
- **fcntl dependency**: Linux/macOS only (not Windows), acceptable for APME's container-first deployment
- **Module-level singleton**: `_manager` in `_venv.py` means TTL is set on first use; subsequent calls with different TTL are ignored

## References

- `src/apme_engine/collection_cache/venv_session.py` — Implementation
- `src/apme_engine/validators/ansible/_venv.py` — Integration layer
- `tests/test_venv_session.py` — 20 tests covering all lifecycle operations
