# DR-001: Version-Specific Analysis Behavior

## Status

Open

## Raised By

Team Review — 2026-03-11

## Category

Product

## Priority

High

---

## Question

What does "Version-Specific Analysis" mean in practice? The PRD says "ability to scan against a target version" but doesn't define the behavior.

## Context

The PRD mentions version-specific analysis as a capability, but there's no acceptance criteria defining what this feature actually does. Multiple interpretations exist:

1. A `--target-version 2.16` flag that checks against a specific ansible-core version
2. A matrix scan that tests against multiple versions simultaneously
3. Version-aware deprecation warnings (e.g., "deprecated in 2.14, removed in 2.17")

This affects the Ansible validator design and potentially the dashboard reporting.

## Impact of Not Deciding

- Ansible validator implementation is blocked for version-aware rules (M001-M004)
- Test playbook selection for different ansible-core versions is unclear
- Dashboard cannot show version-specific remediation paths

---

## Options Considered

### Option A: Single Target Version Flag

**Description**: Add `--target-version 2.18` CLI flag. Scanner validates against that specific version's module set, deprecations, and syntax.

**Pros**:
- Simple to implement
- Clear user mental model
- Matches common use case: "I'm migrating to AAP 2.5, which uses 2.18"

**Cons**:
- User must run multiple scans for different targets
- No comparison view

**Effort**: Low

### Option B: Matrix Scan (Multiple Versions)

**Description**: Add `--target-versions 2.16,2.18,2.20` that scans once and reports findings per version. Output grouped by version.

**Pros**:
- Single scan covers migration path
- Good for planning phased upgrades
- Dashboard can show "fixed in 2.18, still broken in 2.20"

**Cons**:
- More complex output
- Longer scan time (need to load multiple version metadata)
- Dashboard design more complex

**Effort**: High

### Option C: Default to Latest with Deprecation Timeline

**Description**: Always scan against latest (2.20), but deprecation messages include version info: "Deprecated in 2.14, removed in 2.17". No explicit version flag.

**Pros**:
- Zero configuration
- Deprecation timeline gives version context
- Simpler implementation

**Cons**:
- Can't check "will my playbook work on 2.16?"
- Assumes user targets latest

**Effort**: Low

---

## Recommendation

Start with **Option A** (single target version flag) for v1. It's low effort, covers the common case, and we can extend to matrix scanning later if users request it.

Default behavior: scan against the version specified in the Ansible validator's venv (currently 2.18/2.19/2.20 based on user's ansible-core version spec).

---

## Related Artifacts

- REQ-001: Scanner Module (needs acceptance criteria update)
- ADR-006: Ephemeral venvs (already supports multiple versions)
- Ansible validator: L057-L059, M001-M004 rules

---

## Discussion Log

| Date | Participant | Input |
|------|-------------|-------|
| 2026-03-11 | Team | Initial question raised during PRD review |

---

## Decision

**Status**: Decided
**Date**: 2026-03-16
**Decided By**: Team

**Decision**: Combined Approach — implement all three options

**Rationale**:
- Default to latest with deprecation timeline for zero-config experience
- Single target version flag for users migrating to specific AAP version
- Matrix scan for planning phased upgrades and comparison views
- Phased implementation: C (default) → A (single) → B (matrix)

**Implementation Plan**:
1. **Phase 1**: Default to latest with deprecation timeline (Option C)
2. **Phase 2**: Add `--target-version 2.18` flag (Option A)
3. **Phase 3**: Add `--target-versions 2.16,2.18,2.20` matrix scan (Option B)

**Action Items**:
- [ ] Implement deprecation timeline in rule messages (M001-M004)
- [ ] Add `--target-version` CLI flag
- [ ] Design matrix scan output format
- [ ] Implement `--target-versions` multi-version scanning
- [ ] Update REQ-001 acceptance criteria
