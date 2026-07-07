# ADR-057: Per-Rule Ansible-Core Version Applicability

## Status

Accepted

## Date

2026-07-07

## Context

APME's M-series (Modernize) rules detect ansible-core deprecations and breaking changes. When a user scans with `--ansible-version 2.19`, they expect every violation to be relevant to ansible-core 2.19. Today, only M001-M004 deliver on that expectation — they introspect the session venv's actual ansible-core via `find_plugin_with_context()`, so results are inherently correct for the requested version.

All other M rules (M005-M030) are static pattern checks implemented in native Python (`GraphRule` subclasses) or OPA Rego policies. They fire unconditionally regardless of the target version. A user scanning for 2.19 sees:

- M022: "tree/oneline callback plugins removed in **2.23**"
- M014: "top-level fact variables removed in **2.24**"
- M018: "removed in **2.21**"

These violations are irrelevant noise for someone targeting 2.19. The user has no way to distinguish "affects my version" from "affects a future version" because no machine-readable version metadata exists on rules. Version context is scattered across unstructured markdown prose (`**Removal version**: 2.23` in sidecar `.md` files) and violation message strings.

### Existing version metadata

| Location | Version info | Machine-readable? |
|----------|-------------|-------------------|
| Rule `.md` body | `**Removal version**: 2.23` | No — prose only |
| Rule `description` field | "…removed in 2.24" | No — embedded in string |
| OPA Rego messages | "…deprecated in 2.23" | No — violation text |
| `RuleMetadata.version` | `"v0.0.2"` | Yes, but means **rule implementation version**, not ansible-core |
| `ValidateRequest.ansible_core_version` | Target version string | Yes — selects venv, not used for rule filtering |

### Related decisions

- [ADR-043](ADR-043-default-severity-assignment.md) established the pattern of a central `severity_defaults.py` table as single source of truth for per-rule metadata.
- [ADR-026](ADR-026-rule-scope-metadata.md) added `scope` as first-class rule metadata.
- [ADR-041](ADR-041-rule-catalog-override-architecture.md) defined `RuleDefinition` proto for catalog registration.
- [DR-001](../decisions/closed/decided/DR-001-version-specific-analysis.md) decided on version-specific scanning (phased: default latest → `--target-version` → matrix).

## Decision

**Add a PEP 440 version specifier to each rule's metadata, stored in a central table, surfaced on the `RuleDefinition` proto and `Violation.metadata` map. The engine always reports all violations; filtering is a UI concern.**

### 1. Version specifier format: PEP 440

Each version-sensitive rule carries an `ansible_core_version` field containing a [PEP 440 version specifier](https://peps.python.org/pep-0440/#version-specifiers) string. The `packaging` library (`packaging>=23`, already a core dependency) provides `SpecifierSet` for validation and matching.

Examples:

| Rule | Specifier | Meaning |
|------|-----------|---------|
| M008 | `>=2.19` | Bare include removed in 2.19+ |
| M022 | `>=2.23` | Callback plugins removed in 2.23+ |
| M014 | `>=2.24` | Fact variables removed in 2.24+ |

PEP 440 gives us:

- **Standard syntax** — same as `requires-python` in `pyproject.toml` and pip dependency strings
- **Programmatic matching** — `SpecifierSet(">=2.19").contains(Version("2.20"))` → `True`
- **Validation at load time** — malformed specifiers raise `InvalidSpecifier`
- **Complex ranges** — `>=2.19,!=2.21` or `>=2.23,<3.0` if needed

### 2. Central table: `version_defaults.py`

Following the ADR-043 pattern for severity, a static `VERSION_DEFAULTS` dict in `src/apme_engine/version_defaults.py` maps `rule_id → SpecifierSet`. This is the single source of truth — individual rule classes, Rego policies, and frontmatter reference it, not the reverse.

### 3. Proto: `RuleDefinition.ansible_core_version`

The `RuleDefinition` message in `reporting.proto` (ADR-041) gains one field:

```protobuf
message RuleDefinition {
  // ... existing fields 1-7 ...
  string ansible_core_version = 8;  // PEP 440 specifier, e.g. ">=2.19"
}
```

Primary populates this during `RegisterRules`. The Gateway exposes it via the rule catalog REST API.

### 4. Violation metadata: `ansible_core_version` key

During violation conversion (`violation_convert.py`), each violation whose rule has version metadata gets `ansible_core_version` injected into the `Violation.metadata` map. This allows the UI to display version context per-violation without a separate catalog lookup.

### 5. Filtering is a UI concern

The engine always fires all rules and reports all violations with version metadata attached. The UI provides version-scoped views:

- **Scanned version** — show violations matching the scan's target version
- **Upcoming changes** — show violations for the next version(s) for migration planning
- **All** — unfiltered

This design preserves the "upcoming changes" use case: a user on 2.19 may want to see what 2.20 will require, without the engine suppressing that information.

### 6. M001-M004: inherently version-correct

M001-M004 (Ansible validator) introspect the venv's ansible-core runtime, so their results are already correct for the requested version. They receive version metadata in the table for catalog completeness, but their per-finding version relevance is determined by the module's own `runtime.yml` deprecation data, not by a static specifier.

### 7. Non-M rules

Rules without version sensitivity (L, R, P, SEC, A categories) get an empty `ansible_core_version` string. The field is optional and only meaningful for version-sensitive rules. If version-sensitive L or A rules are added in the future, they can use the same field.

## Alternatives Considered

### Alternative 1: Engine-side suppression

**Description**: The engine filters out violations that don't match the target version before returning results.

**Pros**:
- Users only see relevant violations — zero noise
- Simpler UI (no filtering needed)

**Cons**:
- Removes the "upcoming changes" use case entirely — users can't plan ahead
- Violates the engine's current design: validators report everything, consumers decide what to show
- Users who want the full picture must scan multiple times with different versions

**Why not chosen**: Suppression discards useful information. The UI can default to showing only the scanned version while offering "upcoming" and "all" views.

### Alternative 2: Per-rule-class version fields

**Description**: Add `ansible_core_version` directly to `GraphRule` base class, Rego rule metadata, and Ansible rule functions.

**Pros**:
- Version data lives with the rule definition
- No central table to maintain

**Cons**:
- Scattered across three validator types with different implementation patterns
- Native rules use Python dataclass fields, OPA uses Rego annotations, Ansible uses function metadata — no unified access pattern
- Hard to audit: "which rules apply to 2.23?" requires searching three codebases
- Frontmatter already exists alongside the code but isn't machine-read for version data

**Why not chosen**: The ADR-043 pattern (central table) proved effective for severity. Version data has the same characteristics: cross-validator, auditable, rarely changes.

### Alternative 3: Custom version syntax

**Description**: Define a project-specific version format (e.g., `"2.19+"`, `"2.20-2.24"`).

**Pros**:
- Could be simpler to read for non-Python audiences

**Cons**:
- Requires custom parsing logic
- No ecosystem library support
- `packaging` already supports PEP 440 and is already a dependency
- Developers must learn a non-standard format

**Why not chosen**: PEP 440 is standard, well-understood, and already available.

## Consequences

### Positive

- **Correct user experience** — violations carry the version context needed for the UI to show only what's relevant to the scan target
- **Migration planning** — users can preview upcoming version changes without scanning multiple times
- **Auditable** — `version_defaults.py` shows every rule's version applicability in one file
- **Standard format** — PEP 440 specifiers are familiar and tooling-friendly
- **Extensible** — future L or A rules with version sensitivity use the same mechanism
- **Consistent with ADR-043** — follows the established central-table pattern

### Negative

- **Manual data entry** — each M rule's version must be looked up from upstream ansible-core changelogs and deprecation docs
- **Proto regeneration** — adding a field to `RuleDefinition` requires `tox -e grpc` and coordinated deployment

### Neutral

- Engine behavior is unchanged — all rules still fire for every scan
- No changes to `ValidateRequest`, the scanning pipeline, or validator code
- `RuleMetadata` and `GraphRule` base classes are not modified (version is looked up centrally)

## Implementation Notes

1. Add `ansible_core_version` field to `RuleDefinition` in `reporting.proto`; regenerate stubs
2. Create `src/apme_engine/version_defaults.py` with `VERSION_DEFAULTS` dict and accessor functions
3. Update `rule_catalog.py` to populate `ansible_core_version` from the table during collection
4. Update `violation_convert.py` to inject `ansible_core_version` into `Violation.metadata`
5. Update M rule `.md` frontmatter with `ansible_core_version` field
6. UI filtering is a follow-up — this ADR covers the data contract only

## Related Decisions

- [ADR-008](ADR-008-rule-id-conventions.md): Rule ID Conventions — M prefix identifies Modernize rules
- [ADR-026](ADR-026-rule-scope-metadata.md): Rule Scope Metadata — scope is an independent per-rule dimension
- [ADR-041](ADR-041-rule-catalog-override-architecture.md): Rule Catalog & Override Architecture — `RuleDefinition` proto being extended
- [ADR-043](ADR-043-default-severity-assignment.md): Default Severity Assignment — establishes the central-table pattern
- [DR-001](../decisions/closed/decided/DR-001-version-specific-analysis.md): Version-Specific Analysis — phased version targeting

## References

- `proto/apme/v1/reporting.proto` — `RuleDefinition` message
- `src/apme_engine/severity_defaults.py` — central severity table (pattern to follow)
- `src/apme_engine/rule_catalog.py` — catalog collection
- `.sdlc/context/ansible-core-migration.md` — M rule version sources
- [PEP 440](https://peps.python.org/pep-0440/) — Version Identification and Dependency Specification

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-07-07 | APME Team | Initial proposal |
