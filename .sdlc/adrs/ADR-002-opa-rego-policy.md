# ADR-002: OPA/Rego for Declarative Policy Rules

## Status

Accepted

## Date

2026-02

## Context

Ansible-lint implements ~100 rules in Python. We needed a strategy for rule implementation that balances expressiveness, maintainability, and extensibility.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| All rules in Python | Full engine access, familiar language | Monolithic, hard to contribute declaratively |
| All rules in Rego | Declarative, portable, data-driven | Cannot access full engine model, limited for complex checks |
| Hybrid: Rego + Python | Best of both worlds | Two rule languages to maintain |

## Decision

**Hybrid approach.**

- **Rego rules**: Rules that operate on the JSON hierarchy payload (structural checks, naming conventions, best-practice patterns) are written in Rego and evaluated by OPA.
- **Python rules**: Rules that require the full in-memory engine model (variable resolution, call-graph traversal, risk annotations) are written in Python as native rules.

## Rationale

- Rego is purpose-built for structural policy checks on JSON — concise and auditable
- OPA's `data.json` mechanism makes rules data-driven (e.g., deprecated module lists, package module sets)
- Rules needing `scandata` (the full `SingleScan` object with trees, contexts, variable tracking) cannot be expressed in Rego — they stay native
- Each OPA rule lives in its own `.rego` file with a colocated `_test.rego`, matching the native rule pattern

## Consequences

### Positive
- Declarative rules are easier to audit and contribute
- Data-driven rules update without code changes
- Clear separation: structural (Rego) vs semantic (Python)

### Negative
- Two rule languages to learn and maintain
- Context switching between Rego and Python debugging

## Implementation Notes

- Rego rules: `rules/opa/*.rego`
- Rego tests: `rules/opa/*_test.rego`
- Data files: `rules/opa/data.json`
- Native rules: `src/apme_engine/rules/`

## Related Decisions

- ADR-008: Rule ID conventions (L/M/R/P)
- ADR-003: Vendored ARI engine (provides scandata)
