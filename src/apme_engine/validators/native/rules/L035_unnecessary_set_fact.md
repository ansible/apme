---
rule_id: L035
validator: native
description: set_fact with random in args.
scope: task
---

## Unnecessary set_fact (L035)

set_fact with random in args.

### Example: violation

```yaml
- name: Random port
  ansible.builtin.set_fact:
    port: "{{ 10000 | random }}"
```

### Example: pass

```yaml
- name: Fixed value
  ansible.builtin.set_fact:
    my_var: fixed_value
```
