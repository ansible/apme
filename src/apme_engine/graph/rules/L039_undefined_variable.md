---
rule_id: L039
validator: native
description: Variable use may be undefined.
scope: inventory
---

## Undefined variable (L039)

Variable use may be undefined.

### Example: violation

```yaml
- name: Use undefined var
  ansible.builtin.debug:
    msg: "{{ never_defined_var_xyz }}"
```

### Example: pass

```yaml
- name: Test play
  hosts: localhost
  vars:
    my_var: value
  tasks:
    - name: Use defined var
      ansible.builtin.debug:
        msg: "{{ my_var }}"
```
