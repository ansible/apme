---
rule_id: L050
validator: native
description: "Variable names: lowercase, underscores."
scope: task
---

## Var naming (L050)

Variable names defined in `vars:`, `set_fact`, `register`, or role
defaults/vars should use lowercase letters and underscores only.

### Example: violation

```yaml
- name: Set bad variable
  ansible.builtin.set_fact:
    MyVariable: "value"
```

### Example: pass

```yaml
- name: Set good variable
  ansible.builtin.set_fact:
    my_variable: "value"
```
