# ADR-008: Rule ID Conventions (L/M/R/P/A)

## Status

Implemented

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
| **L** | Lint (style, correctness, best practice) | L002–L114 (with gaps) |
| **M** | Modernize (ansible-core migration) | M001–M048 (with gaps) |
| **R** | Risk/security and advisory rules | R101–R501 (with gaps) |
| **P** | Policy (legacy, requires ansible runtime) | P001–P004 |
| **A** | AAP-specific (platform compatibility, APIs) | A001–A099 |

Rule ranges are illustrative, not a promise that every ID is implemented.
The current catalog is the source of truth for registered rules and their
validators.

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
L001-L199: Lint rules (style, correctness, naming, structure)

M001-M099: Modernization rules (ansible-core version migration)

R001-R499: Risk/security graph and OPA rules
R501: Advisory collection dependency suggestion
R500, R502-R599: Unassigned; Gitleaks uses SEC:* IDs

P001-P099: Policy rules (require ansible runtime)

A001-A099: AAP-specific rules (platform compatibility, deprecated APIs)
```

### Mapping Document

`docs/rules/LINT_RULE_MAPPING.md` maps:
```
L002 (fqcn) ← ansible-lint: fqcn
L003 (no-changed-when) ← ansible-lint: no-changed-when
...
```

## Amendments

### 2026-05: Added A (AAP-specific) prefix

AAP platform-specific rules warrant their own category to distinguish them from general Ansible lint rules. The A prefix covers:
- AAP API compatibility (deprecated endpoints, version-specific behaviors)
- AAP best practices (named_url vs template IDs, execution environment requirements)
- AAP 2.5+ migration guidance

## Related Decisions

- ADR-002: OPA/Rego rules (implements L-rules)
- ADR-010: Gitleaks (uses SEC:* IDs)
