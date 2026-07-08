---
rule_id: L032
validator: native
description: Variable redefinition may cause confusion.
scope: task
---

## Changed data dependence (L032)

Variable redefinition may cause confusion.

### Example: violation

```yaml
- name: Test play
  hosts: localhost
  vars:
    my_var: original
  tasks:
    - name: Override var
      ansible.builtin.set_fact:
        my_var: overridden
```

### Example: pass

```yaml
- name: Test play
  hosts: localhost
  tasks:
    - name: Set var
      ansible.builtin.set_fact:
        new_var: value
```
