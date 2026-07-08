---
rule_id: L078
validator: native
description: Use bracket notation for dict key access in Jinja.
scope: task
---

## Bracket notation (L078)

Use bracket notation (`item['key']`) instead of dot notation (`item.key`) for dict access in Jinja.

### Example: violation

```yaml
- name: Show item
  ansible.builtin.debug:
    msg: "{{ item.name }}"
```

### Example: pass

```yaml
- name: Show item
  ansible.builtin.debug:
    msg: "{{ item['name'] }}"
```
