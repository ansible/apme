# ADR-059: Extract Shared Graph Analysis Library

## Status

Accepted

## Date

2026-07-08

## Context

The native validator and engine share code through direct imports across
`apme_engine.engine` and `apme_engine.validators.native.rules`. During
remediation, Primary bypasses the native validator gRPC boundary and runs
graph rules in-process. This coupling is invisible in the package structure,
prevents independent performance measurement, and maintains two execution
paths for the same analysis code.

### Forces

- The native validator imports `ContentGraph`, `ContentNode`, enums, and types
  directly from `apme_engine.engine.content_graph` and `apme_engine.engine.models`.
- Graph rules live in `validators/native/rules/` but operate on engine data
  structures, not validator-specific abstractions.
- `content_graph.py` is 3,270 lines mixing the graph data structure with the
  `GraphBuilder` that converts ARI model objects to graph nodes.
- `models.py` is 5,198 lines with shared type definitions interleaved with
  ARI-specific model classes.
- The `graph_scanner`, `variable_provenance`, and `graph_rule_base` modules
  form a coherent analysis layer but are scattered across `engine/` and
  `validators/native/rules/`.

### What problem are we solving?

1. No enforceable boundary between the ARI parsing pipeline (engine) and the
   graph analysis layer (shared by engine and native validator).
2. Impossible to independently measure native validator performance when the
   same code runs both in-process (Primary remediation) and via gRPC.
3. The package structure does not reflect the logical architecture.

## Decision

**We will extract the shared graph analysis code into `apme_engine/graph/` as
an explicit subpackage.**

### What moves to `graph/`

| Module | Source | Destination |
|--------|--------|-------------|
| Shared types | `engine/models.py` (type aliases, enums) | `graph/types.py` |
| Severity | `severity_defaults.py` | `graph/severity.py` |
| ContentGraph data structure | `engine/content_graph.py` (graph + node classes) | `graph/content_graph.py` |
| Graph scanner | `engine/graph_scanner.py` | `graph/scanner.py` |
| Variable provenance | `engine/variable_provenance.py` | `graph/variable_provenance.py` |
| Rule base class | `validators/native/rules/graph_rule_base.py` | `graph/rule_base.py` |
| Class loader | `engine/utils.py::load_classes_in_dir` | `graph/_loader.py` |
| Graph rules (~90 files) | `validators/native/rules/` | `graph/rules/` |

### What stays in `engine/`

- `GraphBuilder` (extracted from `content_graph.py` into `engine/graph_builder.py`)
- All ARI model classes in `models.py`
- `graph_opa_payload.py` (OPA-specific transport serialization)
- Everything else in `engine/` unrelated to graph analysis

### Boundary rules

1. `graph/` **must not** import from `engine/` (except via TYPE_CHECKING for
   ARI types in `graph_builder.py`'s inverse direction).
2. `engine/` may import from `graph/`.
3. `validators/` may import from `graph/`.
4. `daemon/` may import from `graph/`.
5. Re-export shims at old paths ensure backward compatibility during migration.

### Dependency direction

```
graph/ (shared library, no engine deps)
  ↑         ↑
engine/   validators/native/
  ↑         ↑
daemon/ (orchestration)
```

## Alternatives Considered

### Alternative 1: Separate PyPI package

**Description**: Extract into a standalone `apme-graph` package.

**Pros**: Strongest boundary enforcement via packaging.

**Cons**: Unnecessary complexity in a monorepo where all containers install the
full package. Adds release coordination overhead.

**Why not chosen**: The monorepo already has a single `pyproject.toml`; a
subpackage achieves boundary isolation without distribution overhead.

### Alternative 2: Leave as-is, enforce via convention

**Description**: Document the boundary but don't move files.

**Pros**: Zero code changes.

**Cons**: Conventions erode without enforcement. The scattered file layout
actively confuses new contributors and AI agents. Doesn't enable independent
performance measurement.

**Why not chosen**: The current layout has already caused coupling issues
(Primary running native rules in-process during remediation).

## Consequences

### Positive

- Package structure reflects logical architecture.
- Import boundary is enforceable via lint rules.
- Prerequisite for future work: unifying remediation to use gRPC for native
  rules (separate ADR-level decision).
- `ContentGraph` data structure has no ARI dependency, enabling lighter tests.

### Negative

- Large mechanical diff (~150 files touched for import updates).
- Re-export shims add temporary indirection (removed once all consumers migrate).

### Neutral

- No runtime behavior change — same code, different package paths.
- `models.py` ARI types stay in `engine/`; only ~50 lines of shared type
  definitions move.

## Implementation Notes

- Re-export shims at old paths (`engine/content_graph`, `engine/graph_scanner`,
  `validators/native/rules/graph_rule_base`, `severity_defaults`) ensure
  backward compatibility. Shims are removed in a follow-up cleanup pass.
- `engine/models.py` re-exports all types from `graph/types.py` so existing
  consumers are unaffected.
- PR #354 adds `sensitivity.py`, `audit_metadata.py`, and `_variable_helpers.py`
  which will move to `graph/` when that PR lands. This extraction rebases
  cleanly regardless of merge order.

## Related Decisions

- ADR-001: gRPC for inter-service communication
- ADR-003: Vendored ARI engine
- ADR-009: Validators are read-only; remediation is separate
- ADR-042: Built-in validator bundles are closed
- ADR-043: Severity as IntEnum from severity_defaults
- ADR-044: Transforms and engine state management

## References

- Extraction plan: `.cursor/plans/graph_library_extraction_aae10431.plan.md`
- AGENTS.md architectural invariants 1, 12, 14

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-07-08 | AI Agent | Initial proposal and acceptance |
