# ADR-008: Rule ID Conventions (L/M/R/P)

## Status

Accepted

## Date

2026-02

## Context

Rules needed stable, human-readable IDs. The original ansible-lint used kebab-case names (`no-changed-when`, `fqcn`). We needed a convention for our multi-validator system.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| kebab-case (ansible-lint style) | Descriptive | Verbose, not sortable, no category prefix |
| Lxxx / Rxxx / Mxxx / Pxxx | Sortable, categorized, concise | Less self-documenting |

## Decision

**Use prefixed numeric IDs:**

| Prefix | Category | Examples |
|--------|----------|----------|
| **L** | Lint (style, correctness, best practice) | L002–L059 |
| **M** | Modernize (ansible-core migration) | M001–M004 |
| **R** | Risk/security (annotation-based) | R101–R501, R118 |
| **P** | Policy (legacy, requires ansible runtime) | P001–P004 |

## Rationale

- Rule IDs are independent of the validator that implements them — the user sees `L002`, not "the OPA rule that checks FQCN"
- Numeric IDs are sortable and stable across refactors
- The prefix immediately communicates the rule's category
- A cross-mapping document (`LINT_RULE_MAPPING.md`) tracks the correspondence to original ansible-lint rule names

> "I think lint rules should have an Lxxx ID." — user decision

## Consequences

### Positive
- Sortable, stable IDs
- Clear category from prefix
- Validator-agnostic
- Easy to reference in docs and configs

### Negative
- Less self-documenting than kebab-case
- Requires mapping document for ansible-lint users

## Implementation Notes

### Rule Ranges

```
L001-L099: Core lint rules (style, naming, structure)
L100-L199: Reserved for future lint rules

M001-M099: Modernization rules (ansible-core version migration)

R001-R099: Risk annotation rules
R100-R199: Security pattern rules
R500-R599: Secret detection rules (Gitleaks)

P001-P099: Policy rules (require ansible runtime)
```

### Mapping Document

`docs/LINT_RULE_MAPPING.md` maps:
```
L002 (fqcn) ← ansible-lint: fqcn
L003 (no-changed-when) ← ansible-lint: no-changed-when
...
```

## Related Decisions

- ADR-002: OPA/Rego rules (implements L-rules)
- ADR-010: Gitleaks (implements R5xx rules)
