# REQ-017: Python Import Deprecation Detection (M031-M034)

**Status:** Draft
**Created:** 2026-07-13
**Priority:** Medium
**Phase:** PHASE-001
**Source:** Ansible 2.23 DWCODE deprecation warnings

## Summary

Add rules M031-M034 to detect deprecated Python imports in Ansible plugin code using regex-based scanning. These are "quick wins" requiring no AST parsing—just import statement pattern matching.

## Background

Ansible 2.23+ emits deprecation warnings (DWCODEs) for several internal imports that will be removed in future versions. Collections shipping plugins with these imports will fail when the imports are removed.

APME currently scans only YAML content. These rules extend scanning to Python files in plugin directories using simple regex patterns, avoiding the complexity of full AST parsing.

## Deprecations Covered

| Rule | DWCODE | Deprecated Import | Replacement |
|------|--------|-------------------|-------------|
| M031 | `import_cache_plugins_base` | `from ansible.plugins.cache.base import ...` | `from ansible.plugins.cache import BaseCacheModule` |
| M032 | `import_ansiblefiltertypeerror` | `from ansible.errors import AnsibleFilterTypeError` | `from ansible.errors import AnsibleTypeError` |
| M033 | `import_ansibleactiondone` | `from ansible.errors import _AnsibleActionDone` | Return directly from action plugins |
| M034 | `compat_importlib_resources` | `from ansible.compat.importlib_resources import ...` | `from importlib.resources import ...` |

## Requirements

### Functional

1. **M031-M034 Rules**: Detect deprecated import patterns in Python files
2. **File Scope**: Scan `*.py` files in:
   - `plugins/` (all subdirectories)
   - `module_utils/`
   - `library/` (legacy module location)
3. **Detection**: Regex match on import statements applied per physical line after comment/string stripping (see below). Module deprecations (M031, M034) match both `import X` and `from X import Y` forms; class deprecations (M032, M033) match `from X import Y` only (no valid plain-import form for a class symbol).
4. **Message**: Include deprecated import and recommended replacement
5. **Line Numbers**: Report exact line of offending import

### Non-Functional

1. **Severity**: HIGH (will break on removal)
2. **Tags**: `[modernization, python, deprecated]`
3. **Scope**: PYTHON_PLUGIN (new scope type)
4. **Performance**: Simple regex, no Python parsing overhead

## Acceptance Criteria

- [ ] M031 detects `from ansible.plugins.cache.base import` and `import ansible.plugins.cache.base`
- [ ] M032 detects `AnsibleFilterTypeError` import
- [ ] M033 detects `_AnsibleActionDone` import
- [ ] M034 detects `from ansible.compat.importlib_resources import` and `import ansible.compat.importlib_resources`
- [ ] Rules ignore non-plugin Python files (tests, scripts)
- [ ] Rules report file path and line number
- [ ] Unit tests cover all 4 patterns (positive and negative cases per table below)
- [ ] `tox -e lint` passes
- [ ] `tox -e unit` passes with coverage

## Technical Notes

### Implementation Approach

Native validator extension—no new validator service needed:

```python
# Regex patterns — applied per line with re.MULTILINE after preprocessing.
# Module deprecations (M031, M034) match both import forms; class deprecations
# (M032, M033) appear only as `from X import Y`.
#
# Preprocessing (before matching):
#   1. Split source into physical lines.
#   2. Strip trailing `# ...` comments (not inside string literals).
#   3. Skip lines that are only whitespace or comments.
#   4. Match patterns anchored to line start (^\s*).
#
# Module patterns use (?![.\w]) after the final segment to reject longer names
# (e.g. `ansible.plugins.cache.base_extra`). Class patterns use \b word
# boundaries to reject suffixed names (e.g. `AnsibleFilterTypeErrorExtra`).
# Plain-import patterns accept comma-separated and `as alias` forms.

_MODULE_SUFFIX = r"(?![.\w])"  # reject longer dotted module names
_IMPORT_END = r"(?:\s*[,;]|\s+as\s+\w+|\s*$)"  # comma, semicolon, as-alias, EOL

DEPRECATED_IMPORTS = {
    "M031": [
        rf"^\s*from\s+ansible\.plugins\.cache\.base\s+import\b",
        rf"^\s*import\s+ansible\.plugins\.cache\.base{_MODULE_SUFFIX}{_IMPORT_END}",
    ],
    "M032": [
        r"^\s*from\s+ansible\.errors\s+import\s+.*\bAnsibleFilterTypeError\b",
    ],
    "M033": [
        r"^\s*from\s+ansible\.errors\s+import\s+.*\b_AnsibleActionDone\b",
    ],
    "M034": [
        rf"^\s*from\s+ansible\.compat\.importlib_resources\s+import\b",
        rf"^\s*import\s+ansible\.compat\.importlib_resources{_MODULE_SUFFIX}{_IMPORT_END}",
    ],
}
```

### Pattern test matrix

| Rule | Positive (must match) | Negative (must not match) |
|------|----------------------|---------------------------|
| M031 | `from ansible.plugins.cache.base import BaseCache` | `from ansible.plugins.cache.base_extra import X` |
| M031 | `import ansible.plugins.cache.base` | `# from ansible.plugins.cache.base import X` (comment) |
| M031 | `import ansible.plugins.cache.base, other` | `"import ansible.plugins.cache.base"` (string literal) |
| M031 | `import ansible.plugins.cache.base as cache` | |
| M032 | `from ansible.errors import AnsibleFilterTypeError` | `from ansible.errors import AnsibleFilterTypeErrorExtra` |
| M033 | `from ansible.errors import _AnsibleActionDone` | `from ansible.errors import _AnsibleActionDoneExtra` |
| M034 | `import ansible.compat.importlib_resources, foo` | `import ansible.compat.importlib_resources_v2` |

### File Discovery

Leverage existing ContentGraph file enumeration or add Python file discovery:

```python
PLUGIN_DIRS = ["plugins", "module_utils", "library"]
```

### Integration with Existing Rules

- Follows ADR-008 rule ID convention (M = Modernization)
- Similar pattern to SEC rules scanning non-YAML content
- Results flow through standard violation pipeline

## Out of Scope

- Full Python AST parsing (see REQ-018)
- Method call detection (requires AST)
- Attribute access detection (requires AST)

## References

- Ansible deprecation warnings: `lib/ansible/utils/display.py`
- ADR-008 — Rule ID conventions
- REQ-018 — Python AST validator (full parsing, future)
