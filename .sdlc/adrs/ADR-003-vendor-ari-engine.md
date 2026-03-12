# ADR-003: Vendor the ARI Engine, Do Not Use as Dependency

## Status

Accepted

## Date

2026-02

## Context

The engine that parses Ansible content (playbooks, roles, collections, task files) and builds the in-memory model originates from ARI (Ansible Risk Insights). We needed to decide how to consume it.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| pip dependency | Clean separation, upstream updates | No control over parser behavior, version drift, API instability |
| Vendored (copied in, read-only) | Full control, single repo | Must maintain fork |
| Subprocess | No code coupling | Double parsing, IPC overhead, fragile |
| Full integration | Native, tested as one unit | Maintenance burden |

## Decision

**Full integration.**

The ARI engine code was brought into `src/apme_engine/engine/` and is maintained as part of the project. It is:
- Not vendored read-only
- Not a pip dependency
- Not invoked via subprocess

## Rationale

- The engine is the critical data path — its output (hierarchy payload, scandata) feeds every validator
- We need to modify the parser, annotators, and hierarchy builder to suit our payload shape
- Single parse, single model: all validators reuse the same engine output with no version drift
- The original ARI rules become one validator ("native") among many, not the engine itself

> "We need to vendor the code, bring it into this project." — user decision

## Consequences

### Positive
- Full control over parsing behavior
- Single source of truth for the model
- No external version drift
- Can optimize and extend freely

### Negative
- Maintenance burden for engine code
- Must manually port upstream improvements
- Larger codebase

## Implementation Notes

- Engine code: `src/apme_engine/engine/`
- Hierarchy payload is the standard interchange format
- All validators consume the same parsed model

## Supersedes

- Original planning ADR about ARI wrapper approach

## Related Decisions

- ADR-002: OPA/Rego rules consume hierarchy payload
- ADR-009: Remediation engine operates on parsed model
