# Ansible-Core Migration Rules

This technical guide outlines the specific breaking changes and migration rules required for transitioning to ansible-core versions 2.19 and 2.20. It categorises various updates—ranging from critical data tagging security shifts to the removal of legacy directives like bare includes—and maps them to codified detection rules for automated validation. By detailing how these changes affect playbook logic, variable templating, and privilege escalation, the document provides a clear framework for developers to modernise their automation code. Ultimately, the text serves as a structured implementation roadmap that replaces manual analysis with deterministic linting and AI-assisted fixing to ensure compatibility with the latest Ansible engine.

---

## ansible-core 2.19 / 2.20 Migration Rules

This document catalogues the breaking changes in ansible-core 2.19 and 2.20, maps each to an APME rule (existing or planned), and describes the detection approach.

### Summary

| # | Breaking change | Severity | Rule | Status | Fixer |
|---|-----------------|----------|------|--------|-------|
| 1 | Data tagging — trust model inversion | CRITICAL | M005 | Implemented (native) | Tier 2 (AI) |
| 2 | Jinja in conditionals (`when: "{{ }}"`) | HIGH | L015 | Exists (OPA) | Yes (L015 transform) |
| 3 | Become timeout now unreachable | HIGH | M006 | Implemented (OPA) | Yes (M006 transform) |
| 4 | Jinja2 filter behavior on nested vars | MEDIUM | M007 | Planned (native) | — |
| 5 | AnsibleVaultEncryptedUnicode removed | LOW | — | Not codifiable (plugin code) | — |
| 6 | Bare include: removed | HIGH | M008 | Implemented (OPA) | Yes (M008 transform) |
| 7 | with_* loop deprecation | MEDIUM | M009 | Implemented (OPA) | Yes (M009 transform, simple cases) |
| 8 | Python version requirements | MEDIUM | M010 | Implemented (native) | Tier 3 (manual) |
| 9 | Network collection incompatibilities | MEDIUM | M011 | Implemented (OPA) | Tier 3 (informational) |
| 10 | Error message string parsing | LOW | M012 | Planned (native) | — |
| 11 | DEFAULT_TRANSPORT: smart removed (2.20) | LOW | M013 | Planned (native) | — |
| 12 | Module string options accept None | LOW | L058/L059 | Exists (ansible) | — |

---

## Detailed Breakdown

### 1. Data Tagging — Trust Model Inversion (CRITICAL)

**Change**: The trust model is inverted in 2.19. Previously all strings were implicitly trusted as Jinja2 templates unless wrapped with `!unsafe`. Now only strings from trusted sources (playbook files, role files, vars files) are eligible for template rendering. Strings from module results (registered vars), external sources, and `!unsafe`-marked strings are untrusted and will not be re-templated.

**Impact**: Any playbook that registers a variable and then uses it inside a Jinja2 template expression will behave differently. Conditionals that embed `{{ }}` blocks referencing registered variables may fail with "Conditional is marked as unsafe."

**Detection**: Native rule on scandata. The engine already tracks variable definitions (`register`, `set_fact`) and usages. Detect patterns where a registered variable is referenced inside a `{{ }}` expression in a subsequent task's template context (e.g., `"{{ result.stdout }}"` in a `when`, `template`, `lineinfile`, or `debug msg`).

**Example violation**:

```yaml
- name: Get hostname
  ansible.builtin.command: hostname
  register: result

- name: Show hostname
  ansible.builtin.debug:
    msg: "Hostname is {{ result.stdout }}"  # 2.19: result.stdout is untrusted
```

**Rule**: M005 (implemented, native). **Fixer**: Tier 2 — AI-proposable (requires understanding template intent).

---

### 2. Jinja in Conditionals (HIGH)

**Change**: `when` and `until` should not contain embedded `{{ }}` template expressions.

**Detection**: Already covered by L015 (OPA, no-jinja-when). Detects `{{ }}` inside `when` values.

**Example violation**:

```yaml
- name: Check result
  ansible.builtin.debug:
    msg: ok
  when: "{{ result.rc == 0 }}"    # L015 fires
```

**Fix**: `when: result.rc == 0`

**Rule**: L015 (exists, OPA)

---

### 3. Become Timeout Now Unreachable (HIGH)

**Change**: Timeout waiting for privilege escalation (`become`) is now an `UNREACHABLE` error, not a task error. `ignore_errors: true` will not catch it.

**Detection**: OPA rule. Check for tasks with `become: true` and `ignore_errors: true` but without `ignore_unreachable: true`.

**Example violation**:

```yaml
- name: Risky become task
  ansible.builtin.command: whoami
  become: true
  ignore_errors: true           # M006: won't catch become timeout in 2.19+
```

**Fix**: Add `ignore_unreachable: true` or handle the error differently.

**Rule**: M006 (implemented, OPA). **Fixer**: Deterministic — adds `ignore_unreachable: true`.

---

### 4. Jinja2 Filter Behavior on Nested Vars (MEDIUM)

**Change**: Filters `default`, `mandatory`, `defined`, `undefined` behave differently for nested non-scalars with embedded templates. Nested values are now templated on use (lazy evaluation). `complex_var is defined` passes even if `complex_var.nested` is undefined.

**Detection**: Native rule. Detect `is defined` / `default()` filter usage on variables known to be complex (registered vars, dict/list vars from `set_fact`). Heuristic — flags patterns likely to be affected.

**Example violation**:

```yaml
- name: Check nested
  ansible.builtin.debug:
    msg: "{{ complex_var.nested | default('fallback') }}"
  when: complex_var is defined    # 2.19: passes even if .nested is undefined
```

**Rule**: M007 (planned, native)

---

### 5. AnsibleVaultEncryptedUnicode Removed (LOW)

**Change**: Internal type `AnsibleVaultEncryptedUnicode` replaced by `EncryptedString`. Affects custom action plugins, filter plugins, or Python code referencing this type.

**Detection**: Not codifiable from playbook YAML. Would require inspecting custom plugin Python source code. Out of scope for playbook-level analysis.

**Rule**: None

---

### 6. Bare include: Removed (HIGH)

**Change**: The bare `include:` directive (deprecated since 2.4) is removed.

**Detection**: OPA rule. Check for tasks using `include` as the module name (not `include_tasks` or `import_tasks`).

**Example violation**:

```yaml
- include: tasks/setup.yml       # M008: use include_tasks or import_tasks
```

**Fix**: Use `include_tasks:` (dynamic) or `import_tasks:` (static).

**Rule**: M008 (implemented, OPA). **Fixer**: Deterministic — rewrites to `ansible.builtin.include_tasks`.

---

### 7. with_* Loop Deprecation (MEDIUM)

**Change**: All `with_*` style loops (`with_items`, `with_dict`, `with_fileglob`, etc.) are deprecated in favor of `loop:` + filters.

**Detection**: OPA rule. Check task options keys for any key starting with `with_`.

**Example violation**:

```yaml
- name: Install packages
  ansible.builtin.yum:
    name: "{{ item }}"
    state: present
  with_items:                     # M009: use loop: instead
    - httpd
    - nginx
```

**Fix**: `loop: ["httpd", "nginx"]`

**Rule**: M009 (implemented, OPA). **Fixer**: Deterministic for `with_items`/`with_list`/`with_flattened` → `loop:`. Complex `with_*` forms (`with_dict`, `with_subelements`) are Tier 2 (AI).

---

### 8. Python Version Requirements (MEDIUM)

**Change**: ansible-core 2.18+ requires Python 3.11+ on the control node and Python 3.8+ on target nodes. Python 2.7 is dropped.

**Detection**: Native rule. Check for `ansible_python_interpreter` set to a Python 2.x path (e.g., `/usr/bin/python2`, `/usr/bin/python2.7`) in variables, group_vars, host_vars, or task options.

**Example violation**:

```yaml
vars:
  ansible_python_interpreter: /usr/bin/python2.7   # M010: Python 2 dropped
```

**Rule**: M010 (implemented, native). **Fixer**: Tier 3 — manual (user must choose the correct Python 3 path).

---

### 9. Network Collection Incompatibilities (MEDIUM)

**Change**: `ansible.netcommon` and vendor network collections (EOS, IOS, NXOS, IOS-XR, JunOS) had compatibility issues with 2.19's data tagging.

**Detection**: OPA rule. Check for usage of known affected network modules (`ios_command`, `eos_config`, `nxos_*`, `junos_*`, etc.). Flag as informational — recommend upgrading netcommon and vendor collections alongside core.

**Example violation**:

```yaml
- name: Get config
  cisco.ios.ios_command:          # M011: check collection version for 2.19 compat
    commands: show running-config
```

**Rule**: M011 (implemented, OPA). **Fixer**: Tier 3 — informational (user must upgrade collections).

---

### 10. Error Message String Parsing (LOW)

**Change**: Error messages are more verbose and include operational context. Automation that parses or matches error message strings will break. The `exception` key in task results is renamed to `failed_when_suppressed_exception` when using `failed_when`.

**Detection**: Native rule. Detect `when` or `failed_when` conditions that do string comparison on `result.msg` or `result.exception`.

**Example violation**:

```yaml
- name: Check error
  ansible.builtin.debug:
    msg: ok
  when: "'Permission denied' in result.msg"   # M012: error format changed in 2.19
```

**Rule**: M012 (planned, native)

---

### 11. DEFAULT_TRANSPORT smart Removed — 2.20 (LOW)

**Change**: In 2.20, `DEFAULT_TRANSPORT` no longer supports the `smart` value.

**Detection**: Native rule. Check `ansible.cfg` or task-level `connection: smart` settings.

**Example violation**:

```ini
# ansible.cfg
[defaults]
transport = smart    # M013: 'smart' removed in 2.20
```

**Rule**: M013 (planned, native)

---

### 12. Module String Options Accept None (LOW)

**Change**: Since 2.19.1, module options of type `string` now accept `None` and convert to empty string. Previously `None` caused an error.

**Detection**: Already covered by argspec validation (L058/L059). These rules validate module arguments against the actual argspec, which includes type checking.

**Rule**: L058, L059 (exists, ansible validator)

---

## Implementation Approach

All new M-series rules should be **version-gated**: they only fire when the target `ansible_core_version` is 2.19+ (or 2.20+ for M013). The version is available in the `ValidateRequest` and in `ScanContext`.

| Validator | Rules | Implementation |
|-----------|-------|----------------|
| **OPA** | M006, M008, M009, M011 | Add to the Rego bundle. The target version can be passed as `input.ansible_core_version` and checked in the rule guard. |
| **Native** | M005, M007, M010, M012, M013 | Add to `validators/native/rules/`. Use `context.scandata` for variable tracking and template pattern detection. |

Each rule gets:

- A colocated test (`*_test.py` or `*_test.rego`)
- A `.md` doc with violation/pass examples (per [rule-doc-format.md](rule-doc-format.md))
- An entry in [lint-rule-mapping.md](lint-rule-mapping.md)

---

## Source

This analysis is derived from the ansible-core 2.19 and 2.20 changelogs and migration guides. The original knowledge was captured in 2.20 (a Google Apps Script that used an LLM for migration analysis). This document replaces that approach with deterministic, codified rules.

---

## Related Documents

- [lint-rule-mapping.md](lint-rule-mapping.md) — Rule ID cross-mapping
- [rule-catalog.md](rule-catalog.md) — Complete rule listing with fixer status
- [rule-doc-format.md](rule-doc-format.md) — Documentation format for individual rules
- [ADR-009: Remediation Engine](/.sdlc/adrs/ADR-009-remediation-engine.md) — Tiered fixer architecture
