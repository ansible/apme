# Phase 1: CycloneDX Data Model & PURL Strategy - Context

**Gathered:** 2026-03-25
**Status:** Ready for planning

<domain>
## Phase Boundary

Define stdlib-only Python dataclasses for CycloneDX 1.5 BOM and Component structures, PURL generation functions for Ansible collections, roles, and Python packages, and a component validation function with reporting. This phase produces the data model only — no inventory collection, serialization, or CLI integration.

</domain>

<decisions>
## Implementation Decisions

### Incomplete Metadata Handling
- Unversioned components use `"unversioned"` as the version sentinel (not `"0.0.0"`)
- Missing supplier/author fields use `"unknown"` placeholder to maintain NTIA minimum element compliance
- Bare roles (no `meta/main.yml`, only `tasks/main.yml`) are included with best-effort metadata — name derived from directory, everything else unknown/unversioned
- Philosophy: maximum inventory visibility — never silently drop a component

### PURL Formatting
- Collections use dot-joined name in PURL: `pkg:generic/cisco.ios@2.0` (not `pkg:generic/cisco/ios@2.0`)
- All collection PURLs include `repository_url=https://galaxy.ansible.com` qualifier, regardless of actual source
- Role names derived from `galaxy_info.role_name` in `meta/main.yml` when available, fall back to directory name
- Fallback-derived names must be marked/filterable on the component (e.g., a property or annotation) so users can distinguish authoritative vs inferred names
- Python package PURLs use PEP 503 normalized names: `pkg:pypi/ruamel-yaml@0.18.0` (lowercased, hyphens only)

### Validation Behavior
- Collect all validation errors — do not fail on first error
- Invalid components are still included in the SBOM with best-effort placeholders (not excluded)
- Validation report surfaced via a separate `--validate` flag (not emitted by default)
- Report includes: what failed, why it failed, and a suggestion for how to fix
- Report format: human-readable table by default, `--json` flag for automation (matches existing apme-scan output patterns)

### Claude's Discretion
- PURL version handling for unversioned components (omit vs sentinel in PURL itself)
- Exact dataclass field names and internal structure
- Validation error severity levels (warning vs error classification)
- How to annotate fallback-derived role names on the component (property, external reference, or custom field)

</decisions>

<specifics>
## Specific Ideas

- Validation report should match the style of existing `apme-scan` output (table with columns) for consistency
- Fallback role names need to be easily filterable so users can audit which roles need better metadata

</specifics>

<code_context>
## Existing Code Insights

### Reusable Assets
- `@dataclass` pattern with `field(default_factory=...)` used extensively (e.g., `src/apme_engine/engine/models.py`)
- Google-style docstrings enforced by pydoclint pre-commit hook
- `from __future__ import annotations` required in all modules

### Established Patterns
- Strict mypy (`disallow_any_explicit = true`) — all types must be explicit
- Ruff linting with 120-char line length
- Enum classes use `str, Enum` base (e.g., `RuleScope(str, Enum)`)
- Module-level logger: `logger = logging.getLogger(__name__)`

### Integration Points
- New SBOM module would live under `src/apme_engine/` (e.g., `src/apme_engine/sbom/`)
- No external dependencies allowed — stdlib only per ADR-014
- Existing `src/apme_engine/engine/models.py` provides pattern reference for data modeling

</code_context>

<deferred>
## Deferred Ideas

- `--validate` flag and validation report are Phase 4 (CLI integration) scope, but the validation function itself is Phase 1
- Validation report JSON format details deferred to Phase 4

</deferred>

---

*Feature: REQ-003-security-compliance / SBOM Generation*
*Context gathered: 2026-03-25*
