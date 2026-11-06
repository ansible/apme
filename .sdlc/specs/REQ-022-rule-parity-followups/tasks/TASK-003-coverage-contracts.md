# TASK-003: Correct Rule Contracts and Record Deferred Gaps

## Parent Requirement

REQ-022: Close Rule Parity and Risk Assessment Gaps

## Status

Complete

## Description

Align argspec, metadata, ARI, and coverage documentation with source behavior,
and identify broad schema/YAML/sanity gaps as explicitly deferred.

## Prerequisites

- None

## Implementation Notes

1. Correct P001–P004 delegation claims and L058/L059 validation scope.
2. Describe role metadata and collection metadata checks separately.
3. Record unsupported schema, YAML formatting, and sanity-ignore-file scopes as
   deferred without claiming parity.
4. Correct cross-mapping rule enablement notes and regenerate the rule catalog
   only if source rule metadata changes.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/apme_engine/graph/rules/P001_*.md`–`P004_*.md` | Modify | Correct delegated behavior claims |
| `src/apme_engine/graph/rules/L088_*.md` | Modify | State README existence behavior accurately |
| `docs/rules/` | Modify | Keep coverage and mapping current |

## Verification

- `git diff --check`
- Review every documented mapping against its implementation.

## Acceptance Criteria Reference

- REQ-022: documentation matches implementation and clearly marks deferrals.

## Notes

No tests were added or run per the active task instructions. Documentation was
checked against implementation source, the catalog was regenerated, and
`git diff --check` passed.
