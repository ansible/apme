---
rule_id: L050
validator: native
description: Variable names: lowercase, underscores.
scope: inventory
---

## Var naming (L050)

Variable names: lowercase, underscores.

### Example: violation

```yaml
- name: Use variable
  ansible.builtin.debug:
    msg: "{{ MyVariable }}"
```

### Example: pass

```yaml
- name: Use variable
  ansible.builtin.debug:
    msg: "{{ my_variable }}"
```
