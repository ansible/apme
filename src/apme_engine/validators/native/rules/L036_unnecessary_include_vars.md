---
rule_id: L036
validator: native
description: include_vars without when/tags.
---

## Unnecessary include_vars (L036)

include_vars without when/tags.

### Example: violation

```yaml
- name: Load vars
  ansible.builtin.include_vars:
    file: vars.yml
```

### Example: pass

```yaml
- name: Load vars conditionally
  ansible.builtin.include_vars:
    file: vars.yml
  when: some_condition is defined
```
