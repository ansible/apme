# ADR-019: Dependency Governance Policy

## Status

Accepted

## Date

2026-03

## Context

APME currently declares 14 runtime dependencies in `pyproject.toml`. A recent audit found 4 that are unused or build-time-only (`setuptools`, `grpcio-tools`, `gitdb`, `smmap`), 1 (`requests`) that overlaps with an existing dep (`httpx`), and 2 (`tabulate`, `filelock`) that could be replaced by internal code or stdlib.

Every dependency is a maintenance liability: version bumps, security advisories, license reviews, type-stub availability for mypy strict (ADR-018), and transitive-dependency risk. The project needs a clear philosophy for when a third-party package is justified versus when internal code is the better choice.

The guiding principle: for core data-processing problems (parsing, protocols, algorithms), lean on well-maintained libraries. For presentation, CLI formatting, or small utilities, control our own destiny — write it, test it, own it.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| No policy, add deps ad-hoc | Fast to pull things in | Dep list grows unchecked, upgrade churn, install bloat |
| Ban all deps, pure stdlib | Zero external risk | Unrealistic — reimplementing gRPC or YAML parsing is not viable |
| Two-tier governance (this ADR) | Clear criteria, justified core deps, internal code for utilities | Requires discipline on reviews |

## Decision

**Adopt a two-tier dependency governance model with a mandatory review checklist for new runtime dependencies.**

### Tier 1 — Core Infrastructure (approved, keep)

Dependencies that provide complex, domain-specific functionality we could not reasonably replicate in-house. Justified by deep algorithmic complexity or protocol-level interop:

| Package | Stars (as of 2026-03) | Justification |
|---------|----------------------|---------------|
| `grpcio` | ~65k | gRPC protocol implementation |
| `PyYAML` | ~2.6k | YAML 1.1 parser |
| `ruamel.yaml` | ~600 | Round-trip YAML 1.2 with comment preservation |
| `rapidfuzz` | ~2.7k | C-extension fuzzy string matching |
| `httpx` | ~13.5k | Async-capable HTTP client (also covers sync use cases served by `requests`) |
| `jsonpickle` | ~1.3k | Complex object serialization |
| `joblib` | ~3.9k | Parallel execution and caching |

### Tier 2 — Utility / Presentation (prefer internal)

Small, focused functionality where writing and testing our own code gives us full control, eliminates upgrade churn, and keeps the install footprint minimal:

| Package | Stars (as of 2026-03) | Status | Replacement |
|---------|----------------------|--------|-------------|
| `tabulate` | ~2.1k | Replace | Internal ANSI `table()` function |
| `requests` | ~52.5k | Consolidate | Already have `httpx`; migrate the 1 file that uses `requests` |
| `filelock` | ~940 | Evaluate | `fcntl.flock()` — APME targets Linux containers (Podman/Docker); `fcntl` is POSIX-only, so a cross-platform fallback would be needed if non-Linux support is added |

### New Dependency Checklist

Before adding any new runtime dependency, the PR must answer all seven questions:

1. **Complexity** — Does this dependency solve a genuinely hard problem (parsing, protocols, algorithms), or is it a convenience wrapper around something we could write in <200 lines?
2. **Footprint** — What is the install size? Does it pull transitive dependencies?
3. **Maintenance health** — GitHub stars, commit recency, open-issue count, release cadence.
4. **Type coverage** — Does it ship `py.typed` or have a `types-*` stub? Required for mypy strict (ADR-018).
5. **License** — Is it compatible with Apache-2.0?
6. **Overlap** — Does an existing dep or internal module already cover this?
7. **Stdlib alternative** — Can Python stdlib do this, perhaps with a thin wrapper?

If the answer to question 1 is "convenience wrapper," the default answer is **no** — write it internally, test it, and own it.

## Rationale

- Core infrastructure deps (gRPC, YAML parsers, fuzzy matching) would each require thousands of lines to replicate and would be worse than the originals — these are justified
- Presentation and utility deps (`tabulate`, `filelock`) are thin wrappers around simple functionality — owning this code means zero upgrade churn, full test coverage, and no transitive-dependency surprises
- A checklist forces explicit justification before a new dep enters the tree, preventing the slow accumulation that leads to bloated installs
- Type-stub availability is a hard requirement because mypy strict (ADR-018) is enforced on every commit — a dep without stubs creates `ignore_missing_imports` overrides that weaken type safety

## Consequences

### Positive

- Clear, documented criteria prevent ad-hoc dependency accumulation
- Internal code for utilities gives full control over behavior, testing, and compatibility
- Smaller install footprint reduces supply-chain risk and container image size
- Every new dep gets explicit review rather than silent addition

### Negative

- Internal utility code must be written and maintained (mitigated by the <200-line threshold — if it's bigger, a dep is likely justified)
- Existing Tier 2 deps require migration work in separate PRs

### Cleanup Actions (separate PRs)

- Remove `gitdb`, `smmap` from `[project.dependencies]` (unused; leftover from removed GitPython)
- Move `setuptools` to `[build-system] requires` only; move `grpcio-tools` to `[project.optional-dependencies] dev`
- ~~Replace `tabulate` with internal ANSI `table()` when the ANSI abstraction lands~~ **Done**
- Consolidate `requests` into `httpx` (1 file to migrate)
- Evaluate replacing `filelock` with an `fcntl.flock()` wrapper

## Related Decisions

- ADR-014: Ruff linter and prek pre-commit hooks (established quality gates)
- ADR-018: mypy strict mode type checking (requires type stubs for all deps)
