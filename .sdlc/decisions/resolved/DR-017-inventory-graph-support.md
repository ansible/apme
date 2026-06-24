# DR-017: Inventory Graph Support

**Status:** Resolved
**Created:** 2026-06-23
**Category:** Architecture
**Priority:** Low
**Blocking:** No

## Context

APME currently has no `NodeType.INVENTORY` or `NodeType.INVENTORY_GROUP` in the ContentGraph. This limits ability to build graph-aware rules for inventory content.

Related RFE: [AAPRFE-2997](https://issues.redhat.com/browse/AAPRFE-2997) — inventory group names with hyphens cause issues with Python variable access in Jinja2 templates.

Current state:
- L074 detects dashes in role names (graph-based)
- No equivalent for inventory group names
- REQ-016 proposes file-based L111 rule as interim solution

## Question

Should APME extend ContentGraph to parse inventory files and create INVENTORY_GROUP nodes?

## Options

### Option A: Add NodeType.INVENTORY_GROUP

**Pros:**
- Consistent with graph-based rule architecture
- Enables relationship tracking (group hierarchies, host membership)
- Future-proofs for inventory-related rules

**Cons:**
- Significant parser work (INI, YAML, dynamic inventory)
- Inventory formats are complex (children, vars, patterns)
- May be overkill if only one rule needs it

### Option B: File-Based Rules Only

**Pros:**
- Simpler implementation
- L111 can ship faster
- No graph schema changes

**Cons:**
- Inconsistent with graph-first direction
- Limited cross-reference capability

### Option C: Hybrid (File now, Graph later)

**Pros:**
- Ship L111 immediately (REQ-016)
- Migrate to graph when more inventory rules justified

**Cons:**
- Two implementations to maintain temporarily

## Recommendation

Option C — ship file-based L111 per REQ-016, revisit graph support when additional inventory rules are needed.

## Decision

**Option C accepted** — L111 shipped as file-based rule. Graph support deferred until additional inventory rules justify the investment.

Implemented: 2026-06-24

## References

- [AAPRFE-2997](https://issues.redhat.com/browse/AAPRFE-2997) — Inventory group hyphen normalization
- REQ-016 — L111 file-based rule specification
- ADR-008 — Rule ID conventions
