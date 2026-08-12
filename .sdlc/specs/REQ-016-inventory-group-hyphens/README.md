# REQ-016: Inventory Group Hyphen Detection (L111)

**Status:** Implemented
**Created:** 2026-06-23
**Priority:** Medium
**Phase:** PHASE-001
**RFE:** [AAPRFE-2997](https://issues.redhat.com/browse/AAPRFE-2997)

## Summary

Add rule L111 to detect inventory group names containing hyphens. Group names with hyphens cause issues when accessed as Python variables in Jinja2 templates (e.g., `groups['web-servers']` works but `hostvars[groups.web-servers]` fails).

## Background

Ansible inventory group names become Python identifiers in various contexts:
- `groups.<groupname>` magic variable access
- Jinja2 dot notation: `{{ groups.web-servers }}` fails
- Group variable files: `group_vars/web-servers.yml` works but access is inconsistent

Recommendation: use underscores (`web_servers`) instead of hyphens (`web-servers`).

## Requirements

### Functional

1. **L111 Rule**: Detect inventory group names containing hyphens
2. **File Types**: Scan INI and YAML inventory files
3. **Detection Points**:
   - INI: `[group-name]`, `[group-name:children]`, `[group-name:vars]`
   - YAML: Top-level keys under `all.children`, nested `children` keys
4. **Exclusions**: Skip `all`, `ungrouped` (reserved names)
5. **Message**: Clear explanation of Jinja2 access issues

### Non-Functional

1. Severity: LOW (style/compatibility, not breaking)
2. Tags: `[style, inventory]`
3. Scope: INVENTORY (file-based, no graph node yet — see DR-017)

## Acceptance Criteria

- [x] L111 detects `[web-servers]` in INI inventory
- [x] L111 detects `web-servers:` under `children:` in YAML inventory
- [x] L111 ignores `all` and `ungrouped`
- [x] L111 reports file path and line number
- [x] Unit tests cover INI and YAML formats
- [x] `tox -e lint` passes
- [x] `tox -e unit` passes with coverage

## Technical Notes

- Pattern similar to L074 (role name hyphens)
- File-based implementation initially (no graph node)
- Future: migrate to graph-based when NodeType.INVENTORY_GROUP exists (DR-017)

## References

- [AAPRFE-2997](https://issues.redhat.com/browse/AAPRFE-2997) — Customer RFE
- DR-017 — Inventory graph support (deferred)
- L074 — Role name hyphen detection (reference implementation)
- ADR-008 — Rule ID conventions
